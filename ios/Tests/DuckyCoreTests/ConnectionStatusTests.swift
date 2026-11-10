import XCTest
@testable import DuckyCore

private func statusDecode<T: Decodable>(_ raw: Any) -> T {
    try! JSONDecoder().decode(T.self, from: JSONSerialization.data(withJSONObject: raw))
}
private final class StatusClock { var date = Date(timeIntervalSince1970: 1000) }
private final class StatusTransport: DuckyTransport {
    let host: PairedHost
    var revision = 1, snapshotCalls = 0, conversationCalls = 0, receiptCalls = 0
    var capabilities = ["conversation:read", "session:send"]
    var snapshotError: Error?, conversationFailure: Error?, receiptFailure: Error?, sendFailure: Error?
    var holdSnapshot = false, holdConversation = false, holdSend = false
    var snapshotGate: CheckedContinuation<Void, Never>?, conversationGate: CheckedContinuation<Void, Never>?, sendGate: CheckedContinuation<Void, Never>?
    var reply = "Current reply"
    var receipts: [MessageReceipt] = []
    var foundReceipt: MessageReceipt?
    var posts: [OutgoingMessage] = []
    init(_ host: PairedHost) { self.host = host }
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus { try await pairingStatus() }
    func pairingStatus() async throws -> PairStatus { statusDecode(["id": "fixture-device", "status": "active", "host_id": host.hostID]) }
    func snapshot(cursor: String?) async throws -> HostSnapshot {
        snapshotCalls += 1
        let failure = snapshotError
        let result: HostSnapshot = statusDecode(["version": 2, "host_id": host.hostID, "read_only": true,
            "generated_at": 1, "truncated": false, "reset": false, "notices": [], "notification_delivery": "foreground_refresh",
            "folders": ["Fixture"], "features": ["conversations"], "capabilities": capabilities, "authorization_revision": revision,
            "sessions": [["id": "a", "name": "Fixture agent", "folder": "Fixture", "runtime": "codex", "state": "busy",
                "observed_at": 1, "approval_visibility": "terminal_only", "can_send": true, "can_read": true, "send_context": "context"]]])
        if holdSnapshot { holdSnapshot = false; await withCheckedContinuation { snapshotGate = $0 } }
        if let failure { throw failure }
        return result
    }
    func conversation(sessionID: String) async throws -> ConversationSnapshot {
        conversationCalls += 1
        let failure = conversationFailure
        let result = ConversationSnapshot(version: 1, hostID: host.hostID, sessionID: sessionID,
            messages: [ConversationMessage(id: "reply", role: "assistant", text: reply, at: 1)],
            truncated: false, receipts: receipts, canSend: true, sendContext: "context", sendReason: nil)
        if holdConversation { holdConversation = false; await withCheckedContinuation { conversationGate = $0 } }
        if let failure { throw failure }
        return result
    }
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt {
        posts.append(message)
        if holdSend { holdSend = false; await withCheckedContinuation { sendGate = $0 } }
        if let sendFailure { throw sendFailure }
        return makeReceipt(message)
    }
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt {
        receiptCalls += 1
        if let receiptFailure { throw receiptFailure }
        guard let foundReceipt else { throw DuckyError.unavailable(404) }
        return foundReceipt
    }
    func makeReceipt(_ message: OutgoingMessage, status: String = "queued", updated: Int64 = 1_000_000, waitReason: String = "agent_busy") -> MessageReceipt {
        statusDecode(["request_id": message.requestID, "session_id": message.sessionID,
            "status": status, "created_at": 1_000_000, "updated_at": updated,
            "expires_at": 1_900_000, "wait_reason": status == "queued" ? (waitReason as Any) : NSNull(), "text": message.text])
    }
    func close() {}
}

@MainActor
final class ConnectionStatusTests: XCTestCase {
    private func setup() async -> (SessionModel, StatusTransport, StatusClock) {
        let invitation = Invitation(version: 1, hostID: String(repeating: "a", count: 32), invite: String(repeating: "i", count: 43),
            expiresAt: Int(Date().timeIntervalSince1970) + 600, url: URL(string: "https://127.0.0.1:9")!, certificateSHA256: String(repeating: "c", count: 64))
        let host = PairedHost(invitation: invitation, token: String(repeating: "t", count: 43))
        let client = StatusTransport(host), clock = StatusClock()
        let model = SessionModel(credentials: FakeStore(host), makeTransport: { _ in client }, now: { clock.date })
        await model.restore(); model.selectSession("a"); await model.refreshConversation()
        return (model, client, clock)
    }
    private func settle() async { for _ in 0..<100 { await Task.yield() } }
    private func queued(_ model: SessionModel, _ client: StatusTransport) async -> MessageReceipt {
        model.setDraft("Fixture message"); await model.sendMessage()
        guard case .acknowledged(let receipt) = model.sendState else { XCTFail("Expected Mac acknowledgment"); return client.makeReceipt(client.posts[0]) }
        return receipt
    }

