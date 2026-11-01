import Combine
import Foundation

/// The three protocol routes the session model needs; DuckyClient is the real one.
public protocol DuckyTransport: AnyObject {
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus
    func pairingStatus() async throws -> PairStatus
    func snapshot(cursor: String?) async throws -> HostSnapshot
    func close()
}
extension DuckyClient: DuckyTransport {}

public protocol CredentialStoring {
    func save(_ host: PairedHost) throws
    func load() throws -> PairedHost?
    func forget() throws
}
extension CredentialStore: CredentialStoring {}

/// Pairing and refresh lifecycle for one paired Mac. Only the PairedHost
/// credential is persisted; snapshots and notices live in memory.
@MainActor
public final class SessionModel: ObservableObject {
    public enum Phase: Equatable {
        case unpaired
        /// Claimed; the Mac has not run `duckterm ducky approve` yet.
        case awaitingApproval
        /// The last refresh succeeded.
        case live
        /// The last refresh failed; `snapshot` is the last one observed, if any.
        case stale
        /// The Mac refused this device: revoked, or its approval request expired.
        case denied
    }

    @Published public private(set) var phase: Phase = .unpaired
    @Published public private(set) var host: PairedHost?
    /// The full device identifier, for approval and Settings.
    @Published public private(set) var deviceID: String?
    @Published public private(set) var snapshot: HostSnapshot?
    @Published public private(set) var snapshotAt: Date?
    /// Notices not shown before in this run; the UI clears them once shown.
    @Published public private(set) var notices: [Notice] = []
    @Published public private(set) var error: String?

    private let credentials: CredentialStoring
    private let makeTransport: (PairedHost) throws -> DuckyTransport
    private let now: () -> Date
    private let pause: () async throws -> Void
    private var transport: DuckyTransport?
    private var tracker = NoticeTracker()
    private var cursor: String?
    private var poller: Task<Void, Never>?
    // Every await checks this; forgetting or re-pairing makes older responses inert.
    private var generation = 0
    // Leaving or returning to the foreground invalidates refreshes but keeps the pairing.
    private var epoch = 0
    // Forgetting or a newer claim makes an earlier claim's answer inert.
    private var pairingAttempt = 0
    // Timer and pull-to-refresh share one request, so responses cannot apply out of order.
    private var inflight: Task<Void, Never>?

    public init(credentials: CredentialStoring = CredentialStore(),
                makeTransport: @escaping (PairedHost) throws -> DuckyTransport = { try DuckyClient(host: $0) },
                now: @escaping () -> Date = Date.init,
                pause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 15_000_000_000) }) {
        self.credentials = credentials
        self.makeTransport = makeTransport
        self.now = now
        self.pause = pause
    }

    /// Reconnects to the saved Mac, if any. Shows stale until the Mac answers.
    public func restore() async {
        let saved: PairedHost?
        do { saved = try credentials.load() } catch { self.error = error.localizedDescription; return }
        guard let saved else { reset(to: .unpaired); return }
        do { try connect(saved) } catch { self.error = error.localizedDescription; return }
        phase = .stale
        await refresh()
    }

    /// Claims an invitation link. The credential is saved only once the Mac accepts
    /// the claim; until then the current Mac, if any, keeps refreshing as before.
    public func pair(link: String, name: String) async {
        let paired: PairedHost
        let candidate: DuckyTransport
        let invitation: Invitation
        do {
            invitation = try Invitation.parse(link, now: now())
            paired = PairedHost(invitation: invitation, token: try DuckyClient.newCredential())
            candidate = try makeTransport(paired)
        } catch { self.error = error.localizedDescription; return }
        pairingAttempt += 1
        let mine = pairingAttempt
        do {
            let status = try await candidate.claim(invitation, name: name)
            guard mine == pairingAttempt else { candidate.close(); return }
            try credentials.save(paired)
            reset(to: .awaitingApproval)
            transport = candidate
            host = paired
            deviceID = status.id
            if status.status == "active" { await refresh() }
        } catch {
            candidate.close()
            if mine == pairingAttempt { self.error = error.localizedDescription }
        }
    }

    /// One read: pairing status while awaiting approval or before the ID is known,
    /// then the snapshot. A refresh during another waits for that one instead.
    public func refresh() async {
        if let inflight { await inflight.value; return }
        let task = Task { await self.fetch() }
        inflight = task
        await task.value
        if inflight == task { inflight = nil }
    }

    private func fetch() async {
        guard let transport, phase != .unpaired else { return }
        let mine = generation, started = epoch
        func current() -> Bool { mine == generation && started == epoch }
        do {
            if phase == .awaitingApproval || deviceID == nil {
                let status = try await transport.pairingStatus()
                guard current() else { return }
                deviceID = status.id
                guard status.status == "active" else { phase = .awaitingApproval; error = nil; return }
            }
            let next = try await transport.snapshot(cursor: cursor)
            guard current() else { return }
            cursor = next.cursor
            notices += tracker.newNotices(in: next)
            snapshot = next
            snapshotAt = now()
            phase = .live
            error = nil
        } catch DuckyError.unavailable(403) {
            guard current() else { return }
            // A revoked or expired device must not keep showing what it can no longer read.
            snapshot = nil; snapshotAt = nil
            phase = .denied
            error = DuckyError.unavailable(403).localizedDescription
        } catch {
            guard current() else { return }
            if phase != .awaitingApproval { phase = .stale }
            self.error = error.localizedDescription
        }
    }

    /// Refreshes now and keeps refreshing until the app leaves the foreground.
    /// What was shown before is stale until a fresh response arrives.
    public func enterForeground() {
        invalidateRefreshes()
        guard transport != nil else { return }
        poller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                do { try await self.pause() } catch { return }
            }
        }
    }

    public func enterBackground() { invalidateRefreshes() }

    public func clearNotices() { notices = [] }

    /// Removes this phone's credential and drops everything from that Mac. The
    /// Mac keeps its grant until `duckterm ducky revoke` runs there. If Keychain
    /// keeps the credential, the pairing stays visible: a relaunch would restore it.
    public func forget() {
        do { try credentials.forget() } catch { self.error = error.localizedDescription; return }
        pairingAttempt += 1
        reset(to: .unpaired)
    }

    private func connect(_ paired: PairedHost) throws {
        let next = try makeTransport(paired)
        reset(to: .unpaired)
        transport = next
        host = paired
    }

    private func stop() {
        poller?.cancel()
        poller = nil
    }

    private func invalidateRefreshes() {
        stop()
        epoch += 1
        inflight = nil
        if phase == .live { phase = .stale }
    }

    private func reset(to next: Phase) {
        stop()
        generation += 1
        inflight = nil
        transport?.close()
        transport = nil
        host = nil; deviceID = nil
        snapshot = nil; snapshotAt = nil
        notices = []; tracker = NoticeTracker(); cursor = nil
        phase = next
        if next == .unpaired { error = nil }
    }
}
