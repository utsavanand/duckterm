import Foundation

public struct ConversationMessage: Decodable, Identifiable, Equatable {
    public let id: String
    public let role: String
    public let text: String
    public let at: Int64?
}

public struct MessageReceipt: Decodable, Identifiable, Equatable {
    public let requestID: String
    public let sessionID: String
    public let status: String
    public let reason: String?
    public let createdAt: Int64
    public let updatedAt: Int64
    public let text: String?
    public let expiresAt: Int64?
    public let waitReason: String?
    public var id: String { requestID }
    public var isPending: Bool { ["queued", "delivering"].contains(status) }
    public var label: String { deliveryLabel() }
    public func deliveryLabel(agentState: String? = nil) -> String {
        switch status {
        case "queued":
            switch waitReason {
            case "agent_busy": return "On your Mac · waiting for this turn to finish"
            case "approval_pending": return "On your Mac · waiting for approval on your Mac"
            case "desktop_input": return "On your Mac · waiting for desktop input to finish"
            case "safe_prompt": return "On your Mac · waiting for a safe prompt"
            case "delivering": return "On your Mac · sending to agent"
            default: return agentState == "busy" ? "On your Mac · waiting for this turn to finish" : "On your Mac · waiting to send"
            }
        case "delivering": return "On your Mac · sending to agent"
        case "delivered": return "Delivered to agent"
        case "cancelled": return "Not sent"
        case "expired": return "Not sent · expired while waiting"
        default: return "Delivery unconfirmed · Check your Mac"
        }
    }
    enum CodingKeys: String, CodingKey {
        case status, reason, text
        case expiresAt = "expires_at", waitReason = "wait_reason"
        case requestID = "request_id", sessionID = "session_id", createdAt = "created_at", updatedAt = "updated_at"
    }
    func validate(sessionID expected: String, requestID: String? = nil) throws {
        guard sessionID == expected, UUID(uuidString: self.requestID) != nil,
              requestID == nil || self.requestID.caseInsensitiveCompare(requestID!) == .orderedSame,
              ["queued", "delivering", "delivered", "unknown", "cancelled", "expired"].contains(status),
              (text?.utf8.count ?? 0) <= 4096,
              createdAt >= 0, updatedAt >= createdAt,
              expiresAt.map({ $0 >= createdAt }) ?? true,
              (waitReason?.utf8.count ?? 0) <= 64 else { throw DuckyError.invalidResponse }
    }
}

public struct ReceiptResponse: Decodable {
    public let version: Int
    public let hostID: String
    public let receipt: MessageReceipt
    enum CodingKeys: String, CodingKey { case version, receipt; case hostID = "host_id" }
    public func validate(hostID: String, sessionID: String, requestID: String) throws {
        guard version == 1, self.hostID == hostID else { throw DuckyError.invalidResponse }
        try receipt.validate(sessionID: sessionID, requestID: requestID)
    }
}

public struct ConversationSnapshot: Decodable {
    public let version: Int
    public let hostID: String
    public let sessionID: String
    public let messages: [ConversationMessage]
    public let truncated: Bool
    public let receipts: [MessageReceipt]
    public let canSend: Bool
    public let sendContext: String?
    public let sendReason: String?
    enum CodingKeys: String, CodingKey {
        case version, messages, truncated, receipts
        case hostID = "host_id", sessionID = "session_id", canSend = "can_send", sendContext = "send_context", sendReason = "send_reason"
    }
    public func validate(hostID: String, sessionID: String) throws {
        guard version == 1, self.hostID == hostID, self.sessionID == sessionID,
              messages.count <= 100, receipts.count <= 100,
              Set(messages.map(\.id)).count == messages.count,
              Set(receipts.map(\.id)).count == receipts.count,
              messages.allSatisfy({ ["user", "assistant"].contains($0.role) }),
              messages.reduce(0, { $0 + $1.text.utf8.count }) <= 256 * 1024,
              !canSend || (sendContext?.isEmpty == false) else { throw DuckyError.invalidResponse }
        for receipt in receipts { try receipt.validate(sessionID: sessionID) }
    }
}

public struct OutgoingMessage: Equatable {
    public let sessionID: String
    public let sendContext: String
    public let requestID: String
    public let text: String
    public init(sessionID: String, sendContext: String, requestID: String = UUID().uuidString.lowercased(), text: String) {
        self.sessionID = sessionID; self.sendContext = sendContext
        self.requestID = requestID; self.text = Self.normalize(text)
    }
    public static func normalize(_ text: String) -> String {
        text.replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }
    public static func startsWithLocalCommand(_ text: String) -> Bool {
        guard let first = text.unicodeScalars.first(where: {
            !CharacterSet.whitespacesAndNewlines.contains($0) && $0.properties.generalCategory != .format
        }) else { return false }
        return first == "!" || first == "/" || first == "#"
    }
    public static func validText(_ text: String) -> Bool {
        let normalized = normalize(text)
        return !normalized.isEmpty && !startsWithLocalCommand(normalized) &&
            normalized.utf8.count <= 4096 && normalized.unicodeScalars.allSatisfy {
            ($0.value >= 32 && !(127...159).contains($0.value)) || $0.value == 10 || $0.value == 9
        }
    }
}
