import XCTest
@testable import DuckyCore

private let analysisHost = String(repeating: "a", count: 32)
private let analysisNow: Int64 = 1_800_000_000_000
private func decodeAnalysis<T: Decodable>(_ value: Any) throws -> T {
    try JSONDecoder().decode(T.self, from: JSONSerialization.data(withJSONObject: value))
}
private func scopeJSON(_ scope: AnalysisScope) -> [String: Any] {
    switch scope {
    case .folder(let path): return ["kind": "folder", "path": path]
    case .ungrouped: return ["kind": "ungrouped"]
    case .oracle: return ["kind": "oracle"]
    }
}
private func receiptJSON(_ submission: AnalysisSubmission, scope: AnalysisScope = .folder("Work"), status: String = "queued") -> [String: Any] {
    ["request_id": submission.requestID, "context_id": submission.contextID, "scope": scopeJSON(scope),
     "status": status, "reason": NSNull(), "question": submission.question,
     "answer": status == "complete" ? "**Fixture answer**, no provider called." : NSNull(),
     "created_at": analysisNow, "updated_at": analysisNow + 1000, "expires_at": analysisNow + 240_000,
     "captured_at": status == "complete" ? analysisNow + 500 : NSNull(), "source_count": 2, "truncated": false]
}
private func submission(_ question: String = "What changed?") -> AnalysisSubmission {
    AnalysisSubmission(requestID: UUID().uuidString.lowercased(), contextID: "context-original", question: question)
}
private func contextJSON(_ scope: AnalysisScope, history: [[String: Any]] = [], pending: [String: Any]? = nil) -> [String: Any] {
    ["version": 1, "host_id": analysisHost, "scope": scopeJSON(scope), "context_id": "context-new", "expires_at": analysisNow + 60_000,
     "history": history, "pending": pending ?? NSNull()]
}
private func analysisSnapshot(folder: Bool = true, oracle: Bool = true, features: [String] = ["analysis_jobs"], capabilities: [String] = ["analysis:folder", "analysis:oracle"], revision: Int = 0) -> HostSnapshot {
    try! decodeAnalysis(["version": 2, "host_id": analysisHost, "generated_at": analysisNow, "read_only": true,
        "sessions": [], "folders": ["Work", "Empty"], "truncated": false, "reset": false, "notices": [],
        "notification_delivery": "foreground_refresh", "features": features, "capabilities": capabilities, "authorization_revision": revision,
        "analysis": ["folder_questions": folder, "oracle_questions": oracle]])
}
private func testInvitation(host: String = analysisHost, pin: String = String(repeating: "b", count: 64)) -> Invitation {
    Invitation(version: 1, hostID: host, invite: String(repeating: "i", count: 43), expiresAt: Int(Date().timeIntervalSince1970) + 600,
               url: URL(string: "https://127.0.0.1:8765")!, certificateSHA256: pin)
}
private final class AnalysisStore: CredentialStoring {
    var value: PairedHost? = PairedHost(invitation: testInvitation(), token: String(repeating: "t", count: 43))
    func load() -> PairedHost? { value }
    func save(_ host: PairedHost) { value = host }
    func forget() { value = nil }
}
private final class AnalysisFake: DuckyTransport {
    var snapshotValue = analysisSnapshot()
    var snapshotError: Error?
    var contextError: Error?
    var contextCalls = 0, receiptCalls = 0
    var posts: [(AnalysisSubmission, AnalysisScope)] = []
    var postError: Error?
    var lookupError: Error?
    var status = "queued"
    var completedHistory: [[String: Any]] = []
    var recovery: AnalysisSubmission?
    var holdPost = false, holdContext = false, holdLookup = false
    var held: CheckedContinuation<Void, Never>?
    var pairState = "active"
    var claimedTokens: [String] = []
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus {
        try decodeAnalysis(["id": "device", "host_id": invitation.hostID, "status": "pending", "re_pair": true, "authorization_revision": 0])
    }
    func pairingStatus() async throws -> PairStatus { try decodeAnalysis(["id": "device", "host_id": analysisHost, "status": pairState]) }
    func snapshot(cursor: String?) async throws -> HostSnapshot {
        if let snapshotError { throw snapshotError }; return snapshotValue
    }
    func analysisContext(scope: AnalysisScope) async throws -> AnalysisContext {
        contextCalls += 1
        if let contextError { throw contextError }
        let result: AnalysisContext = try decodeAnalysis(contextJSON(scope, history: completedHistory, pending: recovery.map { receiptJSON($0, scope: scope) }))
        if holdContext { holdContext = false; await withCheckedContinuation { held = $0 } }
        return result
    }
    func askAnalysis(_ submission: AnalysisSubmission, scope: AnalysisScope) async throws -> AnalysisReceipt {
        posts.append((submission, scope))
        let initialStatus = status
        if holdPost { holdPost = false; await withCheckedContinuation { held = $0 } }
        if let postError { throw postError }
        return try decodeAnalysis(receiptJSON(submission, scope: scope, status: initialStatus))
    }
    func analysisReceipt(requestID: String, scope: AnalysisScope) async throws -> AnalysisReceipt {
        receiptCalls += 1
        if holdLookup { holdLookup = false; await withCheckedContinuation { held = $0 } }
        if let lookupError { throw lookupError }
        guard let value = posts.last?.0 ?? recovery else { throw DuckyError.unavailable(404) }
        return try decodeAnalysis(receiptJSON(value, scope: scope, status: status))
    }
    func close() {}
    func release() { held?.resume(); held = nil }
}