    func testIndependentStatusAndReplyErrorsPreserveSafeSnapshotSending() async {
        let (model, client, clock) = await setup(); defer { model.forget() }
        let at = model.conversationAt
        clock.date.addTimeInterval(5); client.conversationFailure = URLError(.timedOut)
        await model.refreshConversation()
        XCTAssertEqual(model.phase, .live); XCTAssertEqual(model.agentActivityLabel, "Working")
        XCTAssertEqual(model.conversationAt, at); XCTAssertFalse(model.conversationFresh)
        XCTAssertEqual(model.conversationError, "Connection timed out.")
        XCTAssertTrue(model.canSend, "Fresh snapshot independently authorizes sends when replies fail")
        client.snapshotError = URLError(.notConnectedToInternet); await model.refresh()
        XCTAssertEqual(model.phase, .stale); XCTAssertEqual(model.agentActivityLabel, "Last seen working")
        XCTAssertFalse(model.canSend); XCTAssertFalse(model.canEditDraft)
        XCTAssertEqual(model.conversationAt, at)
    }

    func testRetryCoalescesStatusThenRepliesAndNeverReplaysPost() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        let receipt = await queued(model, client); client.foundReceipt = receipt
        client.snapshotError = URLError(.timedOut); await model.refresh()
        let reads = client.snapshotCalls, replies = client.conversationCalls
        client.snapshotError = nil; client.holdSnapshot = true; client.holdConversation = true
        let first = Task { await model.retryConnection() }; await settle()
        let second = Task { await model.retryConnection() }; await settle()
        XCTAssertTrue(model.retrying); XCTAssertTrue(model.statusRefreshing)
        XCTAssertEqual(client.snapshotCalls, reads + 1); XCTAssertEqual(client.conversationCalls, replies)
        client.snapshotGate?.resume(); await settle()
        XCTAssertEqual(model.phase, .live); XCTAssertFalse(model.statusRefreshing); XCTAssertTrue(model.conversationRefreshing)
        client.conversationGate?.resume(); await first.value; await second.value
        XCTAssertEqual(client.conversationCalls, replies + 1)
        XCTAssertEqual(client.posts.count, 1); XCTAssertFalse(model.retrying)
        XCTAssertTrue(model.conversationFresh); XCTAssertTrue(model.receiptIsFresh(receipt))
    }

    func testOfflinePastExpiryPreservesLastConfirmedUntilDeliveredReceipt() async {
        let (model, client, clock) = await setup(); defer { model.forget() }
        let receipt = await queued(model, client)
        XCTAssertTrue(model.receiptLabel(receipt).contains("waiting for this turn to finish"))
        client.snapshotError = URLError(.timedOut); await model.refresh()
        clock.date.addTimeInterval(3600)
        XCTAssertEqual(model.sendState, .acknowledged(receipt))
        XCTAssertTrue(model.receiptLabel(receipt).hasPrefix("Last confirmed:"))
        XCTAssertFalse(model.receiptLabel(receipt).contains("expired"))
        client.snapshotError = nil
        client.foundReceipt = client.makeReceipt(client.posts[0], status: "delivered", updated: 1_010_000)
        await model.retryConnection()
        guard case .acknowledged(let final) = model.sendState else { return XCTFail("Missing final receipt") }
        XCTAssertEqual(final.status, "delivered"); XCTAssertTrue(model.receiptLabel(final).hasPrefix("Delivered to agent · "))
        XCTAssertEqual(client.posts.count, 1)
    }

    func testOnlyServerCanConfirmExpiryAndRetryDoesNotResend() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        _ = await queued(model, client)
        client.foundReceipt = client.makeReceipt(client.posts[0], status: "expired", updated: 1_900_000)
        await model.retryConnection(); await model.retryConnection()
        guard case .acknowledged(let final) = model.sendState else { return XCTFail("Missing expiry receipt") }
        XCTAssertEqual(final.label, "Not sent · expired while waiting"); XCTAssertEqual(client.posts.count, 1)
        XCTAssertEqual(model.draft, "")
    }

    func testReceiptFailureDoesNotPretendFreshRepliesConfirmedDelivery() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        let receipt = await queued(model, client)
        client.receiptFailure = URLError(.timedOut); await model.refreshConversation()
        XCTAssertTrue(model.conversationFresh); XCTAssertEqual(model.phase, .live)
        XCTAssertFalse(model.receiptIsFresh(receipt)); XCTAssertNotNil(model.receiptError)
        XCTAssertTrue(model.receiptLabel(receipt).hasPrefix("Last confirmed:")); XCTAssertEqual(client.posts.count, 1)
    }

    func testHeldReplyAfterConnectionFailureCannotBecomeFresh() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        client.reply = "Stale held reply"; client.holdConversation = true
        let reading = Task { await model.refreshConversation() }; await settle()
        client.snapshotError = URLError(.timedOut); await model.refresh()
        client.conversationGate?.resume(); await reading.value
        XCTAssertFalse(model.conversationFresh)
        XCTAssertEqual(model.conversation?.messages.first?.text, "Current reply")
    }

    func testCancelledHeldReplyCannotPublishAfterStatusRecovery() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        client.reply = "Obsolete reply"; client.holdConversation = true
        let old = Task { await model.refreshConversation() }; await settle()
        client.snapshotError = URLError(.timedOut); await model.refresh()
        client.snapshotError = nil; client.reply = "Recovered reply"; await model.retryConnection()
        client.conversationGate?.resume(); await old.value
        XCTAssertEqual(model.conversation?.messages.first?.text, "Recovered reply")
        XCTAssertTrue(model.conversationFresh)
    }

    func testPermissionChangeDiscardsHeldReplyAndPrivateDraft() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        model.setDraft("Private draft"); client.holdConversation = true
        let reading = Task { await model.refreshConversation() }; await settle()
        client.capabilities = []; client.revision += 1; await model.refresh()
        client.conversationGate?.resume(); await reading.value
        XCTAssertNil(model.conversation); XCTAssertEqual(model.draft, ""); XCTAssertFalse(model.canSend)
    }

    func testSameScopeReapprovalCannotRestoreAnOldRevisionReply() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        client.reply = "Old private reply"; client.holdConversation = true
        let old = Task { await model.refreshConversation() }; await settle()
        client.revision += 1; client.reply = "New revision reply"; await model.refresh()
        client.conversationGate?.resume(); await old.value
        XCTAssertEqual(model.conversation?.messages.first?.text, "New revision reply")
    }

    func testBackgroundCancelsLoadingAndForegroundPromptlyRefreshesReplies() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        client.holdConversation = true
        let reading = Task { await model.refreshConversation() }; await settle()
        model.enterBackground(); XCTAssertFalse(model.conversationRefreshing)
        client.conversationGate?.resume(); await reading.value
        let count = client.conversationCalls; client.reply = "Reconnected reply"
        model.enterForeground(); await settle()
        XCTAssertEqual(client.conversationCalls, count + 1)
        XCTAssertEqual(model.conversation?.messages.first?.text, "Reconnected reply")
        XCTAssertTrue(model.conversationFresh)
    }

    func testLateQueuedPostCannotRegressDeliveredConversationReceipt() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        model.setDraft("Only once"); client.holdSend = true
        let posting = Task { await model.sendMessage() }; await settle()
        client.receipts = [client.makeReceipt(client.posts[0], status: "delivered", updated: 1_010_000)]
        await model.refreshConversation(); client.sendGate?.resume(); await posting.value
        guard case .acknowledged(let receipt) = model.sendState else { return XCTFail("Missing delivery") }
        XCTAssertEqual(receipt.status, "delivered"); XCTAssertEqual(client.posts.count, 1)
        client.receipts = [client.makeReceipt(client.posts[0])]; await model.refreshConversation()
        XCTAssertEqual(model.conversation?.receipts.first?.status, "delivered")
        XCTAssertFalse(model.receiptIsFresh(receipt), "Old queued response cannot confirm a newer delivery")
    }

    func testOlderQueuedReplyCannotConfirmNewerPendingReceipt() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        let old = await queued(model, client)
        client.foundReceipt = client.makeReceipt(client.posts[0], updated: 1_001_000)
        await model.refreshConversation()
        client.receipts = [old]; await model.refreshConversation()
        guard case .acknowledged(let receipt) = model.sendState else { return XCTFail("Missing receipt") }
        XCTAssertEqual(receipt.updatedAt, 1_001_000)
        XCTAssertFalse(model.receiptIsFresh(receipt))
    }

    func testPreviousMessageTerminalReceiptCannotRegressAfterAnotherSend() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        let old = await queued(model, client)
        client.receipts = [client.makeReceipt(client.posts[0], status: "delivered", updated: 1_001_000)]
        await model.refreshConversation()
        model.setDraft("Next message"); await model.sendMessage()
        client.receipts = [old, client.makeReceipt(client.posts[1])]; await model.refreshConversation()
        XCTAssertEqual(model.conversation?.receipts.first?.status, "delivered")
        XCTAssertFalse(model.receiptIsFresh(model.conversation!.receipts[0]))
    }

    func testLatePostErrorsCannotEraseAlreadyConfirmedQueuedReceipt() async {
        for failure: Error in [URLError(.timedOut), DuckyError.unavailable(503)] {
            let (model, client, _) = await setup()
            model.setDraft("Send once"); client.holdSend = true; client.sendFailure = failure
            let sending = Task { await model.sendMessage() }; await settle()
            let confirmed = client.makeReceipt(client.posts[0])
            client.receipts = [confirmed]; await model.refreshConversation()
            client.sendGate?.resume(); await sending.value
            XCTAssertEqual(model.sendState, .acknowledged(confirmed))
            XCTAssertTrue(model.receiptIsFresh(confirmed)); XCTAssertEqual(model.draft, "")
            await model.retryConnection(); XCTAssertEqual(client.posts.count, 1)
            model.forget()
        }
    }

    func testLateQueuedPostDoesNotReplaceNewerWaitReasonAtSameJournalTimestamp() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        model.setDraft("Send once"); client.holdSend = true
        let sending = Task { await model.sendMessage() }; await settle()
        let confirmed = client.makeReceipt(client.posts[0], waitReason: "approval_pending")
        client.receipts = [confirmed]; await model.refreshConversation()
        client.sendGate?.resume(); await sending.value
        XCTAssertEqual(model.sendState, .acknowledged(confirmed))
        XCTAssertEqual(model.conversation?.receipts.first?.waitReason, "approval_pending")
    }

    func testReplyForbiddenRequiresStatusRefreshBeforeAnotherSend() async {
        let (model, client, _) = await setup(); defer { model.forget() }
        client.conversationFailure = DuckyError.unavailable(403); await model.refreshConversation()
        XCTAssertNil(model.conversation); XCTAssertFalse(model.canSend)
        client.capabilities = []; await model.refresh(); XCTAssertFalse(model.canSend)
    }
}

