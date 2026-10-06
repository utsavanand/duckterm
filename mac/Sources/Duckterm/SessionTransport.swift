import Foundation

/// Delegate-driven collection enforces the limit while bytes arrive, including
/// a dishonest/missing Content-Length. Completion-handler data tasks buffer the
/// entire response before application code can check its size.
final class BoundedSessionHTTP: NSObject, URLSessionDataDelegate {
    private struct Pending {
        var data = Data()
        var response: URLResponse?
        var oversized = false
        let continuation: CheckedContinuation<(Data, URLResponse), Error>
    }
    private let limit = 16 * 1024 * 1024
    private let lock = NSLock()
    private var pending: [Int: Pending] = [:]
    private lazy var session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)

    func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        try await withCheckedThrowingContinuation { continuation in
            lock.lock()
            let task = session.dataTask(with: request)
            pending[task.taskIdentifier] = Pending(continuation: continuation)
            lock.unlock()
            task.resume()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        lock.lock()
        pending[dataTask.taskIdentifier]?.response = response
        let oversized = response.expectedContentLength > limit
        pending[dataTask.taskIdentifier]?.oversized = oversized
        lock.unlock()
        completionHandler(oversized ? .cancel : .allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        let id = dataTask.taskIdentifier
        let oversized = (pending[id]?.data.count ?? 0) + data.count > limit
        if oversized { pending[id]?.oversized = true }
        else { pending[id]?.data.append(data) }
        lock.unlock()
        if oversized { dataTask.cancel() }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let result = pending.removeValue(forKey: task.taskIdentifier)
        lock.unlock()
        guard let result else { return }
        if result.oversized {
            result.continuation.resume(throwing: LaunchDestination.Failure.message("Response too large"))
        } else if let error {
            result.continuation.resume(throwing: error)
        } else if let response = result.response {
            result.continuation.resume(returning: (result.data, response))
        } else {
            result.continuation.resume(throwing: LaunchDestination.Failure.message("Invalid response"))
        }
    }
}

