import DuckyCore
import SwiftUI
import UIKit

@main
@MainActor
struct DuckyApp: App {
    @StateObject private var model: SessionModel
    @StateObject private var account: AccountModel
    @Environment(\.scenePhase) private var scene

    init() {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ducky-status-ui-fixture") {
            let fixture = StatusUIFixture.model()
            _model = StateObject(wrappedValue: fixture)
            _account = StateObject(wrappedValue: AccountModel(cloudURL: nil, devices: fixture))
            return
        }
        if ProcessInfo.processInfo.arguments.contains("--ducky-map-ui-fixture") || ProcessInfo.processInfo.arguments.contains("--ducky-analysis-ui-fixture") {
            let fixture = MapUIFixture.model()
            _model = StateObject(wrappedValue: fixture)
            _account = StateObject(wrappedValue: AccountModel(cloudURL: nil, devices: fixture))
            return
        }
        if ProcessInfo.processInfo.arguments.contains("--ducky-chat-ui-fixture") {
            let fixture = ChatUIFixture.model()
            _model = StateObject(wrappedValue: fixture)
            _account = StateObject(wrappedValue: AccountModel(cloudURL: nil, devices: fixture))
            return
        }
        if ProcessInfo.processInfo.arguments.contains("--ducky-account-ui-fixture") {
            let (fixture, account) = AccountUIFixture.models()
            _model = StateObject(wrappedValue: fixture)
            _account = StateObject(wrappedValue: account)
            return
        }
        #endif
        let devices = SessionModel()
        _model = StateObject(wrappedValue: devices)
        // Empty unless the build sets DUCKY_CLOUD_URL; without it Ducky offers invitations only.
        _account = StateObject(wrappedValue: AccountModel(
            cloudURL: AccountPairing.cloudURL(Bundle.main.object(forInfoDictionaryKey: "DuckyCloudURL")), devices: devices))
    }

    var body: some Scene {
        WindowGroup {
            ContentView(model: model, account: account)
                .preferredColorScheme(.dark)
                .task {
                    account.restore()
                    await model.restore()
                }
                .onOpenURL { url in
                    // Sign-in callbacks belong to the web sheet that started them, never to pairing.
                    guard url.host != "signed-in" else { return }
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
/// Connection states are synthetic; no persisted credentials or network requests.
private enum StatusUIFixture {
    static let hostID = "33333333333333333333333333333333"
    static let sessionID = "status-ui-agent"
    static let requestID = "33333333-3333-4333-8333-333333333333"
    @MainActor static func model() -> SessionModel {
        let invitation = Invitation(version: 1, hostID: hostID, invite: String(repeating: "i", count: 43),
            expiresAt: Int(Date().timeIntervalSince1970) + 600, url: URL(string: "https://127.0.0.1:9")!, certificateSHA256: String(repeating: "a", count: 64))
        let client = StatusUIFixtureTransport()
        let domain = "com.rubberduckhq.ducky.status-ui-fixture"
        let defaults = UserDefaults(suiteName: domain)!
        defaults.removePersistentDomain(forName: domain)
        let model = SessionModel(credentials: ChatUIFixtureStore(PairedHost(invitation: invitation, token: String(repeating: "t", count: 43))),
            makeTransport: { _ in client }, pause: { try await Task.sleep(nanoseconds: 60_000_000_000) },
            conversationPause: { try await Task.sleep(nanoseconds: 60_000_000_000) },
            retryPause: { try await Task.sleep(nanoseconds: 60_000_000_000) }, onLocalNetwork: { true },
            expansionStore: FolderExpansionStore(defaults: defaults))
        client.afterFirstSnapshot = { [weak model] in
            guard client.mode.hasPrefix("no-cache-offline") else { return }
            Task { @MainActor in
                // Scene startup can cancel an early read. Inject the outage only
                // after a successful snapshot has actually reached the model.
                for _ in 0..<1000 {
                    guard let model else { return }
                    if model.snapshot != nil { break }
                    try? await Task.sleep(nanoseconds: 10_000_000)
                }
                guard let model, model.snapshot != nil else { return }
                client.failSnapshots = true
                // The snapshot can be visible while its refresh is still
                // finishing. Wait through that coalesced read, then request
                // the failing read rather than leaving the fixture live.
                for _ in 0..<100 {
                    await model.refresh()
                    if model.phase == .stale { return }
                    try? await Task.sleep(nanoseconds: 10_000_000)
                }
            }
        }
        client.afterFirstRead = { [weak model] in
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 300_000_000)
                if client.mode == "reading" { await model?.refreshConversation() }
                else if ["failed", "reconnecting", "orphan-delivered"].contains(client.mode) {
                    await model?.refresh()
                    if client.mode == "reconnecting" { await model?.retryConnection() }
                }
            }
        }
        return model
    }
}
private final class StatusUIFixtureTransport: DuckyTransport {
    let mode = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix("--ducky-status-mode=") })?.components(separatedBy: "=").last ?? "waiting"
    var afterFirstRead: (() -> Void)?
    var afterFirstSnapshot: (() -> Void)?
    var failSnapshots = false
    private var reads = 0, failedReads = 0, snapshots = 0
    private let created = Int64(Date().timeIntervalSince1970 * 1000) - 120_000
    private var newReceipts: [MessageReceipt] = []
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus { throw DuckyError.unavailable(403) }
    func pairingStatus() async throws -> PairStatus {
        try ChatUIFixture.decode(["id": "status-fixture-device", "status": "active", "host_id": StatusUIFixture.hostID])
    }
    func snapshot(cursor: String?) async throws -> HostSnapshot {
        snapshots += 1
        if failSnapshots { throw URLError(.timedOut) }
        if reads > 0 && ["failed", "reconnecting", "orphan-delivered"].contains(mode) {
            failedReads += 1
            if mode == "reconnecting" && failedReads > 1 { try await Task.sleep(nanoseconds: 8_000_000_000) }
            throw URLError(.timedOut)
        }
        if snapshots == 1 { afterFirstSnapshot?() }
        return try ChatUIFixture.decode(["version": 2, "host_id": StatusUIFixture.hostID, "read_only": true,
            "generated_at": 1, "truncated": false, "reset": false, "notices": [], "notification_delivery": "foreground_refresh",
            "folders": ["Status fixture"], "features": ["conversations"], "capabilities": ["status:read", "conversation:read", "session:send"],
            "sessions": [["id": StatusUIFixture.sessionID, "name": "Status review agent", "folder": "Status fixture", "runtime": "codex",
                "state": mode == "delivered" ? "idle" : "busy", "observed_at": 1, "test": true,
                "approval_visibility": "terminal_only", "can_read": mode != "no-cache-offline-unreadable", "can_send": true, "send_context": "status-context"]]])
    }
    func conversation(sessionID: String) async throws -> ConversationSnapshot {
        reads += 1
        if mode == "no-cache-read-failure" || (reads > 1 && mode == "reading") { throw URLError(.timedOut) }
        let status = mode == "orphan-delivered" ? "delivered" : (["delivered", "expired"].contains(mode) ? mode : "queued")
        var receipts: [[String: Any]] = [["request_id": StatusUIFixture.requestID, "session_id": sessionID, "status": status,
            "created_at": created, "updated_at": created + 60_000, "expires_at": created + 900_000,
            "wait_reason": status == "queued" ? "agent_busy" : NSNull(), "text": "Can you share the current release status?"]]
        if mode == "orphan-delivered" {
            for (id, text) in [("44444444-4444-4444-8444-444444444444", NSNull() as Any),
                               ("55555555-5555-4555-8555-555555555555", "" as Any)] {
                receipts.append(["request_id": id, "session_id": sessionID, "status": "delivered",
                    "created_at": created, "updated_at": created + 60_000, "text": text])
            }
        }
        receipts += newReceipts.map { ["request_id": $0.requestID, "session_id": $0.sessionID, "status": $0.status,
            "created_at": $0.createdAt, "updated_at": $0.updatedAt, "wait_reason": "agent_busy", "text": $0.text ?? ""] }
        let value: ConversationSnapshot = try ChatUIFixture.decode(["version": 1, "host_id": StatusUIFixture.hostID,
            "session_id": sessionID, "messages": [["id": "status-reply", "role": "assistant",
                "text": "The release review is still in progress. I’m checking the remaining changes before publishing.", "at": created]],
            "receipts": receipts, "can_send": true, "send_context": "status-context", "truncated": false])
        if reads == 1 { afterFirstRead?() }
        return value
    }
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt {
        guard OutgoingMessage.validText(message.text), message.sessionID == StatusUIFixture.sessionID, message.sendContext == "status-context" else { throw DuckyError.unavailable(400) }
        let receipt: MessageReceipt = try ChatUIFixture.decode(["request_id": message.requestID, "session_id": message.sessionID,
            "status": "queued", "created_at": created + 100_000, "updated_at": created + 100_000, "wait_reason": "agent_busy", "text": message.text])
        newReceipts.append(receipt)
        return receipt
    }
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt {
        guard let found = newReceipts.first(where: { $0.id == requestID && $0.sessionID == sessionID }) else { throw DuckyError.unavailable(404) }
        return found
    }
    func close() { afterFirstRead = nil }
}

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

