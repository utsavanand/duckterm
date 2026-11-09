import Combine
import Foundation

/// The three protocol routes the session model needs; DuckyClient is the real one.
public protocol DuckyTransport: AnyObject {
    func claim(_ invitation: Invitation, name: String) async throws -> PairStatus
    func pairingStatus() async throws -> PairStatus
    func snapshot(cursor: String?) async throws -> HostSnapshot
    func conversation(sessionID: String) async throws -> ConversationSnapshot
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt
    func analysisContext(scope: AnalysisScope) async throws -> AnalysisContext
    func askAnalysis(_ submission: AnalysisSubmission, scope: AnalysisScope) async throws -> AnalysisReceipt
    func analysisReceipt(requestID: String, scope: AnalysisScope) async throws -> AnalysisReceipt
    func close()
}
public extension DuckyTransport {
    func analysisContext(scope: AnalysisScope) async throws -> AnalysisContext { throw DuckyError.unavailable(404) }
    func askAnalysis(_ submission: AnalysisSubmission, scope: AnalysisScope) async throws -> AnalysisReceipt { throw DuckyError.unavailable(404) }
    func analysisReceipt(requestID: String, scope: AnalysisScope) async throws -> AnalysisReceipt { throw DuckyError.unavailable(404) }
    func conversation(sessionID: String) async throws -> ConversationSnapshot { throw DuckyError.unavailable(404) }
    func send(_ message: OutgoingMessage) async throws -> MessageReceipt { throw DuckyError.unavailable(404) }
    func receipt(requestID: String, sessionID: String) async throws -> MessageReceipt { throw DuckyError.unavailable(404) }
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

