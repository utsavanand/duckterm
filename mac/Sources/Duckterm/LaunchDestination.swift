import Foundation

/// Narrow API for a launch form on a different computer. No arbitrary URLs,
/// redirects, cookies, or credentials cross the web-view bridge.
final class LaunchDestination: NSObject, URLSessionTaskDelegate {
    private lazy var session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
    private let probes = BoundedSessionHTTP()

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
        var paths = ["browse": "/browse", "branches": "/branches", "themes": "/zsh-themes", "launch": "/sessions/launch", "opencode-options": "/runtimes/opencode/launch-options", "edition": "/edition"]
        if operation == "edition" && !params.isEmpty { throw Failure.message("Invalid edition parameters") }
        if operation == "opencode-options" && !params.isEmpty { throw Failure.message("Invalid OpenCode options parameters") }
        let transfers = ["mkdir", "repositories", "preview", "prepare", "chunk", "begin", "receive", "finish", "clone", "launch", "status", "preflight", "link", "continue"]
        for name in transfers { paths["transfer-" + name] = "/transfers/" + name }
        guard let path = paths[operation] else { throw Failure.message("Unsupported operation") }
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        components.path = path
        components.query = nil
        if operation == "browse" || operation == "branches", let folder = params["path"] as? String {
            components.queryItems = [URLQueryItem(name: "path", value: folder)]
        }
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = operation == "edition" ? 2 : 30
        if operation.hasPrefix("transfer-") {
            request.timeoutInterval = 240
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject: params)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if operation == "launch" {
            let allowed: Set<String> = ["command", "name", "prompt", "cwd", "repo_path", "branch", "base", "zsh_theme", "test"]
            guard Set(params.keys).isSubset(of: allowed), params.allSatisfy({ key, value in key == "test" ? (value as? Bool == true) : value is String }) else {
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

    func token(base: URL, attempts: Int = 30) async throws -> String {
        // Wait only for readiness. A launch POST is sent once, never retried.
        // The locked page answers 401 with the self-probe header.
        var ready = false
        for _ in 0..<attempts {
            var probe = URLRequest(url: base, cachePolicy: .reloadIgnoringLocalCacheData)
            probe.timeoutInterval = 2
            if let (_, response) = try? await probes.perform(probe),
               let http = response as? HTTPURLResponse,
               http.value(forHTTPHeaderField: "X-Duckterm") == "1" {
                ready = true; break
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        guard ready else { throw Failure.message("Could not connect to this computer. Check SSH access and try again.") }
        return try await OwnerToken.token(base: base)
    }

    func perform(base: URL, operation: String, params: [String: Any]) async throws -> Any {
        var request = try Self.request(base: base, operation: operation, params: params)
        request.setValue(try await token(base: base, attempts: operation == "edition" ? 1 : 30), forHTTPHeaderField: "X-Duckterm-Token")
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