final class AnalysisProtocolTests: XCTestCase {
    func testTypedScopesCannotTurnUngroupedIntoOracle() throws {
        let a: AnalysisScope = try decodeAnalysis(["kind": "ungrouped"])
        XCTAssertEqual(a, .ungrouped); XCTAssertNotEqual(a, .oracle)
        for value in [["kind": "folder", "path": ""], ["kind": "ungrouped", "path": "Work"], ["kind": "oracle", "path": ""], ["kind": "all"], ["kind": "folder", "path": "Work", "host": "other"]] {
            XCTAssertThrowsError(try decodeAnalysis(value) as AnalysisScope)
        }
        XCTAssertEqual(try JSONDecoder().decode(AnalysisScope.self, from: JSONEncoder().encode(AnalysisScope.folder("Work/Sub"))), .folder("Work/Sub"))
    }
    func testAvailabilityRequiresFeatureScopeAndEffectiveEditionFlagIndependently() {
        XCTAssertTrue(analysisSnapshot().allowsAnalysis(.oracle))
        XCTAssertFalse(analysisSnapshot(oracle: false).allowsAnalysis(.oracle))
        XCTAssertTrue(analysisSnapshot(oracle: false).allowsAnalysis(.ungrouped))
        XCTAssertFalse(analysisSnapshot(features: ["conversations"]).allowsAnalysis(.folder("Work")))
        XCTAssertFalse(analysisSnapshot(capabilities: ["conversation:read", "session:send"]).allowsAnalysis(.oracle))
        XCTAssertFalse(analysisSnapshot(capabilities: ["analysis:folder"]).allowsAnalysis(.oracle))
        XCTAssertFalse(analysisSnapshot(folder: false).allowsAnalysis(.folder("Work")))
    }
    func testReceiptRejectsCrossScopeIdentityOversizeAndInvalidStatus() throws {
        let sent = submission()
        var value = receiptJSON(sent, status: "complete")
        let valid: AnalysisReceipt = try decodeAnalysis(value)
        try valid.validate(scope: .folder("Work"), submission: sent)
        XCTAssertThrowsError(try valid.validate(scope: .oracle, submission: sent))
        XCTAssertThrowsError(try valid.validate(scope: .folder("Work"), submission: submission()))
        for (key, replacement) in [("request_id", sent.requestID.uppercased()), ("context_id", String(repeating: "x", count: 129)), ("answer", String(repeating: "é", count: 16385)), ("question", String(repeating: "é", count: 2049))] {
            var changed = value; changed[key] = replacement
            XCTAssertThrowsError(try (decodeAnalysis(changed) as AnalysisReceipt).validate(scope: .folder("Work")))
        }
        value["status"] = "delivered"
        XCTAssertThrowsError(try decodeAnalysis(value) as AnalysisReceipt)
    }
    func testCompleteHistoryCanOutliveWorkDeadlineButCannotLeakOtherScope() throws {
        let sent = submission()
        let good: AnalysisContext = try decodeAnalysis(contextJSON(.folder("Work"), history: [receiptJSON(sent, status: "complete")]))
        try good.validate(hostID: analysisHost, scope: .folder("Work"))
        XCTAssertLessThan(good.history[0].expiresAt, analysisNow + 86_400_000)
        XCTAssertThrowsError(try good.validate(hostID: "another-host", scope: .folder("Work")))
        let crossed: AnalysisContext = try decodeAnalysis(contextJSON(.oracle, history: [receiptJSON(sent, status: "complete")]))
        XCTAssertThrowsError(try crossed.validate(hostID: analysisHost, scope: .oracle))
        let repeated: AnalysisContext = try decodeAnalysis(contextJSON(.folder("Work"), history: Array(repeating: receiptJSON(sent, status: "complete"), count: 2)))
        XCTAssertThrowsError(try repeated.validate(hostID: analysisHost, scope: .folder("Work")))
    }
    func testPendingRecoveryUsesOriginalContextAndBoundsHistory() throws {
        let sent = submission()
        let context: AnalysisContext = try decodeAnalysis(contextJSON(.ungrouped, pending: receiptJSON(sent, scope: .ungrouped)))
        try context.validate(hostID: analysisHost, scope: .ungrouped)
        XCTAssertNotEqual(context.contextID, context.pending?.contextID)
        let tooMany: AnalysisContext = try decodeAnalysis(contextJSON(.folder("Work"), history: (0..<21).map { _ in receiptJSON(submission(), status: "complete") }))
        XCTAssertThrowsError(try tooMany.validate(hostID: analysisHost, scope: .folder("Work")))
    }
    func testQuestionAndErrorDataNeverBecomeCommandOrProviderOutput() {
        XCTAssertTrue(AnalysisSubmission.validQuestion("/What does this mean?\n# A question")) // Analysis is not terminal input.
        XCTAssertFalse(AnalysisSubmission.validQuestion(" \n"))
        XCTAssertFalse(AnalysisSubmission.validQuestion("text\u{0}"))
        let error = AnalysisServiceError(status: 503, reason: "token=secret https://private.example")
        XCTAssertNil(error.reason); XCTAssertEqual(error.message, "Your Mac couldn’t answer this question.")
    }
}

