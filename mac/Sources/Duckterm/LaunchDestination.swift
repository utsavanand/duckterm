import Foundation

/// Narrow API for a launch form on a different computer. No arbitrary URLs,
/// redirects, cookies, or credentials cross the web-view bridge.
final class LaunchDestination: NSObject, URLSessionTaskDelegate {
    private lazy var session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    enum Failure: LocalizedError {
        case message(String)
        var errorDescription: String? {
            if case .message(let text) = self { return text }
            return nil
        }
    }

    static func request(base: URL, operation: String, params: [String: Any]) throws -> URLRequest {
        guard base.scheme == "http", base.host == "127.0.0.1" else {
            throw Failure.message("Invalid destination")
        }
        var paths = ["browse": "/browse", "branches": "/branches", "themes": "/zsh-themes", "launch": "/sessions/launch"]
        let transfers = ["preview", "prepare", "chunk", "begin", "receive", "finish", "clone", "launch", "status", "preflight", "link", "continue"]
        for name in transfers { paths["transfer-" + name] = "/transfers/" + name }
        guard let path = paths[operation] else { throw Failure.message("Unsupported operation") }
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        components.path = path
        components.query = nil
        if operation == "browse" || operation == "branches", let folder = params["path"] as? String {
            components.queryItems = [URLQueryItem(name: "path", value: folder)]
        }
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 30
        if operation.hasPrefix("transfer-") {
            request.timeoutInterval = 240
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject: params)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if operation == "launch" {
            let allowed: Set<String> = ["command", "name", "prompt", "cwd", "repo_path", "branch", "base", "zsh_theme"]
            guard Set(params.keys).isSubset(of: allowed), params.values.allSatisfy({ $0 is String }) else {
                throw Failure.message("Invalid launch parameters")
            }
            var body = params
            body["in_terminal"] = false
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    func perform(base: URL, operation: String, params: [String: Any]) async throws -> Any {
        var request = try Self.request(base: base, operation: operation, params: params)
        // Wait only for readiness. A launch POST is sent once, never retried.
        var token: String?
        for _ in 0..<30 {
            var probe = URLRequest(url: base, cachePolicy: .reloadIgnoringLocalCacheData)
            probe.timeoutInterval = 2
            if let (data, response) = try? await session.data(for: probe),
               let http = response as? HTTPURLResponse, http.statusCode == 200,
               http.value(forHTTPHeaderField: "X-Duckterm") == "1" {
                let html = String(decoding: data, as: UTF8.self)
                let pattern = #"<meta name="duckterm-token" content="([A-Za-z0-9_-]+)">"#
                let regex = try NSRegularExpression(pattern: pattern)
                if let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
                   let range = Range(match.range(at: 1), in: html) {
                    token = String(html[range]); break
                }
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        guard let token else { throw Failure.message("Could not connect to this computer. Check SSH access and try again.") }
        if request.httpMethod == "POST" { request.setValue(token, forHTTPHeaderField: "X-Duckterm-Token") }
        let (data, response): (Data, URLResponse)
        do { (data, response) = try await session.data(for: request) }
        catch {
            if operation == "launch" {
                throw Failure.message("The launch response was lost. Check sessions on this computer before starting another session.")
            }
            throw error
        }
        guard let http = response as? HTTPURLResponse else { throw Failure.message("Invalid server response") }
        let result = try JSONSerialization.jsonObject(with: data)
        guard (200..<300).contains(http.statusCode) else {
            throw Failure.message((result as? [String: Any])?["error"] as? String ?? "Request failed (\(http.statusCode))")
        }
        return result
    }
}
