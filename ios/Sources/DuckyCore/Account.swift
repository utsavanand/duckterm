import CryptoKit
import Foundation
import Security

/// This iPhone's DuckTerm account session. GitHub's token never reaches the phone;
/// the sign-in service hands back its own session token after the PKCE redeem.
public struct AccountSession: Codable, Equatable {
    public let accountID: String
    public let login: String
    public let deviceID: String
    public let sessionToken: String
    enum CodingKeys: String, CodingKey {
        case login; case accountID = "account_id", deviceID = "device_id", sessionToken = "session_token"
    }
    public init(accountID: String, login: String, deviceID: String, sessionToken: String) {
        self.accountID = accountID; self.login = login; self.deviceID = deviceID; self.sessionToken = sessionToken
    }
}

/// A Mac signed in to the same account, as the sign-in service lists it.
public struct AccountMac: Decodable, Identifiable, Equatable {
    public let id: String
    public let name: String
    /// When the Mac last spoke to the sign-in service, in seconds. Not a live presence signal.
    public let lastSeenAt: Int
    public let hostID: String
    public let certificateSHA256: String
    public let tlsName: String?
    public let url: URL
    enum CodingKeys: String, CodingKey {
        case id, name, url
        case lastSeenAt = "last_seen_at", hostID = "host_id", certificateSHA256 = "certificate_sha256", tlsName = "tls_name"
    }
    public init(id: String, name: String, lastSeenAt: Int, hostID: String, certificateSHA256: String, tlsName: String?, url: URL) {
        self.id = id; self.name = name; self.lastSeenAt = lastSeenAt; self.hostID = hostID
        self.certificateSHA256 = certificateSHA256; self.tlsName = tlsName; self.url = url
    }
    /// The same checks an invitation gets: the phone only ever connects to a private address it can pin.
    public var valid: Bool {
        hostID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil &&
            certificateSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil &&
            !name.isEmpty && name.count <= 80 &&
            Invitation.validEndpoint(url) && LocalEndpoint.validTLSName(tlsName, for: url)
    }
}

public enum AccountPairing {
    /// Four digits both screens show. The sign-in service never sees the phone's token, so a
    /// service that swapped in another Mac's certificate would produce a different code.
    /// Must match duckterm.mobile.account.matching_code.
    public static func matchingCode(token: String, certificateSHA256: String) -> String {
        let digest = Array(SHA256.hash(data: Data("ducky-match:\(certificateSHA256):\(token)".utf8)))
        let value = digest.prefix(4).reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
        return String(format: "%04d", value % 10000)
    }

    public static func validTicket(_ ticket: String) -> Bool {
        ticket.utf8.count <= 2048 && ticket.range(of: "^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$", options: .regularExpression) != nil
    }

    /// RFC 7636 S256: a 43-character verifier from 32 random bytes.
    public struct PKCE: Equatable {
        public let verifier: String
        public var challenge: String { base64URL(Data(SHA256.hash(data: Data(verifier.utf8)))) }
        public init(verifier: String) { self.verifier = verifier }
        public static func make() throws -> PKCE {
            var bytes = [UInt8](repeating: 0, count: 32)
            let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
            guard status == errSecSuccess else { throw DuckyError.credentialStorage(status) }
            return PKCE(verifier: base64URL(Data(bytes)))
        }
    }

    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    /// The sign-in service's own https origin from the app's Info.plist, or nil when this build has none.
    public static func cloudURL(_ value: Any?) -> URL? {
        guard let text = value as? String, let parts = URLComponents(string: text), parts.scheme == "https",
              parts.host?.isEmpty == false, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, parts.path.isEmpty || parts.path == "/" else { return nil }
        var origin = parts
        origin.path = ""
        return origin.url
    }
}

public enum AccountError: LocalizedError, Equatable {
    case signedOut, cancelled, expired, github, service, macUnreachable(String), macRefused(String), macUnsupported(String), busy
    public var errorDescription: String? {
        switch self {
        case .signedOut: return "You’re signed out. Sign in with GitHub again."
        case .cancelled: return nil
        case .expired: return "Sign-in took too long. Try again."
        case .github: return "GitHub sign-in didn’t finish. Try again."
        case .service: return "Can’t reach the Ducky sign-in service. Check your connection and try again."
        case .macUnreachable(let name): return "Can’t reach \(name) — it needs to be on the same network for now."
        case .macRefused(let name): return "\(name) didn’t accept this iPhone. Sign in on your Mac again, then retry."
        case .macUnsupported(let name): return "\(name) needs a newer DuckTerm to accept sign-in. Pair with an invitation link instead."
        case .busy: return "Too many attempts. Wait a minute, then try again."
        }
    }
}

