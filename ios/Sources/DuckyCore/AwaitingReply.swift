import Foundation

public enum AwaitingReply {
    /// The snapshot that would report "busy" can lag a delivery by a poll, so a
    /// fresh delivery counts as work in progress for this long.
    static let deliveryGrace: TimeInterval = 20

    /// Whether the agent is still turning the conversation into a reply.
    /// Nothing is claimed for a session that needs the owner or has stopped.
    public static func pending(state: String, messages: [ConversationMessage],
                               receipts: [MessageReceipt], now: Date) -> Bool {
        if state == "busy" { return true }
        guard state == "idle" || state == "unknown" else { return false }
        let lastReply = messages.last { $0.role == "assistant" }?.at ?? 0
        let nowMs = Int64(now.timeIntervalSince1970 * 1000)
        return receipts.contains {
            $0.status == "delivered" && $0.updatedAt > lastReply &&
                Double(nowMs - $0.updatedAt) / 1000 < deliveryGrace
        }
    }
}

public extension SessionModel {
    /// Only from a live status: a stale "busy" would be a spinner for work that may have ended.
    var awaitingReply: Bool {
        guard phase == .live, let session = selectedSession else { return false }
        return AwaitingReply.pending(state: session.state, messages: conversation?.messages ?? [],
                                     receipts: conversation?.receipts ?? [], now: Date())
    }
}
