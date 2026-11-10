import XCTest
@testable import DuckyCore

private func decoded<T: Decodable>(_ value: Any, as: T.Type = T.self) throws -> T {
    try JSONDecoder().decode(T.self, from: JSONSerialization.data(withJSONObject: value))
}
private func agent(_ id: String, folder: String = "Work", state: String = "idle", runtime: String = "codex") -> AgentStatus {
    try! decoded(["id": id, "name": id, "folder": folder, "state": state, "runtime": runtime,
                  "observed_at": 1, "approval_visibility": "terminal_only", "can_send": true,
                  "can_read": true, "send_context": "context"])
}
private func snapshotV2(host: String, capabilities: [String] = ["conversation:read", "session:send"], state: String = "busy", canRead: Bool = true) -> HostSnapshot {
    try! decoded(["version": 2, "host_id": host, "read_only": true, "generated_at": 1, "truncated": false,
                  "reset": false, "notices": [], "notification_delivery": "foreground_refresh", "folders": ["Work"],
                  "features": ["conversations"], "capabilities": capabilities,
                  "sessions": ["a", "b"].map { ["id": $0, "name": $0, "folder": "Work", "runtime": "codex",
                      "state": state, "observed_at": 1, "approval_visibility": "terminal_only",
                      "can_send": true, "can_read": canRead, "send_context": "context"] as [String: Any] }])
}
private func conversation(_ host: String, session: String, text: String = "Reply") -> ConversationSnapshot {
    try! decoded(["version": 1, "host_id": host, "session_id": session,
                  "messages": [["id": "reply", "role": "assistant", "text": text]], "truncated": false,
                  "receipts": [], "can_send": true, "send_context": "context"])
}
private func receipt(_ request: OutgoingMessage, status: String = "queued") -> MessageReceipt {
    try! decoded(["request_id": request.requestID, "session_id": request.sessionID, "status": status,
                  "created_at": 1, "updated_at": 2, "text": request.text])
}

final class SessionTreeTests: XCTestCase {
    func testNestedCountsAndExpansionDoNotLoseInactiveOrUngroupedSessions() {
        let tree = SessionTree(sessions: [agent("a", folder: "Root/Child"), agent("b", folder: "Root", state: "terminated"),
            agent("c", folder: "", state: "interrupted"), agent("d", folder: "Root", state: "archived")], folders: ["Empty"])
        let collapsed = tree.rows(filter: .all, query: "", expanded: [])
        XCTAssertEqual(collapsed.map(\.path), ["", "Empty", "Root"])
        XCTAssertEqual(collapsed.map(\.count), [1, 0, 2])
        let expanded = tree.rows(filter: .all, query: "", expanded: ["Root", "Root/Child", ""])
        XCTAssertEqual(expanded.compactMap { $0.session?.id }, ["c", "b", "a"])
        XCTAssertEqual(expanded.first { $0.path == "Root/Child" }?.depth, 1)
    }
    func testSearchAndAttentionRevealMatchingAncestorsWithoutChangingPreferences() {
        let tree = SessionTree(sessions: [agent("match", folder: "Root/Child", state: "waiting"), agent("other", folder: "Other/Child")], folders: [])
        let search = tree.rows(filter: .all, query: "MATCH", expanded: [])
        XCTAssertEqual(search.compactMap { $0.session?.id }, ["match"])
        XCTAssertEqual(search.filter { $0.session == nil }.map(\.path), ["Root", "Root/Child"])
        XCTAssertEqual(tree.rows(filter: .waiting, query: "", expanded: []).compactMap { $0.session?.id }, ["match"])
        XCTAssertTrue(tree.rows(filter: .all, query: "", expanded: []).allSatisfy { $0.session == nil })
    }
    func testAllActiveAndNeedsYouRespectDesktopStates() {
        let states = ["idle", "busy", "waiting", "unknown", "stopped", "interrupted", "terminated", "archived", "merged"]
        let tree = SessionTree(sessions: states.map { agent($0, state: $0) }, folders: [])
        XCTAssertEqual(tree.matching(filter: .all, query: "").count, 7)
        XCTAssertEqual(tree.matching(filter: .active, query: "").map(\.state), Array(states.prefix(4)))
        XCTAssertEqual(tree.matching(filter: .waiting, query: "").map(\.state), ["waiting"])
        XCTAssertEqual(agent("ended", state: "terminated").stateLabel, "Finished")
        XCTAssertEqual(agent("stopped", state: "stopped").stateLabel, "Stopped")
    }
    func testExpansionIsHostScopedAndEmptyFolderKeyDoesNotCollide() {
        let name = "ducky-expansion-test-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let store = FolderExpansionStore(defaults: defaults)
        store.save(["", "Ungrouped", "Root/Child"], hostID: "a")
        store.save(["Other/Child"], hostID: "b")
        XCTAssertEqual(FolderExpansionStore(defaults: defaults).load(hostID: "a"), ["", "Ungrouped", "Root/Child"])
        store.forget(hostID: "a")
        XCTAssertEqual(store.load(hostID: "a"), [])
        XCTAssertEqual(store.load(hostID: "b"), ["Other/Child"])
    }
}

