import XCTest
@testable import DuckyCore

private let hostA = String(repeating: "a", count: 32)
private let hostB = String(repeating: "b", count: 32)
private let device = "3f9a1c27e84b06d5a2c9f1e07b4d8a63"

private func invitationLink(host: String, port: Int = 8765) -> String {
    let body: [String: Any] = ["version": 1, "host_id": host, "invite": String(repeating: "i", count: 43),
                               "expires_at": Int(Date().timeIntervalSince1970) + 600,
                               "url": "https://192.168.1.24:\(port)", "certificate_sha256": String(repeating: "c", count: 64)]
    let data = try! JSONSerialization.data(withJSONObject: body)
    let encoded = data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    return "ducky://pair?invite=" + encoded
}

private func paired(_ host: String) -> PairedHost {
    PairedHost(invitation: try! Invitation.parse(invitationLink(host: host)), token: String(repeating: "t", count: 43))
}

private func makeSnapshot(host: String = hostA, agents: [String] = ["discounts"], notices: [String] = [], cursor: String = "1", reset: Bool = false) -> HostSnapshot {
    let body: [String: Any] = [
        "version": 1, "host_id": host, "generated_at": 1, "read_only": true, "truncated": false, "cursor": cursor, "reset": reset,
        "folders": ["Checkout"], "notification_delivery": "foreground",
        "sessions": agents.map { ["id": $0, "name": $0, "folder": "Checkout", "runtime": "claude-code", "state": "busy", "observed_at": 1, "approval_visibility": "visible"] },
        "notices": notices.map { ["id": $0, "session_id": "discounts", "kind": "attention", "at": 1] },
    ]
    return try! JSONDecoder().decode(HostSnapshot.self, from: JSONSerialization.data(withJSONObject: body))
}

/// Scripted transport. A response can be held until the test releases it,
/// to deliver it late, after the model has moved on.
final class FakeTransport: DuckyTransport {
    let host: PairedHost
    var status = "active"
    var snapshots: [Result<HostSnapshot, Error>] = []
    var claims = 0, statusReads = 0, snapshotReads = 0, closed = false
    var claimError: Error?
    var hold: CheckedContinuation<Void, Never>?
    var holdNextSnapshot = false
    init(host: PairedHost) { self.host = host }
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus {
        claims += 1
        if let claimError { throw claimError }
        return try pairStatus("pending")
    }
    func pairingStatus() async throws -> PairStatus {
        statusReads += 1
        return try pairStatus(status)
    }
    func snapshot(cursor: String?) async throws -> HostSnapshot {
        snapshotReads += 1
        if holdNextSnapshot { holdNextSnapshot = false; await withCheckedContinuation { hold = $0 } }
        return try (snapshots.isEmpty ? .success(makeSnapshot(host: host.hostID)) : snapshots.removeFirst()).get()
    }
    func close() { closed = true }
    private func pairStatus(_ value: String) throws -> PairStatus {
        try JSONDecoder().decode(PairStatus.self, from: JSONSerialization.data(withJSONObject: ["id": device, "status": value, "host_id": host.hostID]))
    }
}

final class FakeStore: CredentialStoring {
    var saved: PairedHost?
    var forgets = 0
    var forgetError: Error?
    init(_ saved: PairedHost? = nil) { self.saved = saved }
    func save(_ host: PairedHost) throws { saved = host }
    func load() throws -> PairedHost? { saved }
    func forget() throws {
        forgets += 1
        if let forgetError { throw forgetError }
        saved = nil
    }
}

/// Lets a test step the poll loop one interval at a time.
final class Clock {
    var waiting: [CheckedContinuation<Void, Error>] = []
    func pause() async throws { try await withCheckedThrowingContinuation { waiting.append($0) } }
    func tick() { if !waiting.isEmpty { waiting.removeFirst().resume() } }
}

@MainActor
final class SessionModelTests: XCTestCase {
    var transports: [FakeTransport] = []
    var store = FakeStore()
    var firstSnapshots: [Result<HostSnapshot, Error>] = []
    var claimError: Error?
    let clock = Clock()

    private func model() -> SessionModel {
        SessionModel(credentials: store, makeTransport: { [unowned self] host in
            let next = FakeTransport(host: host)
            next.snapshots = firstSnapshots; next.claimError = claimError
            transports.append(next); return next
        }, pause: { [clock] in try await clock.pause() })
    }