final class ConnectionStatusProtocolTests: XCTestCase {
    func testAdditiveReceiptFieldsAndLegacyFallbackNeverInventExpiry() throws {
        var raw: [String: Any] = ["request_id": UUID().uuidString, "session_id": "a", "status": "queued", "created_at": 1, "updated_at": 2]
        let legacy: MessageReceipt = statusDecode(raw)
        XCTAssertNil(legacy.expiresAt); XCTAssertNil(legacy.waitReason)
        XCTAssertEqual(legacy.deliveryLabel(agentState: "busy"), "On your Mac · waiting for this turn to finish")
        for (reason, fragment) in [("approval_pending", "approval"), ("desktop_input", "desktop input"), ("safe_prompt", "safe prompt"), ("delivering", "sending"), ("future_reason", "waiting to send")] {
            raw["expires_at"] = 900_001; raw["wait_reason"] = reason
            let receipt: MessageReceipt = statusDecode(raw)
            try receipt.validate(sessionID: "a"); XCTAssertTrue(receipt.label.contains(fragment))
        }
        raw["expires_at"] = -1
        let malformed: MessageReceipt = statusDecode(raw)
        XCTAssertThrowsError(try malformed.validate(sessionID: "a"))
    }
    func testNetworkErrorDescriptionDoesNotExposeUntrustedURLOrPayload() {
        let error = NSError(domain: NSURLErrorDomain, code: URLError.timedOut.rawValue,
            userInfo: [NSLocalizedDescriptionKey: "credential-secret https://secret.invalid/private"])
        XCTAssertEqual(SessionModel.refreshError(error), "Connection timed out.")
    }
}