private final class ConversationTransport: DuckyTransport {
    let host: PairedHost
    var capabilities = ["conversation:read", "session:send"]
    var state = "busy"
    var conversationCalls: [String] = []
    var sends: [OutgoingMessage] = []
    var sendError: Error?
    var readError: Error?
    var canRead = true
    var holdRead = false, holdSend = false
    var readContinuation: CheckedContinuation<Void, Never>?
    var sendContinuation: CheckedContinuation<Void, Never>?
    var receiptResult: MessageReceipt?
    var closed = false
    init(_ host: PairedHost) { self.host = host }
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus { try await pairingStatus() }
    func pairingStatus() async throws -> PairStatus { try decoded(["id": "device", "status": "active", "host_id": host.hostID]) }
    func snapshot(cursor: String?) async throws -> HostSnapshot { snapshotV2(host: host.hostID, capabilities: capabilities, state: state, canRead: canRead) }
    func conversation(sessionID: String) async throws -> ConversationSnapshot {
        conversationCalls.append(sessionID)
        if let readError { throw readError }
        if holdRead { holdRead = false; await withCheckedContinuation { readContinuation = $0 } }
        return DuckyCoreTestsConversation(host.hostID, session: sessionID)
    }
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt {
        sends.append(message)
        if holdSend { holdSend = false; await withCheckedContinuation { sendContinuation = $0 } }
        if let sendError { throw sendError }
        return DuckyCoreTestsReceipt(message)
    }
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt {
        guard let receiptResult else { throw DuckyError.unavailable(404) }
        return receiptResult
    }
    func close() { closed = true }
}
// Avoid same-name protocol methods shadowing the file-local fixture factories.
private func DuckyCoreTestsConversation(_ host: String, session: String) -> ConversationSnapshot { conversation(host, session: session) }
private func DuckyCoreTestsReceipt(_ message: OutgoingMessage) -> MessageReceipt { receipt(message) }