    @Published public private(set) var selectedSessionID: String?
    @Published public private(set) var conversation: ConversationSnapshot?
    @Published public private(set) var conversationError: String?
    @Published public private(set) var conversationFresh = false
    @Published public private(set) var drafts: [String: String] = [:]
    @Published public private(set) var sends: [String: SendState] = [:]
    public enum SendState: Equatable {
        case sending, unconfirmed, acknowledged(MessageReceipt), rejected(String)
    }
    private var pendingMessages: [String: OutgoingMessage] = [:]
    private var sentTexts: [String: String] = [:]
    private var conversationPoller: Task<Void, Never>?
    private var conversationReadID: UUID?
    private var selectionGeneration = 0
    private var foreground = true
    private let conversationPause: () async throws -> Void
    private let expansionStore: FolderExpansionStore

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
                pause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 15_000_000_000) },
                conversationPause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 4_000_000_000) },
                expansionStore: FolderExpansionStore = FolderExpansionStore()) {
        self.credentials = credentials
        self.makeTransport = makeTransport
        self.now = now
        self.pause = pause
        self.conversationPause = conversationPause
        self.expansionStore = expansionStore
    }

    public func makeAnalysisModel() -> AnalysisModel { AnalysisModel(session: self, makeTransport: makeTransport) }

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
            let existing = host ?? (try? credentials.load())
            let reuse = existing?.hostID == invitation.hostID && existing?.certificateSHA256 == invitation.certificateSHA256
            paired = PairedHost(invitation: invitation, token: reuse ? existing!.token : try DuckyClient.newCredential())
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
            if let id = selectedSessionID,
               !next.allows("conversation:read") || !next.sessions.contains(where: { $0.id == id && $0.canRead == true }) {
                conversation = nil; conversationFresh = false
            }
            snapshotAt = now()
            phase = .live
            error = nil
        } catch DuckyError.unavailable(403) {
            guard current() else { return }
            // A revoked or expired device must not keep showing what it can no longer read.
            snapshot = nil; snapshotAt = nil
            clearConversationState()
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
        foreground = true
        invalidateRefreshes()
        startConversationPolling()
        guard transport != nil else { return }
        poller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                do { try await self.pause() } catch { return }
            }
        }
    }

    public func enterBackground() {
        foreground = false
        invalidateRefreshes()
        conversationPoller?.cancel(); conversationPoller = nil
        conversationReadID = nil; conversationFresh = false
    }

    public func clearNotices() { notices = [] }

    public var selectedSession: AgentStatus? { snapshot?.sessions.first { $0.id == selectedSessionID } }
    public var draft: String { selectedSessionID.flatMap { drafts[$0] } ?? "" }
    public var sendState: SendState? { selectedSessionID.flatMap { sends[$0] } }
    public var canSend: Bool {
        guard phase == .live, foreground, snapshot?.allows("session:send") == true,
              let selectedSession, selectedSession.isActive, selectedSession.runtime != "generic",
              selectedSession.canSend == true, currentSendContext != nil else { return false }
        switch sendState {
        case .sending, .unconfirmed: return false
        case .acknowledged(let receipt) where receipt.status == "unknown": return false
        default: return true
        }
    }
    private var currentSendContext: String? {
        guard let snapshotAt, now().timeIntervalSince(snapshotAt) < 55 else { return nil }
        if conversationFresh, let conversation {
            return conversation.canSend ? conversation.sendContext : nil
        }
        return selectedSession?.sendContext.flatMap { $0.isEmpty ? nil : $0 }
    }
    public func messageText(for receipt: MessageReceipt) -> String? { receipt.text ?? sentTexts[receipt.requestID] }
    public func setDraft(_ text: String) {
        guard let selectedSessionID else { return }
        drafts[selectedSessionID] = text
    }
    public func selectSession(_ id: String?) {
        guard id != selectedSessionID else { return }
        conversationPoller?.cancel(); conversationPoller = nil
        selectionGeneration += 1; conversationReadID = nil
        selectedSessionID = id; conversation = nil; conversationError = nil; conversationFresh = false
        startConversationPolling()
    }
    private func startConversationPolling() {
        conversationPoller?.cancel(); conversationPoller = nil
        guard foreground, selectedSessionID != nil, transport != nil else { return }
        conversationPoller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.refreshConversation()
                do { try await self.conversationPause() } catch { return }
            }
        }
    }
    public func refreshConversation() async {
        guard foreground, phase == .live, conversationReadID == nil,
              let transport, let hostID = host?.hostID, let id = selectedSessionID, selectedSession != nil else { return }
        let readID = UUID(), mine = generation, selection = selectionGeneration, started = epoch
        conversationReadID = readID
        func current() -> Bool {
            mine == generation && selection == selectionGeneration && started == epoch &&
            foreground && host?.hostID == hostID && selectedSessionID == id && selectedSession != nil
        }
        defer { if conversationReadID == readID { conversationReadID = nil } }
        guard snapshot?.allows("conversation:read") == true, selectedSession?.canRead == true else {
            if snapshot?.allows("session:send") == true, let pending = pendingMessages[id], sends[id] != .sending {
                do {
                    let receipt = try await transport.receipt(requestID: pending.requestID, sessionID: id)
                    try receipt.validate(sessionID: id, requestID: pending.requestID)
                    guard current(), snapshot?.allows("session:send") == true else { return }
                    acceptReceipt(receipt, message: pending)
                } catch { /* No conversation yet; the send receipt is independently readable. */ }
            }
            return
        }
        do {
            let next = try await transport.conversation(sessionID: id)
            try next.validate(hostID: hostID, sessionID: id)
            guard current(), snapshot?.allows("conversation:read") == true, selectedSession?.canRead == true else { return }
            conversation = next; conversationFresh = true; conversationError = nil
            if let pending = pendingMessages[id] {
                if let receipt = next.receipts.first(where: { $0.requestID == pending.requestID }) {
                    acceptReceipt(receipt, message: pending)
                } else if sends[id] != .sending {
                    do {
                        let receipt = try await transport.receipt(requestID: pending.requestID, sessionID: id)
                        try receipt.validate(sessionID: id, requestID: pending.requestID)
                        guard current() else { return }
                        acceptReceipt(receipt, message: pending)
                    } catch { /* Unknown outcome stays blocked. Never replay the POST. */ }
                }
            }
        } catch {
            guard current() else { return }
            conversationFresh = false
            if case DuckyError.unavailable(403) = error {
                conversation = nil
                conversationError = "Conversation access is unavailable. Check Ducky access on your Mac."
            } else if case DuckyError.unavailable(404) = error {
                conversation = nil
                conversationError = "This session is no longer available."
            } else {
                conversationError = "Conversation unavailable. Pull to retry."
            }
        }
    }
    public func sendMessage() async {
        guard canSend, let transport, let id = selectedSessionID, let hostID = host?.hostID,
              let context = currentSendContext, OutgoingMessage.validText(draft) else { return }
        let message = OutgoingMessage(sessionID: id, sendContext: context, text: draft)
        let mine = generation
        pendingMessages[id] = message; sentTexts[message.requestID] = message.text; sends[id] = .sending
        do {
            let receipt = try await transport.send(message)
            try receipt.validate(sessionID: id, requestID: message.requestID)
            guard mine == generation, host?.hostID == hostID, pendingMessages[id] == message else { return }
            acceptReceipt(receipt, message: message)
        } catch {
            guard mine == generation, host?.hostID == hostID, pendingMessages[id] == message else { return }
            if case DuckyError.localCommandUnavailable = error {
                pendingMessages[id] = nil
                sends[id] = .rejected(error.localizedDescription)
            } else if case DuckyError.unavailable(let status) = error, [400, 403, 404, 409, 413, 429, 503].contains(status) {
                pendingMessages[id] = nil
                let text: String
                switch status {
                case 403: text = "Messaging access is unavailable. Check Ducky access on your Mac."
                case 404: text = "This session is no longer available."
                case 409: text = "The session changed. Refresh before sending."
                case 429: text = "Too many messages. Wait before sending again."
                case 503: text = "Messaging is unavailable. Refresh before sending."
                default: text = "Message not accepted. Check the text and try again."
                }
                sends[id] = .rejected(text)
                if selectedSessionID == id { conversationFresh = false }
            } else { sends[id] = .unconfirmed }
        }
    }
    private func acceptReceipt(_ receipt: MessageReceipt, message: OutgoingMessage) {
        sends[message.sessionID] = .acknowledged(receipt)
        if OutgoingMessage.normalize(drafts[message.sessionID] ?? "") == message.text { drafts[message.sessionID] = "" }
        if !receipt.isPending && receipt.status != "unknown" { pendingMessages[message.sessionID] = nil }
    }
    /// A new send is an explicit decision after an uncertain terminal delivery.
    public func beginNewMessage() {
        guard let id = selectedSessionID else { return }
        if case .acknowledged(let receipt) = sends[id], receipt.status == "unknown" {
            pendingMessages[id] = nil; sends[id] = nil; drafts[id] = ""
        }
    }
    private func clearConversationState() {
        conversationPoller?.cancel(); conversationPoller = nil
        selectionGeneration += 1; conversationReadID = nil
        selectedSessionID = nil; conversation = nil; conversationFresh = false; conversationError = nil
        drafts = [:]; sends = [:]; pendingMessages = [:]; sentTexts = [:]
    }

    /// Removes this phone's credential and drops everything from that Mac. The
    /// Mac keeps its grant until `duckterm ducky revoke` runs there. If Keychain
    /// keeps the credential, the pairing stays visible: a relaunch would restore it.
    public func forget() {
        let oldHost = host?.hostID
        do { try credentials.forget() } catch { self.error = error.localizedDescription; return }
        if let oldHost { expansionStore.forget(hostID: oldHost) }
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
        clearConversationState()
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