@MainActor
final class AnalysisModelTests: XCTestCase {
    private func setup(now: @escaping () -> Date = { Date(timeIntervalSince1970: Double(analysisNow) / 1000) }) async -> (SessionModel, AnalysisModel, AnalysisFake, AnalysisStore) {
        let fake = AnalysisFake(), store = AnalysisStore()
        let session = SessionModel(credentials: store, makeTransport: { _ in fake })
        await session.restore()
        let model = AnalysisModel(session: session, makeTransport: { _ in fake }, now: now,
                                  pause: { try await Task.sleep(nanoseconds: 60_000_000_000) })
        await model.select(.folder("Work"))
        return (session, model, fake, store)
    }
    private func waitHeld(_ fake: AnalysisFake) async {
        for _ in 0..<100 where fake.held == nil { await Task.yield() }
        XCTAssertNotNil(fake.held)
    }
    func testQueuedThenCompletePollNeverCreatesAnotherQuestion() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("What changed?"); await model.ask()
        XCTAssertEqual(fake.posts.count, 1); XCTAssertEqual(model.receipt?.status, .queued)
        fake.status = "complete"; await model.poll()
        XCTAssertEqual(model.history.count, 1); XCTAssertNil(model.submission); XCTAssertFalse(model.busy)
        await model.poll(); XCTAssertEqual(fake.posts.count, 1)
    }
    func testUncertainPostOnlyReadsUntilExplicitRetryReusesExactUUID() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.postError = URLError(.timedOut); fake.lookupError = DuckyError.unavailable(404)
        model.setDraft("Keep these bytes\n unchanged"); await model.ask()
        let original = fake.posts[0].0
        await model.poll(); await model.poll()
        XCTAssertEqual(model.state, .unconfirmed); XCTAssertEqual(fake.posts.count, 1)
        fake.postError = nil; await model.recover()
        XCTAssertEqual(fake.posts.map { $0.0 }, [original, original])
    }
    func testContextRecoversPendingWithoutPostingAndUsesOwnOriginalContext() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.recovery = submission(); await model.load()
        XCTAssertEqual(model.submission, fake.recovery); XCTAssertTrue(fake.posts.isEmpty)
        fake.status = "complete"; await model.poll()
        XCTAssertEqual(model.history.first?.requestID, fake.recovery?.requestID)
        XCTAssertTrue(fake.posts.isEmpty)
    }
    func testSwitchScopeDuringPostCannotShowAnswerInAnotherFolder() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.status = "complete"; fake.holdPost = true
        model.setDraft("Question for Work")
        let task = Task { await model.ask() }; await waitHeld(fake)
        await model.select(.ungrouped); fake.release(); await task.value
        XCTAssertEqual(model.scope, .ungrouped); XCTAssertTrue(model.history.isEmpty)
        XCTAssertEqual(fake.posts.count, 1); XCTAssertTrue(model.busy)
    }
    func testForgetDuringContextReadCannotRestorePrivateAnswerOrDraft() async {
        let (session, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("private draft"); fake.holdContext = true
        let task = Task { await model.load() }; await waitHeld(fake)
        session.forget(); fake.release(); await task.value
        XCTAssertTrue(model.history.isEmpty); XCTAssertEqual(model.draft, ""); XCTAssertNil(model.submission)
    }
    func testGrantLossDuringLookupDiscardsLateAnswerAndClearsDraft() async {
        let (session, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("Question"); await model.ask(); model.setDraft("another draft")
        fake.status = "complete"; fake.holdLookup = true
        let task = Task { await model.poll() }; await waitHeld(fake)
        fake.snapshotValue = analysisSnapshot(folder: false); await session.refresh()
        fake.release(); await task.value
        XCTAssertTrue(model.history.isEmpty); XCTAssertEqual(model.draft, ""); XCTAssertFalse(model.canAsk)
    }
    func testStaleRetainsLastAnswerButDisablesNewQuestions() async {
        let (session, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.status = "complete"; model.setDraft("Question"); await model.ask()
        fake.snapshotError = URLError(.notConnectedToInternet); await session.refresh()
        model.synchronize(); model.setDraft("Another")
        XCTAssertEqual(model.history.count, 1); XCTAssertFalse(model.canAsk)
        await model.ask(); XCTAssertEqual(fake.posts.count, 1)
    }
    func testBackgroundStopsReadsAndPreservesScopeDraft() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("Question"); await model.ask(); model.setDraft("next draft")
        model.setForeground(false); await model.poll()
        XCTAssertEqual(fake.receiptCalls, 0); XCTAssertEqual(model.draft, "next draft")
        XCTAssertFalse(model.canAsk)
    }
    func testPendingQuestionBlocksDifferentScopeAndPreservesSeparateDrafts() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("Work question"); await model.ask()
        model.setDraft("Work later"); await model.select(.oracle); model.setDraft("Oracle later")
        XCTAssertFalse(model.canAsk); await model.ask(); XCTAssertEqual(fake.posts.count, 1)
        await model.select(.folder("Work")); XCTAssertEqual(model.draft, "Work later")
    }
    func testAuthoritativeAdmissionFailureDoesNotBecomeUncertainOrLoseDraft() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.postError = AnalysisServiceError(status: 429, reason: "queue_limit")
        model.setDraft("Question"); await model.ask()
        XCTAssertFalse(model.busy); XCTAssertEqual(model.draft, "Question")
        XCTAssertEqual(model.error, "Your Mac is answering other questions. Try again in a minute.")
    }
    func testLateQueuedPostCannotRegressAlreadyCompletedLookup() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.holdPost = true; model.setDraft("Question")
        let posting = Task { await model.ask() }; await waitHeld(fake)
        fake.status = "complete"; await model.poll()
        XCTAssertEqual(model.history.count, 1)
        fake.release(); await posting.value
        XCTAssertNil(model.receipt); XCTAssertNil(model.submission); XCTAssertFalse(model.busy)
        XCTAssertEqual(model.history.count, 1)
    }
    func testLateContextPendingCannotRegressCompletedReceipt() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("Question"); await model.ask(); fake.recovery = fake.posts[0].0
        fake.holdContext = true
        let loading = Task { await model.load() }; await waitHeld(fake)
        fake.status = "complete"; await model.poll(); fake.release(); await loading.value
        XCTAssertEqual(model.history.count, 1); XCTAssertNil(model.submission); XCTAssertFalse(model.busy)
    }
    func testUnrecognizedHTTPFailureKeepsOriginalRequestUncertain() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.postError = AnalysisServiceError(status: 503, reason: "raw provider failure and key")
        model.setDraft("Question"); await model.ask()
        XCTAssertEqual(model.state, .unconfirmed); XCTAssertNotNil(model.submission); XCTAssertEqual(fake.posts.count, 1)
    }
    func testPathologicalSingleGraphemeDraftIsByteBoundedAndNeverSentTruncated() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("e" + String(repeating: "\u{301}", count: 100_000))
        XCTAssertLessThanOrEqual(model.draft.utf8.count, 4100); XCTAssertFalse(model.canAsk)
        await model.ask(); XCTAssertTrue(fake.posts.isEmpty)
    }
    func testScopeChangedOnContextReadClearsPriorAnswerAndDraft() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.status = "complete"; model.setDraft("Question"); await model.ask()
        model.setDraft("private draft")
        fake.contextError = AnalysisServiceError(status: 409, reason: "scope_changed")
        await model.load()
        XCTAssertTrue(model.history.isEmpty); XCTAssertEqual(model.draft, "")
    }
    func testUnknownReasonForbiddenStillClearsPrivateData() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.status = "complete"; model.setDraft("Question"); await model.ask()
        fake.postError = AnalysisServiceError(status: 403, reason: nil)
        model.setDraft("New question"); await model.ask()
        XCTAssertEqual(model.state, .denied); XCTAssertTrue(model.history.isEmpty); XCTAssertEqual(model.draft, "")
    }
    func testCompletedHistoryResolvesLostReceiptWithoutAnotherPost() async {
        let (_, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.postError = URLError(.timedOut); model.setDraft("Question"); await model.ask()
        fake.completedHistory = [receiptJSON(fake.posts[0].0, status: "complete")]
        await model.load()
        XCTAssertEqual(model.history.count, 1); XCTAssertNil(model.submission); XCTAssertFalse(model.busy)
        XCTAssertEqual(fake.posts.count, 1); XCTAssertEqual(fake.receiptCalls, 0)
    }
    func testReapprovalRevisionClearsAnswersEvenWhenCapabilitiesAreUnchanged() async {
        let (session, model, fake, _) = await setup(); defer { model.setForeground(false) }
        fake.status = "complete"; model.setDraft("Question"); await model.ask(); model.setDraft("private draft")
        fake.snapshotValue = analysisSnapshot(revision: 1); await session.refresh(); model.synchronize()
        XCTAssertTrue(model.allowed); XCTAssertTrue(model.history.isEmpty); XCTAssertEqual(model.draft, "")
    }
    func testSameCapabilityReapprovalDuringHeldReplyDiscardsOldResultWithoutExplicitSync() async {
        let (session, model, fake, _) = await setup(); defer { model.setForeground(false) }
        model.setDraft("Question"); await model.ask()
        fake.holdLookup = true; fake.status = "complete"
        let reading = Task { await model.poll() }; await waitHeld(fake)
        fake.snapshotValue = analysisSnapshot(revision: 1); await session.refresh()
        fake.release(); await reading.value
        XCTAssertTrue(model.history.isEmpty); XCTAssertNil(model.submission)
    }
    func testWorkDeadlineStopsAutomaticReadsButExplicitRecoveryKeepsOriginalID() async {
        var clock = Date(timeIntervalSince1970: Double(analysisNow) / 1000)
        let (_, model, fake, _) = await setup(now: { clock }); defer { model.setForeground(false) }
        model.setDraft("Question"); await model.ask()
        let accepted = fake.posts[0].0
        clock.addTimeInterval(241); await model.poll()
        XCTAssertEqual(fake.receiptCalls, 0); XCTAssertEqual(fake.posts.count, 1); XCTAssertEqual(model.state, .unconfirmed)
        fake.status = "complete"; await model.recover()
        XCTAssertEqual(fake.posts.map { $0.0 }, [accepted, accepted]); XCTAssertEqual(model.history.count, 1)
    }
    func testRePairOnlyReusesTokenForSameHostAndCertificate() async throws {
        let fake = AnalysisFake(), store = AnalysisStore()
        var tokens: [String] = []
        let session = SessionModel(credentials: store, makeTransport: { host in tokens.append(host.token); return fake })
        await session.restore()
        func link(_ invitation: Invitation) throws -> String {
            let data = try JSONEncoder().encode(invitation)
            return "ducky://pair?invite=" + data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        }
        await session.pair(link: try link(testInvitation()), name: "Same name is irrelevant")
        XCTAssertEqual(tokens.count, 2); XCTAssertEqual(tokens.last, String(repeating: "t", count: 43))
        await session.pair(link: try link(testInvitation(pin: String(repeating: "c", count: 64))), name: "Same name is irrelevant")
        XCTAssertNotEqual(tokens.last, String(repeating: "t", count: 43))
    }
}