@MainActor
final class ConversationLifecycleTests: XCTestCase {
    private func setup() async -> (SessionModel, ConversationTransport) {
        let invitation = Invitation(version: 1, hostID: String(repeating: "a", count: 32), invite: String(repeating: "i", count: 43),
            expiresAt: Int(Date().timeIntervalSince1970) + 600, url: URL(string: "https://127.0.0.1:4430")!, certificateSHA256: String(repeating: "c", count: 64))
        let host = PairedHost(invitation: invitation, token: String(repeating: "t", count: 43))
        let transport = ConversationTransport(host)
        let model = SessionModel(credentials: FakeStore(host), makeTransport: { _ in transport })
        await model.restore()
        return (model, transport)
    }
    private func settle() async { for _ in 0..<50 { await Task.yield() } }
    func testLegacyReadOnlyGrantsNeverReadOrSendConversation() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        transport.capabilities = []
        await model.refresh(); model.selectSession("a"); await settle()
        model.setDraft("Hello"); await model.sendMessage()
        XCTAssertTrue(transport.conversationCalls.isEmpty)
        XCTAssertTrue(transport.sends.isEmpty)
        XCTAssertFalse(model.canSend)
    }
    func testNewSessionCanSendBeforeTranscriptExistsAndReadFailureDoesNotBlockSending() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        transport.canRead = false; await model.refresh()
        model.selectSession("a"); await settle()
        XCTAssertTrue(model.canSend); XCTAssertNil(model.conversation)
        XCTAssertTrue(transport.conversationCalls.isEmpty)
        model.setDraft("First message"); await model.sendMessage()
        XCTAssertEqual(transport.sends.count, 1)
        transport.canRead = true; transport.readError = DuckyError.unavailable(503)
        await model.refresh(); await model.refreshConversation()
        XCTAssertTrue(model.canSend); XCTAssertNotNil(model.conversationError)
    }
    func testBusySessionQueuesOnceAndAcknowledgmentDoesNotMeanSeen() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        model.selectSession("a"); await settle()
        model.setDraft("Hello\nsecond line"); transport.holdSend = true
        let sending = Task { await model.sendMessage() }; await settle()
        await model.sendMessage()
        XCTAssertEqual(transport.sends.count, 1)
        transport.sendContinuation?.resume(); await sending.value
        XCTAssertEqual(model.draft, "")
        guard case .acknowledged(let receipt) = model.sendState else { return XCTFail("No acknowledgment") }
        XCTAssertEqual(receipt.label, "On your Mac · waiting to send")
        XCTAssertFalse(receipt.label.contains("seen"))
    }
    func testTimedOutSendNeverReplaysAndLaterReceiptResolvesSameID() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        model.selectSession("a"); await settle(); model.setDraft("Hello")
        transport.sendError = URLError(.timedOut)
        await model.sendMessage()
        XCTAssertEqual(model.sendState, .unconfirmed); XCTAssertEqual(model.draft, "Hello")
        await model.sendMessage(); await model.refreshConversation()
        XCTAssertEqual(transport.sends.count, 1)
        transport.receiptResult = receipt(transport.sends[0], status: "delivered")
        await model.refreshConversation()
        guard case .acknowledged(let result) = model.sendState else { return XCTFail("No receipt") }
        XCTAssertEqual(result.requestID, transport.sends[0].requestID)
        XCTAssertEqual(result.label, "Delivered to agent"); XCTAssertEqual(model.draft, "")
    }
    func testLateConversationCannotAppearInAnotherSession() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        transport.holdRead = true; model.selectSession("a"); await settle()
        model.selectSession("b"); await settle()
        XCTAssertEqual(model.conversation?.sessionID, "b")
        transport.readContinuation?.resume(); await settle()
        XCTAssertEqual(model.conversation?.sessionID, "b")
    }
    func testForgetInvalidatesOutstandingSendAndAllSensitiveConversationState() async {
        let (model, transport) = await setup()
        model.selectSession("a"); await settle(); model.setDraft("Private draft"); transport.holdSend = true
        let sending = Task { await model.sendMessage() }; await settle()
        model.forget(); transport.sendContinuation?.resume(); await sending.value
        XCTAssertNil(model.conversation); XCTAssertNil(model.selectedSessionID)
        XCTAssertTrue(model.sends.isEmpty); XCTAssertTrue(model.drafts.isEmpty)
    }
    func testInactiveCanReadButCannotSendAndPermissionLossClearsHistory() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        transport.state = "stopped"; await model.refresh()
        model.selectSession("a"); await settle()
        XCTAssertNotNil(model.conversation); XCTAssertFalse(model.canSend)
        model.setDraft("Hello"); await model.sendMessage(); XCTAssertTrue(transport.sends.isEmpty)
        transport.capabilities = []; await model.refresh()
        XCTAssertNil(model.conversation)
    }
    func testBackgroundInvalidatesReadAndBlocksSends() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        transport.holdRead = true; model.selectSession("a"); await settle()
        model.enterBackground(); transport.readContinuation?.resume(); await settle()
        XCTAssertNil(model.conversation); XCTAssertFalse(model.canSend)
    }
    func testLocalCommandsNeverReachTransportAndEditingAllowsPlainText() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        model.selectSession("a"); await settle()
        for text in ["!pwd", " /help", "\n\t# remember this", "\r\n  !echo hello\nsecond line", "\u{feff}!pwd", "\u{200b} /clear"] {
            model.setDraft(text); await model.sendMessage()
            XCTAssertTrue(transport.sends.isEmpty)
            XCTAssertEqual(model.draft, text)
            XCTAssertNil(model.sendState)
        }
        model.setDraft("Explain /help and # headings."); await model.sendMessage()
        XCTAssertEqual(transport.sends.count, 1)
        XCTAssertEqual(model.draft, "")
    }
    func testServerCommandRejectionPreservesDraftWithoutUnconfirmedReceipt() async {
        let (model, transport) = await setup()
        defer { model.forget() }
        model.selectSession("a"); await settle()
        model.setDraft("A plain message")
        transport.sendError = DuckyError.localCommandUnavailable
        await model.sendMessage()
        XCTAssertEqual(model.sendState, .rejected(DuckyError.localCommandUnavailable.localizedDescription))
        XCTAssertEqual(model.draft, "A plain message")
        XCTAssertTrue(model.canSend)
        transport.sendError = nil
        model.setDraft("Edited plain message"); await model.sendMessage()
        XCTAssertEqual(transport.sends.count, 2)
        XCTAssertNotEqual(transport.sends[0].requestID, transport.sends[1].requestID)
    }
}

