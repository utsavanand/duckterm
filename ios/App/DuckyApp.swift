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
        if ProcessInfo.processInfo.arguments.contains("--ducky-map-ui-fixture") || ProcessInfo.processInfo.arguments.contains("--ducky-analysis-ui-fixture") {
            _model = StateObject(wrappedValue: MapUIFixture.model())
            return
        }
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
/// Constellation-only in-memory data. The regular chat fixture above is unchanged.
private enum MapUIFixture {
    static let hostID = "22222222222222222222222222222222"
    static let longFolder = "Research/An exceptionally long folder name for viewport verification"
    static let folders = ["DuckTerm/Features/Pairing", "DuckTerm/Infrastructure", "DuckTerm/Empty",
        "Design", longFolder, "Research/Deep work", "Personal/Notes", "Dense", "Empty"]

    static func folder(_ index: Int) -> String {
        switch index {
        case 0...1: return "DuckTerm/Features/Pairing"
        case 2...5: return "DuckTerm/Features"
        case 6...8: return "DuckTerm/Infrastructure"
        case 9...11: return "Design"
        case 12...14: return longFolder
        case 15...17: return "Research/Deep work"
        case 18...20: return "Personal/Notes"
        case 21...23: return ""
        default: return "Dense"
        }
    }
    static func state(_ index: Int) -> String {
        index == 0 ? "idle" : ["idle", "busy", "waiting", "terminated"][index % 4]
    }
    static func name(_ index: Int) -> String {
        switch index {
        case 0: return "Pairing engineer"
        case 1: return "Mobile reviewer"
        case 12: return "A deliberately long agent name for layout verification"
        default: return "Agent \(index)"
        }
    }
    @MainActor static func model() -> SessionModel {
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ducky-map-ui-reduced-motion") {
            precondition(UIAccessibility.isReduceMotionEnabled, "The QA device must have system Reduce Motion enabled")
        }
        let failure = arguments.contains("--ducky-map-ui-revoked") ? 403 :
            arguments.contains("--ducky-map-ui-stale") ? 503 : nil
        let invitation = Invitation(version: 1, hostID: hostID,
            invite: String(repeating: "i", count: 43), expiresAt: Int(Date().timeIntervalSince1970) + 600,
            url: URL(string: "https://127.0.0.1:9")!, certificateSHA256: String(repeating: "b", count: 64))
        let store = ChatUIFixtureStore(PairedHost(invitation: invitation, token: String(repeating: "t", count: 43)))
        let domain = "com.rubberduckhq.ducky.map-ui-fixture"
        let defaults = UserDefaults(suiteName: domain)!
        defaults.removePersistentDomain(forName: domain)
        return SessionModel(credentials: store, makeTransport: { _ in MapUIFixtureTransport(failure: failure) },
            pause: { try await Task.sleep(nanoseconds: failure == nil ? 15_000_000_000 : 1_000_000_000) },
            expansionStore: FolderExpansionStore(defaults: defaults))
    }
}