/// The four sign-in service routes the phone uses; AccountClient is the real one.
public protocol AccountTransport: AnyObject {
    func redeem(code: String, verifier: String, name: String) async throws -> AccountSession
    func macs(token: String) async throws -> [AccountMac]
    func ticket(hostID: String, token: String) async throws -> String
    func signOut(token: String) async throws
}

public final class AccountClient: AccountTransport {
    private let base: URL
    private let session: URLSession
    public init(base: URL) {
        self.base = base
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.urlCredentialStorage = nil
        config.timeoutIntervalForRequest = 15
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }
    public func redeem(code: String, verifier: String, name: String) async throws -> AccountSession {
        try await call("POST", "/v1/session/redeem", body: ["code": code, "verifier": verifier, "name": name])
    }
    public func macs(token: String) async throws -> [AccountMac] {
        struct Listing: Decodable { let macs: [AccountMac] }
        let listing: Listing = try await call("GET", "/v1/macs", token: token)
        return listing.macs
    }
    public func ticket(hostID: String, token: String) async throws -> String {
        struct Issued: Decodable { let ticket: String }
        let issued: Issued = try await call("POST", "/v1/tickets", body: ["host_id": hostID], token: token)
        return issued.ticket
    }
    public func signOut(token: String) async throws {
        struct Removed: Decodable { let removed: Bool }
        let _: Removed = try await call("POST", "/v1/session/sign-out", body: [:], token: token)
    }
    private func call<T: Decodable>(_ method: String, _ path: String, body: [String: String]? = nil, token: String? = nil) async throws -> T {
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch is CancellationError { throw CancellationError() }
        catch { throw AccountError.service }
        guard let http = response as? HTTPURLResponse, data.count <= 256 * 1024 else { throw AccountError.service }
        switch http.statusCode {
        case 200: break
        case 401: throw AccountError.signedOut
        case 429: throw AccountError.busy
        case 400 where path == "/v1/session/redeem": throw AccountError.expired
        default: throw AccountError.service
        }
        do { return try JSONDecoder().decode(T.self, from: data) } catch { throw AccountError.service }
    }
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

public protocol AccountStoring {
    func save(_ session: AccountSession) throws
    func load() throws -> AccountSession?
    func forget() throws
}
extension AccountStore: AccountStoring {}

/// GitHub sign-in, the account's Macs, and asking one of them to list this iPhone.
/// Approval stays on the Mac: asking only makes this iPhone wait there with a matching code.
@MainActor
public final class AccountModel: ObservableObject {
    public enum Phase: Equatable { case unavailable, signedOut, signingIn, signedIn }
    @Published public private(set) var phase: Phase
    @Published public private(set) var session: AccountSession?
    @Published public private(set) var macs: [AccountMac]?
    @Published public private(set) var loadingMacs = false
    @Published public private(set) var askingHostID: String?
    @Published public private(set) var error: String?
    public let cloudURL: URL?
    private let store: AccountStoring
    private let transport: AccountTransport?
    private let devices: SessionModel
    private var pkce: AccountPairing.PKCE?

    public init(cloudURL: URL?, devices: SessionModel, store: AccountStoring = AccountStore(),
                makeTransport: (URL) -> AccountTransport = { AccountClient(base: $0) }) {
        self.cloudURL = cloudURL; self.devices = devices; self.store = store
        transport = cloudURL.map(makeTransport)
        phase = cloudURL == nil ? .unavailable : .signedOut
    }

    public func restore() {
        guard phase != .unavailable else { return }
        do { session = try store.load() } catch { self.error = error.localizedDescription }
        phase = session == nil ? .signedOut : .signedIn
    }