final class ConversationProtocolTests: XCTestCase {
    func testSnapshotV2IsAdditiveAndPermissionsFailClosed() throws {
        let snapshot = snapshotV2(host: "host", capabilities: [])
        try snapshot.validate(hostID: "host")
        XCTAssertTrue(snapshot.supportsConversations)
        XCTAssertFalse(snapshot.allows("session:send"))
    }
    func testConversationRejectsWrongIdentityToolsAndOversizedText() throws {
        let value = conversation("host", session: "a")
        XCTAssertNoThrow(try value.validate(hostID: "host", sessionID: "a"))
        XCTAssertThrowsError(try value.validate(hostID: "other", sessionID: "a"))
        XCTAssertThrowsError(try value.validate(hostID: "host", sessionID: "b"))
        let oversized = conversation("host", session: "a", text: String(repeating: "a", count: 256 * 1024 + 1))
        XCTAssertThrowsError(try oversized.validate(hostID: "host", sessionID: "a"))
        let tools: ConversationSnapshot = try decoded(["version": 1, "host_id": "host", "session_id": "a", "can_send": false,
            "messages": [["id": "x", "role": "tool", "text": "private tool" ]], "receipts": [], "truncated": false])
        XCTAssertThrowsError(try tools.validate(hostID: "host", sessionID: "a"))
    }
    func testTextLimitsUseUTF8AndAllowOnlyPlainMultilineText() {
        XCTAssertTrue(OutgoingMessage.validText("First\r\nSecond\tline"))
        for text in ["  ", "\u{1b}[31m", "a\u{0}b", "a\u{7f}b", "a\u{85}b", "a\rb", String(repeating: "😀", count: 1025)] {
            XCTAssertFalse(OutgoingMessage.validText(text))
        }
        XCTAssertTrue(OutgoingMessage.validText(String(repeating: "😀", count: 1024)))
    }
    func testFirstNonWhitespaceCommandPrefixesAreRejectedWithoutBanningOrdinaryPunctuation() {
        for prefix in ["!", "/", "#"] {
            for whitespace in ["", " ", "\t", "\n\r\n", " \n\t ", "\u{a0}\u{2003}", "\u{feff}", "\u{200b} ", "\t\u{2066}\n\u{200d} "] {
                let text = whitespace + prefix + "command\nmore text"
                XCTAssertTrue(OutgoingMessage.startsWithLocalCommand(text), text)
                XCTAssertFalse(OutgoingMessage.validText(text), text)
            }
        }
        for text in ["Hello!", "Explain /help", "Use # headings", "First line\n/quoted command", "First\n! second", "😀 / # !", "\u{200b}Hello!"] {
            XCTAssertTrue(OutgoingMessage.validText(text), text)
        }
    }
    func testOnlyExactBoundedServerReasonGetsCommandWording() {
        let expected = Data(#"{"error":"local_command_unavailable"}"#.utf8)
        guard case .localCommandUnavailable = DuckyClient.messageRejection(expected) else { return XCTFail("Missing command reason") }
        for data in [Data(), Data(#"{"reason":"local_command_unavailable"}"#.utf8),
                     Data(#"{"error":"invalid_text","message":"untrusted text"}"#.utf8),
                     Data(#"{"error":"LOCAL_COMMAND_UNAVAILABLE"}"#.utf8),
                     expected + Data(repeating: 32, count: 4096)] {
            guard case .unavailable(400) = DuckyClient.messageRejection(data) else { return XCTFail("Unexpected server wording") }
        }
    }
    func testDirectClientRejectsCommandsBeforeDNSOrNetwork() async throws {
        let invitation = Invitation(version: 1, hostID: String(repeating: "a", count: 32), invite: String(repeating: "i", count: 43),
            expiresAt: Int(Date().timeIntervalSince1970) + 600, url: URL(string: "https://fixture.local:4430")!, certificateSHA256: String(repeating: "c", count: 64))
        let client = try DuckyClient(host: PairedHost(invitation: invitation, token: String(repeating: "t", count: 43)), resolver: { _ in
            XCTFail("Rejected messages must not resolve or connect"); return []
        })
        defer { client.close() }
        for text in ["!pwd", "\n /help", "\t# memory", "\u{feff}!pwd", "\u{200b} /clear"] {
            do {
                _ = try await client.send(OutgoingMessage(sessionID: "a", sendContext: "context", text: text))
                XCTFail("Command sent")
            } catch DuckyError.localCommandUnavailable { }
        }
    }
}