private final class MapUIFixtureTransport: DuckyTransport {
    private let failure: Int?
    private var firstSnapshotAt: Date?
    private let analysisEnabled = ProcessInfo.processInfo.arguments.contains("--ducky-analysis-ui-fixture")
    private var analysisJobs: [String: (AnalysisSubmission, AnalysisScope, Date)] = [:]
    private var receipts: [MessageReceipt] = []
    init(failure: Int?) { self.failure = failure }
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus { throw DuckyError.unavailable(403) }
    func pairingStatus() async throws -> PairStatus {
        try ChatUIFixture.decode(["id": "map-ui-device", "status": "active", "host_id": MapUIFixture.hostID])
    }
    func snapshot(cursor: String?) async throws -> HostSnapshot {
        if firstSnapshotAt == nil { firstSnapshotAt = Date() }
        if let failure, Date().timeIntervalSince(firstSnapshotAt!) >= 35 { throw DuckyError.unavailable(failure) }
        let sessions: [[String: Any]] = (0..<48).map { index in
            ["id": "map-agent-\(index)", "name": MapUIFixture.name(index), "folder": MapUIFixture.folder(index),
             "runtime": index.isMultiple(of: 2) ? "codex" : "claude-code", "state": MapUIFixture.state(index),
             "observed_at": Int(Date().timeIntervalSince1970), "test": true, "approval_visibility": "terminal_only", "can_read": true,
             "can_send": MapUIFixture.state(index) != "terminated", "send_context": "map-context-\(index)"]
        }
        let result: HostSnapshot = try ChatUIFixture.decode([
            "version": 2, "host_id": MapUIFixture.hostID, "read_only": true,
            "generated_at": Int(Date().timeIntervalSince1970), "truncated": false, "reset": false,
            "notices": [], "notification_delivery": "foreground_refresh", "folders": MapUIFixture.folders,
            "features": analysisEnabled ? ["conversations", "analysis_jobs"] : ["conversations"],
            "capabilities": ["status:read", "conversation:read", "session:send"] + (analysisEnabled ? ["analysis:folder", "analysis:oracle"] : []),
            "analysis": ["folder_questions": analysisEnabled && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-lite"),
                         "oracle_questions": analysisEnabled && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-lite") && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-code")],
            "sessions": sessions
        ])
        try result.validate(hostID: MapUIFixture.hostID)
        return result
    }
    private func index(_ sessionID: String) throws -> Int {
        guard sessionID.hasPrefix("map-agent-"), let index = Int(sessionID.dropFirst(10)), (0..<48).contains(index) else {
            throw DuckyError.unavailable(404)
        }
        return index
    }
    func conversation(sessionID: String) async throws -> ConversationSnapshot {
        let index = try index(sessionID)
        let messages = (0..<8).map { entry in
            ["id": "map-reply-\(entry)", "role": entry.isMultiple(of: 2) ? "user" : "assistant", "text":
                entry == 7 ? "## Scope checked\n\n**\(MapUIFixture.name(index))** is ready for the next message.\n\n- Keep folder scope\n- Preserve the draft\n\n```swift\nlet viewport = \"landscape\"\nprint(viewport)\n```" :
                "Checkpoint \(entry) for \(MapUIFixture.name(index)). This is synthetic UI test data with no connection to a Mac."]
        }
        let ownReceipts = receipts.filter { $0.sessionID == sessionID }.map {
            ["request_id": $0.requestID, "session_id": $0.sessionID, "status": $0.status,
             "created_at": $0.createdAt, "updated_at": $0.updatedAt, "text": $0.text ?? ""] as [String: Any]
        }
        let result: ConversationSnapshot = try ChatUIFixture.decode([
            "version": 1, "host_id": MapUIFixture.hostID, "session_id": sessionID,
            "messages": messages, "truncated": false, "receipts": ownReceipts,
            "can_send": MapUIFixture.state(index) != "terminated", "send_context": "map-context-\(index)"
        ])
        try result.validate(hostID: MapUIFixture.hostID, sessionID: sessionID)
        return result
    }
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt {
        let index = try index(message.sessionID)
        guard MapUIFixture.state(index) != "terminated", message.sendContext == "map-context-\(index)",
              OutgoingMessage.validText(message.text) else { throw DuckyError.unavailable(400) }
        if let existing = receipts.first(where: { $0.requestID == message.requestID }) { return existing }
        try await Task.sleep(nanoseconds: 500_000_000)
        let receipt: MessageReceipt = try ChatUIFixture.decode([
            "request_id": message.requestID, "session_id": message.sessionID, "status": "queued",
            "created_at": 1, "updated_at": 1, "text": message.text
        ])
        receipts.append(receipt)
        return receipt
    }
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt {
        guard let receipt = receipts.first(where: { $0.requestID == requestID && $0.sessionID == sessionID }) else {
            throw DuckyError.unavailable(404)
        }
        return receipt
    }
    func analysisContext(scope: AnalysisScope) async throws -> AnalysisContext {
        guard analysisEnabled else { throw DuckyError.unavailable(404) }
        let jobs = analysisJobs.values.filter { $0.1 == scope }
        let completed = jobs.filter { Date().timeIntervalSince($0.2) >= 2.5 }
        let pending = jobs.first { Date().timeIntervalSince($0.2) < 2.5 }
        let scopeObject = try JSONSerialization.jsonObject(with: JSONEncoder().encode(scope))
        return try ChatUIFixture.decode(["version": 1, "host_id": MapUIFixture.hostID, "scope": scopeObject,
            "context_id": "analysis-ui-context", "expires_at": Int64(Date().timeIntervalSince1970 * 1000) + 60_000,
            "history": try completed.map { try analysisObject($0.0, scope: $0.1, at: $0.2) },
            "pending": try pending.map { try analysisObject($0.0, scope: $0.1, at: $0.2) } ?? NSNull()])
    }
    func askAnalysis(_ submission: AnalysisSubmission, scope: AnalysisScope) async throws -> AnalysisReceipt {
        guard analysisEnabled else { throw DuckyError.unavailable(404) }
        if analysisJobs[submission.requestID] == nil { analysisJobs[submission.requestID] = (submission, scope, Date()) }
        return try await analysisReceipt(requestID: submission.requestID, scope: scope)
    }
    func analysisReceipt(requestID: String, scope: AnalysisScope) async throws -> AnalysisReceipt {
        guard let job = analysisJobs[requestID], job.1 == scope else { throw DuckyError.unavailable(404) }
        return try ChatUIFixture.decode(analysisObject(job.0, scope: scope, at: job.2))
    }
    private func analysisObject(_ submission: AnalysisSubmission, scope: AnalysisScope, at: Date) throws -> [String: Any] {
        let elapsed = Date().timeIntervalSince(at)
        let complete = elapsed >= 2.5
        let failed = complete && ProcessInfo.processInfo.arguments.contains("--ducky-analysis-failed")
        let created = Int64(at.timeIntervalSince1970 * 1000)
        let answer = "## Scope checked\n\n**2 sessions need attention** in \(scope.title).\n\n- The reviewer has a question.\n- The release agent is waiting for review.\n\n```text\nNo provider was called.\n```"
        return ["request_id": submission.requestID, "context_id": submission.contextID,
            "scope": try JSONSerialization.jsonObject(with: JSONEncoder().encode(scope)),
            "status": failed ? "failed" : complete ? "complete" : elapsed > 0.5 ? "running" : "queued",
            "reason": failed ? "provider_failed" : NSNull(), "question": submission.question,
            "answer": complete && !failed ? answer : NSNull(), "created_at": created,
            "updated_at": Int64(Date().timeIntervalSince1970 * 1000), "expires_at": created + 240_000,
            "captured_at": complete ? created : NSNull(), "source_count": scope == .oracle ? 40 : 8,
            "truncated": scope == .oracle]
    }
    func close() {}
}
#endif
