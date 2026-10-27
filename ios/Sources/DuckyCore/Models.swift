import Foundation
import Network

public enum DuckyError: LocalizedError {
    case invalidInvitation, expiredInvitation, invalidResponse, unavailable(Int), credentialStorage(Int32)
    public var errorDescription: String? {
        switch self {
        case .invalidInvitation: return "This Ducky invitation is invalid. Create a new invitation on your Mac."
        case .expiredInvitation: return "This invitation has expired. Create a new invitation on your Mac."
        case .invalidResponse: return "The Mac returned an unsupported response. Your last view may be out of date."
        case .unavailable(let code): return code == 403 ? "Access is unapproved, expired, disabled or revoked. Check Ducky devices on your Mac." : "The Mac is unavailable. Try refreshing."
        case .credentialStorage: return "Ducky could not save its device credential in Keychain."
        }
    }
}

public struct Invitation: Codable, Equatable {
    public let version: Int
    public let hostID: String
    public let invite: String
    public let expiresAt: Int
    public let url: URL
    public let certificateSHA256: String
    enum CodingKeys: String, CodingKey {
        case version, invite, url
        case hostID = "host_id", expiresAt = "expires_at", certificateSHA256 = "certificate_sha256"
    }
    public static func parse(_ text: String, now: Date = Date()) throws -> Invitation {
        guard text.utf8.count <= 4096,
              let link = URLComponents(string: text), link.scheme == "ducky", link.host == "pair",
              link.user == nil, link.password == nil, link.fragment == nil, link.path.isEmpty,
              link.queryItems?.count == 1, link.queryItems?.first?.name == "invite",
              let raw = link.queryItems?.first?.value else { throw DuckyError.invalidInvitation }
        let base64 = raw.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        guard let data = Data(base64Encoded: base64 + String(repeating: "=", count: (4 - base64.count % 4) % 4)),
              let value = try? JSONDecoder().decode(Invitation.self, from: data), value.version == 1,
              value.hostID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              value.invite.range(of: "^[A-Za-z0-9_-]{43,128}$", options: .regularExpression) != nil,
              value.certificateSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              validEndpoint(value.url) else { throw DuckyError.invalidInvitation }
        guard Double(value.expiresAt) > now.timeIntervalSince1970 else { throw DuckyError.expiredInvitation }
        return value
    }
    public static func validEndpoint(_ url: URL) -> Bool {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "https", parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, parts.path.isEmpty || parts.path == "/",
              let port = parts.port, (1024...65535).contains(port), let rawHost = parts.host else { return false }
        let host = rawHost.trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        if let ip = IPv4Address(host) {
            let octets = Array(ip.rawValue)
            return octets[0] == 10 || octets[0] == 127 || (octets[0] == 192 && octets[1] == 168) || (octets[0] == 172 && (16...31).contains(octets[1]))
        }
        if let ip = IPv6Address(host) {
            let octets = Array(ip.rawValue)
            return ip == IPv6Address.loopback || (octets[0] & 0xfe) == 0xfc
        }
        return false
    }
}

public struct PairedHost: Codable, Equatable {
    public let url: URL
    public let hostID: String
    public let certificateSHA256: String
    public let token: String
    public init(invitation: Invitation, token: String) {
        url = invitation.url; hostID = invitation.hostID
        certificateSHA256 = invitation.certificateSHA256; self.token = token
    }
}
public struct PairStatus: Decodable {
    public let id: String
    public let status: String
    public let hostID: String
    enum CodingKeys: String, CodingKey { case id, status; case hostID = "host_id" }
}
public struct AgentStatus: Decodable, Identifiable, Equatable {
    public let id: String
    public let name: String
    public let folder: String
    public let runtime: String
    public let state: String
    public let observedAt: Int64
    public let approvalVisibility: String
    enum CodingKeys: String, CodingKey {
        case id, name, folder, runtime, state
        case observedAt = "observed_at", approvalVisibility = "approval_visibility"
    }
}
public struct Notice: Decodable, Identifiable, Equatable {
    public let id: String
    public let sessionID: String
    public let kind: String
    public let at: Int64
    enum CodingKeys: String, CodingKey { case id, kind, at; case sessionID = "session_id" }
    public var genericText: String {
        kind == "attention" ? "An agent may need your attention. Open Ducky to check its status." : "An agent's turn finished. Open Ducky to check its status."
    }
}
public struct HostSnapshot: Decodable {
    public let version: Int
    public let hostID: String
    public let generatedAt: Int64
    public let readOnly: Bool
    public let sessions: [AgentStatus]
    public let folders: [String]
    public let truncated: Bool
    public let cursor: String?
    public let reset: Bool
    public let notices: [Notice]
    public let notificationDelivery: String
    enum CodingKeys: String, CodingKey {
        case version, sessions, folders, truncated, cursor, reset, notices
        case hostID = "host_id", generatedAt = "generated_at", readOnly = "read_only", notificationDelivery = "notification_delivery"
    }
    public func validate(hostID expected: String) throws {
        guard version == 1, hostID == expected, readOnly,
              sessions.count <= 1000, folders.count <= 1000, notices.count <= 500,
              Set(sessions.map(\.id)).count == sessions.count,
              notices.allSatisfy({ ["attention", "turn_finished"].contains($0.kind) }) else { throw DuckyError.invalidResponse }
    }
}
public struct NoticeTracker {
    private var seen: [String] = []
    public init() {}
    public mutating func newNotices(in snapshot: HostSnapshot) -> [Notice] {
        let known = Set(seen)
        let fresh = snapshot.reset ? [] : snapshot.notices.filter { !known.contains($0.id) }
        seen = Array((seen + snapshot.notices.map(\.id)).suffix(1000))
        return fresh
    }
}
