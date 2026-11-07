import DuckyCore
import SwiftUI
import UIKit

@main
@MainActor
struct DuckyApp: App {
    @StateObject private var model: SessionModel
    @Environment(\.scenePhase) private var scene

    init() {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ducky-chat-ui-fixture") {
            _model = StateObject(wrappedValue: ChatUIFixture.model())
            return
        }
        #endif
        _model = StateObject(wrappedValue: SessionModel())
    }

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .preferredColorScheme(.dark)
                .task {
                    await model.restore()
                }
                .onOpenURL { url in
                    Task {
                        await model.pair(link: url.absoluteString, name: UIDevice.current.name)
                    }
                }
                .onChange(of: model.host) { _ in
                    // Pairing can finish after the URL-opening scene transition.
                    // Observe the new connection with the current scene value.
                    if scene == .active { model.enterForeground() }
                }
                .onChange(of: scene) { value in
                    if value == .active { model.enterForeground() }
                    else { model.enterBackground() }
                }
        }
    }
}

#if DEBUG
/// UI-only data: no credential storage, sockets, real sessions or agent execution.
private enum ChatUIFixture {
    static let hostID = "11111111111111111111111111111111"
    static let sessionID = "chat-ui-test"

    static func decode<T: Decodable>(_ value: Any) throws -> T {
        try JSONDecoder().decode(T.self, from: JSONSerialization.data(withJSONObject: value))
    }

    @MainActor static func model() -> SessionModel {
        let invitation = Invitation(version: 1, hostID: hostID,
            invite: String(repeating: "i", count: 43),
            expiresAt: Int(Date().timeIntervalSince1970) + 600,
            url: URL(string: "https://127.0.0.1:9")!,
            certificateSHA256: String(repeating: "a", count: 64))
        let store = ChatUIFixtureStore(PairedHost(invitation: invitation,
            token: String(repeating: "t", count: 43)))
        let domain = "com.rubberduckhq.ducky.chat-ui-fixture"
        let defaults = UserDefaults(suiteName: domain)!
        defaults.removePersistentDomain(forName: domain)
        return SessionModel(credentials: store, makeTransport: { _ in ChatUIFixtureTransport() },
            expansionStore: FolderExpansionStore(defaults: defaults))
    }
}

private final class ChatUIFixtureStore: CredentialStoring {
    private var host: PairedHost?
    init(_ host: PairedHost) { self.host = host }
    func save(_ host: PairedHost) { self.host = host }
    func load() -> PairedHost? { host }
    func forget() { host = nil }
}

private final class ChatUIFixtureTransport: DuckyTransport {
    private var receipts: [MessageReceipt] = []

    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus {
        throw DuckyError.unavailable(403)
    }

    func pairingStatus() async throws -> PairStatus {
        try ChatUIFixture.decode(["id": "chat-ui-device", "status": "active", "host_id": ChatUIFixture.hostID])
    }

    func snapshot(cursor: String?) async throws -> HostSnapshot {
        let result: HostSnapshot = try ChatUIFixture.decode([
            "version": 2, "host_id": ChatUIFixture.hostID, "read_only": true,
            "generated_at": Int(Date().timeIntervalSince1970), "truncated": false, "reset": false,
            "notices": [], "notification_delivery": "foreground_refresh", "folders": ["UI Test"],
            "features": ["conversations"], "capabilities": ["status:read", "conversation:read", "session:send"],
            "sessions": [["id": ChatUIFixture.sessionID, "name": "Chat UI test", "folder": "UI Test",
                "runtime": "codex", "state": "idle", "observed_at": 1, "test": true,
                "approval_visibility": "terminal_only", "can_send": true, "can_read": true,
                "send_context": "chat-ui-context"]]
        ])
        try result.validate(hostID: ChatUIFixture.hostID)
        return result
    }

    func conversation(sessionID: String) async throws -> ConversationSnapshot {
        guard sessionID == ChatUIFixture.sessionID else { throw DuckyError.unavailable(404) }
        var messages = (0..<30).map { index in
            let text: String
            if index == 15 {
                text = (0..<14).map { paragraph in
                    "Reading marker \(paragraph). " + String(repeating:
                        "This paragraph belongs to one long agent reply. Returning to the conversation should preserve this reading location instead of jumping to the reply's beginning. ", count: 3)
                }.joined(separator: "\n\n")
            } else {
                text = "Checkpoint \(index)\n\nThis is a disposable conversation entry for reading-position verification. Keep this checkpoint visible when returning from the session list."
            }
            return ["id": "history-\(index)", "role": index.isMultiple(of: 2) ? "user" : "assistant", "text": text]
        }
        messages.append(["id": "formatted-reply", "role": "assistant", "text": """
        ## Shipping checklist

        **Checkpoint ready** with *focused review* and `duckterm status`.

        - Verify folder isolation
        - Keep queues intact

        ```swift
        let retryLimit = 3
        print("ready <agent>")
        ```
        """])
        if ProcessInfo.processInfo.arguments.contains("--ducky-chat-untrusted-reply-fixture") {
            let links = """
            ## Untrusted reply fixture

            [**trusted.example**](https://untrusted.example/open)

            <https://untrusted.example/autolink>

            https://untrusted.example/bare

            www.untrusted.example/plain

            [Pair](ducky://pair?invite=fixture)
            """
            let nesting = String(repeating: "> ", count: 16) +
                String(repeating: "- ", count: 16) + "Deeply nested fixture text remains readable."
            messages = [["id": "untrusted-reply", "role": "assistant", "text": links + "\n\n" + nesting]]
        }
        if ProcessInfo.processInfo.arguments.contains("--ducky-chat-large-reply-fixture") {
            messages = [["id": "large-reply", "role": "assistant", "text":
                "# Oversized **unformatted**\n\n" + String(repeating: "Plain reply. ", count: 3000)]]
        }
        let receiptData = receipts.map { ["request_id": $0.requestID, "session_id": $0.sessionID,
            "status": $0.status, "created_at": $0.createdAt, "updated_at": $0.updatedAt,
            "text": $0.text ?? ""] as [String: Any] }
        let result: ConversationSnapshot = try ChatUIFixture.decode([
            "version": 1, "host_id": ChatUIFixture.hostID, "session_id": sessionID,
            "messages": messages, "truncated": false, "receipts": receiptData,
            "can_send": true, "send_context": "chat-ui-context"
        ])
        try result.validate(hostID: ChatUIFixture.hostID, sessionID: sessionID)
        return result
    }

    func send(_ message: OutgoingMessage) async throws -> MessageReceipt {
        guard message.sessionID == ChatUIFixture.sessionID,
              message.sendContext == "chat-ui-context", OutgoingMessage.validText(message.text) else {
            throw DuckyError.unavailable(400)
        }
        if let existing = receipts.first(where: { $0.requestID == message.requestID }) { return existing }
        try await Task.sleep(nanoseconds: 1_500_000_000)
        let receipt: MessageReceipt = try ChatUIFixture.decode([
            "request_id": message.requestID, "session_id": message.sessionID,
            "status": "queued", "created_at": 1, "updated_at": 1, "text": message.text
        ])
        receipts.append(receipt)
        return receipt
    }

    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt {
        guard sessionID == ChatUIFixture.sessionID,
              let receipt = receipts.first(where: { $0.requestID == requestID }) else {
            throw DuckyError.unavailable(404)
        }
        return receipt
    }

    func close() {}
}
#endif
