import CryptoKit
import Foundation
import Security

/// A pinned local host and three fixed protocol routes; no generic command API.
public final class DuckyClient: NSObject, URLSessionDelegate, URLSessionTaskDelegate {
    public let host: PairedHost
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 10
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    public init(host: PairedHost) throws {
        guard Invitation.validEndpoint(host.url),
              host.certificateSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              host.token.range(of: "^[A-Za-z0-9_-]{43,128}$", options: .regularExpression) != nil else { throw DuckyError.invalidInvitation }
        self.host = host
    }
    public static func newCredential() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard status == errSecSuccess else { throw DuckyError.credentialStorage(status) }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    public func claim(_ invitation: Invitation, name: String) async throws -> PairStatus {
        guard invitation.hostID == host.hostID, invitation.url == host.url,
              invitation.certificateSHA256 == host.certificateSHA256,
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
    public func close() { session.invalidateAndCancel() }
    private func request<T: Decodable>(path: String, body: Data? = nil, cursor: String? = nil) async throws -> T {
        var parts = URLComponents(url: host.url, resolvingAgainstBaseURL: false)!
        parts.path = path
        parts.queryItems = cursor.map { [URLQueryItem(name: "cursor", value: $0)] }
        var request = URLRequest(url: parts.url!)
        request.httpMethod = body == nil ? "GET" : "POST"
        request.httpBody = body
        request.setValue("Ducky " + host.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (bytes, response) = try await session.bytes(for: request, delegate: self)
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
    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        urlSession(session, didReceive: challenge, completionHandler: completionHandler)
    }
    public func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              challenge.protectionSpace.host == host.url.host?.trimmingCharacters(in: CharacterSet(charactersIn: "[]")),
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let certificate = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let data = SecCertificateCopyData(certificate) as Data
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard hash == host.certificateSHA256 else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        // Pinning chooses the anchor; it does not skip expiry or hostname validation.
        SecTrustSetAnchorCertificates(trust, [certificate] as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, true)
        SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, challenge.protectionSpace.host as CFString))
        guard SecTrustEvaluateWithError(trust, nil) else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