/// Account screens with a synthetic service and Macs: signed out, the Mac list,
/// waiting for the Mac, and denied (`--ducky-account-mode=`). No network or Keychain.
private enum AccountUIFixture {
    static let mode = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix("--ducky-account-mode=") })?.components(separatedBy: "=").last ?? "signin"
    static let studio = AccountMac(id: "d1", name: "Studio Mac", lastSeenAt: Int(Date().timeIntervalSince1970) - 120,
        hostID: String(repeating: "4", count: 32), certificateSHA256: String(repeating: "c", count: 64),
        tlsName: nil, url: URL(string: "https://192.168.1.24:4430")!)
    static let air = AccountMac(id: "d2", name: "MacBook Air", lastSeenAt: Int(Date().timeIntervalSince1970) - 7200,
        hostID: String(repeating: "5", count: 32), certificateSHA256: String(repeating: "d", count: 64),
        tlsName: nil, url: URL(string: "https://192.168.1.25:4430")!)

    @MainActor static func models() -> (SessionModel, AccountModel) {
        let paired = ["waiting", "denied"].contains(mode)
            ? PairedHost(mac: studio, token: String(repeating: "t", count: 43), login: "octo") : nil
        let domain = "com.rubberduckhq.ducky.account-ui-fixture"
        let defaults = UserDefaults(suiteName: domain)!
        defaults.removePersistentDomain(forName: domain)
        let devices = SessionModel(credentials: ChatUIFixtureStore(paired), makeTransport: { _ in AccountUIFixtureMac() },
            pause: { try await Task.sleep(nanoseconds: 60_000_000_000) }, onLocalNetwork: { true },
            expansionStore: FolderExpansionStore(defaults: defaults))
        let session = mode == "signin" ? nil : AccountSession(accountID: String(repeating: "e", count: 32), login: "octo",
            deviceID: String(repeating: "f", count: 32), sessionToken: String(repeating: "s", count: 43))
        let account = AccountModel(cloudURL: URL(string: "https://duckterm.utsava.xyz"), devices: devices,
            store: AccountUIFixtureStore(session), makeTransport: { _ in AccountUIFixtureCloud() })
        return (devices, account)
    }
}

