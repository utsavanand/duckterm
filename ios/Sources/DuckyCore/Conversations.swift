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
    public var id: String { requestID }
    public var isPending: Bool { ["queued", "delivering"].contains(status) }
    public var label: String {
        switch status {
        case "queued": return "Queued on your Mac"
        case "delivering": return "Sending to session"
        case "delivered": return "Sent to session"
        case "cancelled": return "Not sent"
        case "expired": return "Expired before sending"
        default: return "Delivery unconfirmed · Check your Mac"
        }
    }
    enum CodingKeys: String, CodingKey {
        case status, reason, text
        case requestID = "request_id", sessionID = "session_id", createdAt = "created_at", updatedAt = "updated_at"
    }
    func validate(sessionID expected: String, requestID: String? = nil) throws {
        guard sessionID == expected, UUID(uuidString: self.requestID) != nil,
              requestID == nil || self.requestID.caseInsensitiveCompare(requestID!) == .orderedSame,
              ["queued", "delivering", "delivered", "unknown", "cancelled", "expired"].contains(status),
              (text?.utf8.count ?? 0) <= 4096 else { throw DuckyError.invalidResponse }
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
    public static func validText(_ text: String) -> Bool {
        let normalized = normalize(text)
        return !normalized.isEmpty && normalized.utf8.count <= 4096 && normalized.unicodeScalars.allSatisfy {
            ($0.value >= 32 && !(127...159).contains($0.value)) || $0.value == 10 || $0.value == 9
        }
    }
}
