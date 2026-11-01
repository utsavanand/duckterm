import CryptoKit
import Foundation
import Security

/// A pinned local host and three fixed protocol routes; no generic command API.
public final class DuckyClient {
    public let host: PairedHost
    private let resolver: LocalEndpoint.Resolver
    private let lock = NSLock()
    private var sessions: [UUID: URLSession] = [:]
    private var closed = false

    public convenience init(host: PairedHost) throws {
        try self.init(host: host, resolver: LocalEndpoint.resolve)
    }

    init(host: PairedHost, resolver: @escaping LocalEndpoint.Resolver) throws {
        guard Invitation.validEndpoint(host.url), LocalEndpoint.validTLSName(host.tlsName, for: host.url),
              host.certificateSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              host.token.range(of: "^[A-Za-z0-9_-]{43,128}$", options: .regularExpression) != nil else { throw DuckyError.invalidInvitation }
        self.host = host; self.resolver = resolver
    }
    public static func newCredential() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard status == errSecSuccess else { throw DuckyError.credentialStorage(status) }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    public func claim(_ invitation: Invitation, name: String) async throws -> PairStatus {
        guard invitation.hostID == host.hostID, invitation.url == host.url,
              invitation.certificateSHA256 == host.certificateSHA256, invitation.tlsName == host.tlsName,
              Double(invitation.expiresAt) > Date().timeIntervalSince1970 else { throw DuckyError.invalidInvitation }
        let body = try JSONSerialization.data(withJSONObject: ["invite": invitation.invite, "token": host.token, "name": name])
        let status: PairStatus = try await request(path: "/v1/pair", body: body)
        guard status.hostID == host.hostID else { throw DuckyError.invalidResponse }
        return status
    }
    public func pairingStatus() async throws -> PairStatus {
        let status: PairStatus = try await request(path: "/v1/pair/status")
        guard status.hostID == host.hostID else { throw DuckyError.invalidResponse }
        return status
    }
    public func snapshot(cursor: String?) async throws -> HostSnapshot {
        let result: HostSnapshot = try await request(path: "/v1/snapshot", cursor: cursor)
        try result.validate(hostID: host.hostID)
        return result
    }
    public func close() {
        lock.lock()
        closed = true
        let active = Array(sessions.values)
        sessions.removeAll()
        lock.unlock()
        active.forEach { $0.invalidateAndCancel() }
    }
    private func register(_ session: URLSession, id: UUID) throws {
        lock.lock(); defer { lock.unlock() }
        guard !closed else { throw CancellationError() }
        sessions[id] = session
    }
    private func remove(_ id: UUID) {
        lock.lock(); defer { lock.unlock() }
        sessions.removeValue(forKey: id)
    }
    private func request<T: Decodable>(path: String, body: Data? = nil, cursor: String? = nil) async throws -> T {
        try Task.checkCancellation()
        let addresses = try await LocalEndpoint.addresses(for: host.url, resolver: resolver)
        let deadline = ProcessInfo.processInfo.systemUptime + 10
        for (index, address) in addresses.enumerated() {
            try Task.checkCancellation()
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0 else { throw URLError(.timedOut) }
            do {
                return try await attempt(path: path, body: body, cursor: cursor, address: address,
                                         timeout: remaining)
            } catch let error as URLError where index + 1 < addresses.count &&
                        [.cannotConnectToHost, .notConnectedToInternet].contains(error.code) {
                // Only failures to establish a connection can try another private result.
                // Never replay after TLS failure, timeout, HTTP response or partial delivery.
                continue
            }
        }
        throw URLError(.cannotConnectToHost)
    }
    private func attempt<T: Decodable>(path: String, body: Data?, cursor: String?, address: String, timeout: TimeInterval) async throws -> T {
        var parts = URLComponents(url: host.url, resolvingAgainstBaseURL: false)!
        parts.host = address.contains(":") ? "[\(address)]" : address
        parts.path = path
        parts.queryItems = cursor.map { [URLQueryItem(name: "cursor", value: $0)] }
        guard let url = parts.url, LocalEndpoint.privateLiteral(LocalEndpoint.hostname(url)) != nil else { throw DuckyError.invalidInvitation }
        var request = URLRequest(url: url)
        request.httpMethod = body == nil ? "GET" : "POST"
        request.httpBody = body
        request.setValue("Ducky " + host.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let delegate = PinnedConnection(address: address, port: host.url.port!,
                                        tlsName: host.tlsName ?? LocalEndpoint.hostname(host.url),
                                        certificateSHA256: host.certificateSHA256)
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.urlCredentialStorage = nil
        config.waitsForConnectivity = false
        config.connectionProxyDictionary = ["HTTPEnable": 0, "HTTPSEnable": 0, "SOCKSEnable": 0,
                                           "ProxyAutoConfigEnable": 0, "ProxyAutoDiscoveryEnable": 0]
        config.timeoutIntervalForRequest = min(8, timeout)
        config.timeoutIntervalForResource = timeout
        let session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
        let id = UUID()
        defer { session.invalidateAndCancel(); remove(id) }
        try register(session, id: id)
        let (bytes, response) = try await session.bytes(for: request, delegate: delegate)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse else { throw DuckyError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw DuckyError.unavailable(http.statusCode) }
        var data = Data()
        for try await byte in bytes {
            guard data.count < 2 * 1024 * 1024 else { throw DuckyError.invalidResponse }
            data.append(byte)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

/// One immutable trust decision per literal connection, even if DNS changes or
/// another refresh runs concurrently. No credential is sent before this succeeds.
private final class PinnedConnection: NSObject, URLSessionDelegate, URLSessionTaskDelegate {
    let address: String
    let port: Int
    let tlsName: String
    let certificateSHA256: String
    init(address: String, port: Int, tlsName: String, certificateSHA256: String) {
        self.address = address; self.port = port; self.tlsName = tlsName; self.certificateSHA256 = certificateSHA256
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        urlSession(session, didReceive: challenge, completionHandler: completionHandler)
    }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let challenged = challenge.protectionSpace.host.trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              LocalEndpoint.privateLiteral(challenged) == address, challenge.protectionSpace.port == port,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let certificate = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let data = SecCertificateCopyData(certificate) as Data
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard hash == certificateSHA256 else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        // The leaf pin selects the sole anchor. Expiry and the original invited TLS
        // identity still apply; the current DHCP address is never substituted for it.
        SecTrustSetNetworkFetchAllowed(trust, false)
        SecTrustSetAnchorCertificates(trust, [certificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, true)
        SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, tlsName as CFString))
        guard SecTrustEvaluateWithError(trust, nil) else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