private final class AccountUIFixtureStore: AccountStoring {
    private var session: AccountSession?
    init(_ session: AccountSession?) { self.session = session }
    func save(_ session: AccountSession) { self.session = session }
    func load() -> AccountSession? { session }
    func forget() { session = nil }
}

private final class AccountUIFixtureCloud: AccountTransport {
    func redeem(code: String, verifier: String, name: String) async throws -> AccountSession { throw AccountError.github }
    func macs(token: String) async throws -> [AccountMac] { [AccountUIFixture.studio, AccountUIFixture.air] }
    func ticket(hostID: String, token: String) async throws -> String { throw AccountError.service }
    func signOut(token: String) async throws {}
}

private final class AccountUIFixtureMac: DuckyTransport {
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus { throw DuckyError.unavailable(403) }
    func pairingStatus() async throws -> PairStatus {
        if AccountUIFixture.mode == "denied" { throw DuckyError.unavailable(403) }
        return try ChatUIFixture.decode(["id": "account-fixture-device", "status": "pending", "host_id": AccountUIFixture.studio.hostID])
    }
    func snapshot(cursor: String?) async throws -> HostSnapshot { throw DuckyError.unavailable(403) }
    func close() {}
}

private final class ChatUIFixtureStore: CredentialStoring {
    private var host: PairedHost?
    init(_ host: PairedHost?) { self.host = host }
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
    // A pairing from before Oracle questions existed: the Mac offers them, this iPhone was never granted them.
    private let oracleUngranted = ProcessInfo.processInfo.arguments.contains("--ducky-analysis-oracle-ungranted")
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
            "capabilities": ["status:read", "conversation:read", "session:send"] + (analysisEnabled ? ["analysis:folder"] + (oracleUngranted ? [] : ["analysis:oracle"]) : []),
            "analysis": ["folder_questions": analysisEnabled && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-lite"),
                         "oracle_questions": analysisEnabled && !oracleUngranted && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-lite") && !ProcessInfo.processInfo.arguments.contains("--ducky-analysis-code")],
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