    private func settle() async { for _ in 0..<20 { await Task.yield() } }

    func testRestoreWithoutCredentialStaysUnpairedAndMakesNoRequest() async {
        let m = model()
        await m.restore()
        XCTAssertEqual(m.phase, .unpaired)
        XCTAssertTrue(transports.isEmpty)
    }

    func testRestoreSavedHostReadsFullDeviceIDThenGoesLive() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        XCTAssertEqual(m.phase, .live)
        XCTAssertEqual(m.deviceID, device)
        XCTAssertEqual(m.host?.hostID, hostA)
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["discounts"])
        XCTAssertEqual(transports[0].statusReads, 1)
    }

    func testPairingWaitsForLocalApprovalBeforeAnySnapshotRead() async {
        let m = model()
        await m.pair(link: invitationLink(host: hostA), name: "Test iPhone")
        XCTAssertEqual(m.phase, .awaitingApproval)
        XCTAssertEqual(m.deviceID, device)
        XCTAssertEqual(store.saved?.hostID, hostA)
        transports[0].status = "pending"
        await m.refresh()
        XCTAssertEqual(m.phase, .awaitingApproval)
        XCTAssertEqual(transports[0].snapshotReads, 0)
        transports[0].status = "active" // `duckterm ducky approve <id>` ran on the Mac
        await m.refresh()
        XCTAssertEqual(m.phase, .live)
        XCTAssertNotNil(m.snapshot)
    }

    func testInvalidLinkSavesNothing() async {
        let m = model()
        await m.pair(link: "ducky://pair?invite=nope", name: "Test iPhone")
        XCTAssertEqual(m.phase, .unpaired)
        XCTAssertNil(store.saved)
        XCTAssertNotNil(m.error)
    }

    func testFailedRefreshKeepsLastSnapshotButNeverCallsItLive() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        let observed = m.snapshotAt
        transports[0].snapshots = [.failure(URLError(.notConnectedToInternet))]
        await m.refresh()
        XCTAssertEqual(m.phase, .stale)
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["discounts"])
        XCTAssertEqual(m.snapshotAt, observed)
        XCTAssertNotNil(m.error)
        await m.refresh()
        XCTAssertEqual(m.phase, .live)
    }

    func testRestoreWhileOfflineIsStaleWithNothingToShow() async {
        store = FakeStore(paired(hostA))
        firstSnapshots = [.failure(URLError(.timedOut))]
        let m = model()
        await m.restore()
        XCTAssertEqual(m.phase, .stale)
        XCTAssertEqual(m.deviceID, device)
        XCTAssertNil(m.snapshot)
    }

    func testDisabledBridgeKeepsPairingAndRecoversWhenEnabled() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        let credential = store.saved?.token
        transports[0].snapshots = [.failure(DuckyError.unavailable(503))]
        await m.refresh()
        XCTAssertEqual(m.phase, .stale)
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["discounts"])
        XCTAssertEqual(store.saved?.token, credential)
        XCTAssertTrue(m.error?.contains("enabled") == true)
        await m.refresh()
        XCTAssertEqual(m.phase, .live)
        XCTAssertEqual(transports.count, 1)
        XCTAssertEqual(transports[0].claims, 0)
        XCTAssertEqual(store.saved?.token, credential)
    }

    func testRevokedDeviceDropsItsSnapshot() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].snapshots = [.failure(DuckyError.unavailable(403))]
        await m.refresh()
        XCTAssertEqual(m.phase, .denied)
        XCTAssertNil(m.snapshot)
    }

    func testForegroundPollsAndBackgroundStops() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        m.enterForeground()
        await settle()
        XCTAssertEqual(transports[0].snapshotReads, 2) // restore + foreground refresh
        clock.tick(); await settle()
        XCTAssertEqual(transports[0].snapshotReads, 3)
        m.enterBackground()
        await settle()
        clock.tick(); await settle()
        XCTAssertEqual(transports[0].snapshotReads, 3)
    }

    func testNoticesAreDeliveredOnceAcrossRefreshes() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].snapshots = [.success(makeSnapshot(notices: ["n1"])), .success(makeSnapshot(notices: ["n1", "n2"]))]
        await m.refresh()
        XCTAssertEqual(m.notices.map(\.id), ["n1"])
        m.clearNotices()
        await m.refresh()
        XCTAssertEqual(m.notices.map(\.id), ["n2"])
    }

    func testForgetRemovesCredentialClosesTransportAndCancelsPolling() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        m.enterForeground(); await settle()
        let reads = transports[0].snapshotReads
        m.forget()
        XCTAssertEqual(m.phase, .unpaired)
        XCTAssertNil(m.deviceID); XCTAssertNil(m.snapshot); XCTAssertNil(m.host)
        XCTAssertNil(store.saved)
        XCTAssertTrue(transports[0].closed)
        clock.tick(); await settle()
        XCTAssertEqual(transports[0].snapshotReads, reads)
    }

    func testLateResponseAfterForgetCannotRepopulate() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].holdNextSnapshot = true
        let inFlight = Task { await m.refresh() }
        await settle()
        m.forget()
        transports[0].hold?.resume()
        await inFlight.value
        XCTAssertEqual(m.phase, .unpaired)
        XCTAssertNil(m.snapshot)
    }

    func testLateResponseFromPreviousHostCannotOverwriteNewPairing() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].holdNextSnapshot = true
        let inFlight = Task { await m.refresh() }
        await settle()
        await m.pair(link: invitationLink(host: hostB), name: "Test iPhone")
        transports[0].hold?.resume()
        await inFlight.value
        XCTAssertEqual(m.host?.hostID, hostB)
        XCTAssertEqual(m.phase, .awaitingApproval)
        XCTAssertNil(m.snapshot)
        XCTAssertTrue(transports[0].closed)
    }

    func testOverlappingRefreshesShareOneRequestAndApplyItOnce() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].snapshots = [.success(makeSnapshot(agents: ["held"])), .success(makeSnapshot(agents: ["later"]))]
        transports[0].holdNextSnapshot = true
        let timer = Task { await m.refresh() }
        await settle()
        let pull = Task { await m.refresh() }
        await settle()
        XCTAssertEqual(transports[0].snapshotReads, 2) // restore + the one shared refresh
        transports[0].hold?.resume()
        await timer.value; await pull.value
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["held"])
        await m.refresh()
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["later"])
    }

    func testBackgroundMarksLiveStaleAndDiscardsItsInFlightResponse() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        transports[0].snapshots = [.success(makeSnapshot(agents: ["late"]))]
        transports[0].holdNextSnapshot = true
        let inFlight = Task { await m.refresh() }
        await settle()
        m.enterBackground()
        XCTAssertEqual(m.phase, .stale)
        transports[0].hold?.resume()
        await inFlight.value
        XCTAssertEqual(m.phase, .stale)
        XCTAssertEqual(m.snapshot?.sessions.map(\.name), ["discounts"])
    }

    func testForegroundIsStaleUntilAFreshResponseArrives() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        m.enterBackground()
        transports[0].snapshots = [.failure(URLError(.timedOut))]
        m.enterForeground()
        XCTAssertEqual(m.phase, .stale)
        await settle()
        XCTAssertEqual(m.phase, .stale)
        clock.tick(); await settle()
        XCTAssertEqual(m.phase, .live)
    }

    func testForgetKeepsThePairingVisibleWhenKeychainKeepsTheCredential() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        store.forgetError = DuckyError.credentialStorage(-25300)
        m.forget()
        XCTAssertEqual(m.phase, .live)
        XCTAssertEqual(m.host?.hostID, hostA)
        XCTAssertNotNil(m.error)
        XCTAssertFalse(transports[0].closed)
        XCTAssertEqual(try store.load()?.hostID, hostA)
    }

    func testFailedClaimForAnotherMacLeavesTheCurrentOneRefreshing() async {
        store = FakeStore(paired(hostA))
        let m = model()
        await m.restore()
        m.enterForeground(); await settle()
        let reads = transports[0].snapshotReads
        claimError = DuckyError.unavailable(403)
        await m.pair(link: invitationLink(host: hostB), name: "Test iPhone")
        XCTAssertEqual(m.host?.hostID, hostA)
        XCTAssertEqual(m.phase, .live)
        XCTAssertNotNil(m.error)
        XCTAssertEqual(store.saved?.hostID, hostA)
        XCTAssertTrue(transports[1].closed)
        clock.tick(); await settle()
        XCTAssertEqual(transports[0].snapshotReads, reads + 1)
    }
}