/// Transport for the single local dashboard. The caller selects a configured
/// SSH connection; JavaScript never supplies an upstream URL or credentials.
@MainActor
final class SessionTransport: NSObject, URLSessionTaskDelegate, URLSessionWebSocketDelegate {
    private let http = BoundedSessionHTTP()
    // Long-lived terminals must never occupy the HTTP request pool.
    private lazy var terminalSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpMaximumConnectionsPerHost = 128
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    private var generation = 0
    private var sockets: [String: (host: String, socket: URLSessionWebSocketTask)] = [:]
    var onTerminal: (([String: Any]) -> Void)?
    var onTerminalData: (([String: Any]) async -> Void)?

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
                               willPerformHTTPRedirection response: HTTPURLResponse,
                               newRequest request: URLRequest,
                               completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    nonisolated func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                               didOpenWithProtocol protocol: String?) {
        Task { @MainActor in
            if let id = sockets.first(where: { $0.value.socket === webSocketTask })?.key {
                onTerminal?(["id": id, "opened": true])
            }
        }
    }

    nonisolated static func envelopeLimit(operation: String, params: [String: Any]) -> Int {
        if operation != "session-request" { return 131072 }
        return params["path"] as? String == "/bugreport/submit" && params["method"] as? String == "POST"
            ? 22 * 1024 * 1024 : 14 * 1024 * 1024
    }

    static func responsePayload(data: Data, response: HTTPURLResponse, path: String) -> [String: Any] {
        if path.range(of: #"^/bugreport/bundles/[a-f0-9]{32}$"#, options: .regularExpression) != nil,
           response.statusCode == 200 {
            return ["status": response.statusCode, "base64": data.base64EncodedString(),
                    "contentType": "application/zip"]
        }
        return ["status": response.statusCode, "body": String(decoding: data, as: UTF8.self)]
    }

    static func request(base: URL, params: [String: Any]) throws -> URLRequest {
        guard base.scheme == "http", base.host == "127.0.0.1", base.user == nil, base.password == nil,
              let path = params["path"] as? String, path.utf8.count <= 16384,
              path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains("\\"),
              !path.unicodeScalars.contains(where: { $0.value < 32 }),
              let components = URLComponents(string: path), components.scheme == nil,
              components.host == nil, components.fragment == nil,
              let method = params["method"] as? String,
              ["GET", "POST", "PATCH", "DELETE"].contains(method) else {
            throw LaunchDestination.Failure.message("Invalid session request")
        }
        let route = components.path
        guard !["/sessions/launch", "/sessions/compare", "/sessions/clear-terminated"].contains(route) else {
            throw LaunchDestination.Failure.message("Unsupported session operation")
        }
        let parts = route.split(separator: "/", omittingEmptySubsequences: false)
        guard !parts.contains("."), !parts.contains(".."), !route.contains("\\") else {
            throw LaunchDestination.Failure.message("Invalid session path")
        }
        let reads: Set<String> = ["/sessions", "/session-inbox-counts", "/tree", "/approvals", "/branches", "/file", "/agents-md", "/connectors", "/harnesses"]
        let writes: Set<String> = ["/file", "/agents-md", "/agents-md/suggest", "/paste-image", "/harnesses/register"]
        let pattern = #"^/sessions/[A-Za-z0-9._-]{1,128}(/(events|messages|digest|diff|checkpoints|checkpoint|spotlight|input|message|stop|resume|archive|fork|fork-conversation|promote|inbox|annotations|pins(/[A-Za-z0-9_-]+)?|artifacts(/[A-Za-z0-9_-]+)?|collaboration/(instructions|introduce)))?$"#
        let sessionRoute = route.range(of: pattern, options: .regularExpression) != nil
        let approval = method == "POST" && route.range(of: #"^/approvals/[A-Za-z0-9_-]+/decide$"#, options: .regularExpression) != nil
        let connector = method == "POST" && route.range(of: #"^/connectors/[A-Za-z0-9_-]+/(enable|disable|forget)$"#, options: .regularExpression) != nil
        let harness = route.range(of: #"^/harnesses/[A-Za-z0-9._-]+(/(contents|install|uninstall))?$"#, options: .regularExpression) != nil
        let bugReport = (method == "GET" && route == "/bugreport/context")
            || (method == "POST" && path == "/bugreport/submit")
            || (method == "GET" && path.range(of: #"^/bugreport/bundles/[a-f0-9]{32}$"#, options: .regularExpression) != nil)
        let shellRoute = ["GET", "POST", "DELETE"].contains(method)
            && components.query == nil
            && route.range(of: #"^/sessions/[A-Za-z0-9._-]{1,128}/shell$"#, options: .regularExpression) != nil
        guard shellRoute || bugReport || sessionRoute || approval || connector || harness || (method == "GET" && reads.contains(route)) || (method == "POST" && writes.contains(route)) else {
            throw LaunchDestination.Failure.message("Unsupported session operation")
        }
        var url = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        url.percentEncodedPath = components.percentEncodedPath
        url.percentEncodedQuery = components.percentEncodedQuery
        var request = URLRequest(url: url.url!, cachePolicy: .reloadIgnoringLocalCacheData)
        request.httpMethod = method
        request.timeoutInterval = 30
        if let binary = params["base64"] as? String {
            guard route == "/paste-image", method == "POST", let data = Data(base64Encoded: binary),
                  data.count <= 10_000_000,
                  let type = params["contentType"] as? String,
                  ["image/png", "image/jpeg", "image/webp", "image/gif"].contains(type) else {
                throw LaunchDestination.Failure.message("Invalid clipboard image")
            }
            request.httpBody = data
            request.setValue(type, forHTTPHeaderField: "Content-Type")
        } else if let body = params["body"] as? String {
            let limit = method == "POST" && path == "/bugreport/submit" ? 21 * 1024 * 1024 : 1024 * 1024
            guard body.utf8.count <= limit else { throw LaunchDestination.Failure.message("Request too large") }
            request.httpBody = Data(body.utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    func perform(base: URL, api: LaunchDestination, params: [String: Any]) async throws -> Any {
        var request = try Self.request(base: base, params: params)
        request.setValue(try await api.token(base: base, attempts: 2), forHTTPHeaderField: "X-Duckterm-Token")
        let (data, response) = try await http.perform(request)
        guard let http = response as? HTTPURLResponse, data.count <= 16 * 1024 * 1024 else {
            throw LaunchDestination.Failure.message("Invalid or oversized response")
        }
        return Self.responsePayload(data: data, response: http, path: request.url!.path)
    }

    static func terminalPath(key: String, kind: Any?) throws -> String {
        guard key != ".", key != "..", key.range(of: #"^[A-Za-z0-9._-]{1,128}$"#, options: .regularExpression) != nil,
              kind == nil || (kind as? String).map({ ["agent", "shell"].contains($0) }) == true else {
            throw LaunchDestination.Failure.message("Invalid terminal target")
        }
        return "/sessions/\(key)/" + ((kind as? String) == "shell" ? "shell/terminal" : "terminal")
    }

    func terminal(host: String, base: URL, api: LaunchDestination, operation: String, params: [String: Any]) async throws -> Any {
        guard let id = params["id"] as? String, UUID(uuidString: id) != nil else {
            throw LaunchDestination.Failure.message("Invalid terminal identifier")
        }
        if operation == "terminal-open" {
            guard sockets[id] == nil, sockets.count < 128,
                  let key = params["key"] as? String,
                  key != ".", key != "..",
                  key.range(of: #"^[A-Za-z0-9._-]{1,128}$"#, options: .regularExpression) != nil else {
                throw LaunchDestination.Failure.message("Invalid terminal session")
            }
            let path = try Self.terminalPath(key: key, kind: params["kind"])
            let openingGeneration = generation
            let token = try await api.token(base: base, attempts: 2)
            guard openingGeneration == generation, sockets[id] == nil, sockets.count < 128 else {
                throw LaunchDestination.Failure.message("Terminal opening cancelled")
            }
            var url = URLComponents(url: base, resolvingAgainstBaseURL: false)!
            url.scheme = "ws"
            url.path = path
            var request = URLRequest(url: url.url!)
            request.setValue(token, forHTTPHeaderField: "X-Duckterm-Token")
            request.setValue(base.absoluteString, forHTTPHeaderField: "Origin")
            let socket = terminalSession.webSocketTask(with: request)
            socket.maximumMessageSize = 4 * 1024 * 1024
            sockets[id] = (host, socket)
            socket.resume()
            Task { [weak self] in
                var failure: String?
                do {
                    while self?.sockets[id]?.socket === socket {
                        let message = try await socket.receive()
                        guard self?.sockets[id]?.socket === socket else { break }
                        if case .data(let bytes) = message {
                            // One delivery at a time: WebKit must consume this
                            // frame before another receive can enqueue output.
                            await self?.onTerminalData?(["id": id, "data": bytes.base64EncodedString()])
                        }
                    }
                } catch { failure = error.localizedDescription }
                if self?.sockets[id]?.socket === socket {
                    self?.sockets.removeValue(forKey: id)
                    self?.onTerminal?(["id": id, "closed": true, "error": failure ?? "Terminal disconnected"])
                }
                socket.cancel(with: .goingAway, reason: nil)
            }
            return ["opened": true]
        }
        guard let entry = sockets[id], entry.host == host else {
            if operation == "terminal-close" { return ["closed": true] }
            throw LaunchDestination.Failure.message("Terminal disconnected")
        }
        if operation == "terminal-close" {
            sockets.removeValue(forKey: id)
            entry.socket.cancel(with: .goingAway, reason: nil)
            return ["closed": true]
        }
        if let text = params["text"] as? String, text.utf8.count <= 65536 {
            try await entry.socket.send(.string(text))
        } else if let encoded = params["data"] as? String, let bytes = Data(base64Encoded: encoded), bytes.count <= 65536 {
            try await entry.socket.send(.data(bytes))
        } else { throw LaunchDestination.Failure.message("Invalid terminal input") }
        return ["sent": true]
    }

    func close(host: String? = nil) {
        generation += 1
        for (id, entry) in sockets where host == nil || entry.host == host {
            sockets.removeValue(forKey: id)
            entry.socket.cancel(with: .goingAway, reason: nil)
            onTerminal?(["id": id, "closed": true])
        }
    }
}