    /// The page ASWebAuthenticationSession opens. Only the challenge leaves the phone.
    public func signInURL() throws -> URL {
        guard let cloudURL else { throw AccountError.service }
        let next = try AccountPairing.PKCE.make()
        pkce = next; error = nil; phase = .signingIn
        var parts = URLComponents(url: cloudURL.appendingPathComponent("/v1/auth/github/start"), resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "challenge", value: next.challenge)]
        return parts.url!
    }

    public func cancelSignIn(_ reason: Error? = nil) {
        pkce = nil
        phase = session == nil ? .signedOut : .signedIn
        error = (reason as? AccountError)?.errorDescription
    }

    /// Redeems the ducky://signed-in callback, then lists the Macs and asks the only one straight away.
    public func finishSignIn(_ callback: URL, name: String) async {
        guard phase == .signingIn, let pkce, let transport,
              let parts = URLComponents(url: callback, resolvingAgainstBaseURL: false),
              parts.scheme == "ducky", parts.host == "signed-in" else { cancelSignIn(AccountError.github); return }
        let items = parts.queryItems ?? []
        if let reason = items.first(where: { $0.name == "error" })?.value {
            cancelSignIn(reason == "cancelled" ? AccountError.cancelled : reason == "expired" ? AccountError.expired : AccountError.github)
            return
        }
        guard let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty, code.utf8.count <= 256 else {
            cancelSignIn(AccountError.github); return
        }
        do {
            let signedIn = try await transport.redeem(code: code, verifier: pkce.verifier, name: name)
            try store.save(signedIn)
            self.pkce = nil
            session = signedIn; phase = .signedIn; error = nil
        } catch { cancelSignIn(error); return }
        await loadMacs(name: name, askOnlyMac: true)
    }

    public func loadMacs(name: String, askOnlyMac: Bool = false) async {
        guard let session, let transport, !loadingMacs else { return }
        loadingMacs = true
        defer { loadingMacs = false }
        do {
            let listed = try await transport.macs(token: session.sessionToken).filter(\.valid)
            macs = listed
            error = nil
            if askOnlyMac, listed.count == 1, devices.host == nil { await ask(listed[0], name: name) }
        } catch { fail(error) }
    }

    /// A ticket from the service proves the account to the Mac; the phone's own token
    /// becomes its device credential once the owner allows it there.
    public func ask(_ mac: AccountMac, name: String) async {
        guard let session, let transport, askingHostID == nil, mac.valid else { return }
        askingHostID = mac.hostID; error = nil
        defer { askingHostID = nil }
        do {
            let existing = devices.host
            let reuse = existing?.hostID == mac.hostID && existing?.certificateSHA256 == mac.certificateSHA256
            let paired = PairedHost(mac: mac, token: reuse ? existing!.token : try DuckyClient.newCredential(), login: session.login)
            let ticket = try await transport.ticket(hostID: mac.hostID, token: session.sessionToken)
            guard AccountPairing.validTicket(ticket) else { throw AccountError.service }
            do { try await devices.pair(account: paired, ticket: ticket) }
            catch let failure as URLError where Self.unreachable.contains(failure.code) { throw AccountError.macUnreachable(mac.name) }
            catch DuckyError.unavailable(403) { throw AccountError.macRefused(mac.name) }
            catch DuckyError.unavailable(404) { throw AccountError.macUnsupported(mac.name) }
            catch DuckyError.unavailable(429) { throw AccountError.busy }
        } catch { fail(error) }
    }

    /// Signs this iPhone out of the account. A Mac it already pairs with keeps working until removed there.
    public func signOut() async {
        guard let session else { return }
        do { try store.forget() } catch { self.error = error.localizedDescription; return }
        self.session = nil; macs = nil; phase = .signedOut; error = nil
        // The local copy is gone either way; an unreachable service's session expires unused.
        try? await transport?.signOut(token: session.sessionToken)
    }

    private static let unreachable: Set<URLError.Code> = [.timedOut, .cannotConnectToHost, .networkConnectionLost, .cannotFindHost, .dnsLookupFailed, .notConnectedToInternet]

    private func fail(_ failure: Error) {
        if failure as? AccountError == .signedOut {
            try? store.forget()
            session = nil; macs = nil; phase = .signedOut
        }
        error = (failure as? AccountError)?.errorDescription ?? SessionModel.refreshError(failure)
    }
}
