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
    @Published public private(set) var statusRefreshing = false
    @Published public private(set) var retrying = false
    /// The Mac just stopped answering and quick retries are still running: say "reconnecting", not "offline".
    @Published public private(set) var reconnecting = false

    @Published public private(set) var selectedSessionID: String?
    @Published public private(set) var conversation: ConversationSnapshot?
    @Published public private(set) var conversationError: String?
    @Published public private(set) var conversationFresh = false
    @Published public private(set) var conversationAt: Date?
    @Published public private(set) var conversationRefreshing = false
    @Published public private(set) var receiptError: String?
    @Published private var receiptConfirmations: [String: Date] = [:]
    @Published private var staleReceipts: Set<String> = []
    // Keep send identity for safe recovery, but do not display bodies after an
    // explicit conversation denial until that session's read succeeds again.
    @Published private var deniedConversationDisplays: Set<String> = []
    @Published public private(set) var drafts: [String: String] = [:]
    @Published public private(set) var sends: [String: SendState] = [:]
    public enum SendState: Equatable {
        case sending, unconfirmed, acknowledged(MessageReceipt), rejected(String)
    }
    private var pendingMessages: [String: OutgoingMessage] = [:]
    private var sentTexts: [String: String] = [:]
    private var conversationPoller: Task<Void, Never>?
    private var conversationReadID: UUID?
    private var conversationInflight: Task<Void, Never>?
    private var retryTask: Task<Void, Never>?
    private var conversationAttempt = 0
    private var permissionGeneration = 0
    private var sendRefreshRequired = false
    private var selectionGeneration = 0
    private var foreground = true
    private let conversationPause: () async throws -> Void
    private let expansionStore: FolderExpansionStore

    private let credentials: CredentialStoring
    private let makeTransport: (PairedHost) throws -> DuckyTransport
    private let now: () -> Date
    private let pause: () async throws -> Void
    private let retryPause: () async throws -> Void
    private let onLocalNetwork: () -> Bool
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
                retryPause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 2_000_000_000) },
                onLocalNetwork: @escaping () -> Bool = { LocalNetworkPath.shared.available },
                expansionStore: FolderExpansionStore = FolderExpansionStore()) {
        self.credentials = credentials
        self.makeTransport = makeTransport
        self.now = now
        self.pause = pause
        self.conversationPause = conversationPause
        self.retryPause = retryPause
        self.onLocalNetwork = onLocalNetwork
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

    /// GET-only recovery. Multiple taps share status and reply reads; never replay a send.
    public func retryConnection() async {
        if let retryTask { await retryTask.value; return }
        let mine = generation, started = epoch
        let task = Task {
            guard mine == self.generation, started == self.epoch, self.foreground else { return }
            self.retrying = true
            let attempt = self.conversationAttempt
            await self.refresh()
            if mine == self.generation, started == self.epoch, self.foreground, self.phase == .live, self.conversationAttempt == attempt { await self.refreshConversation() }
        }
        retryTask = task
        await task.value
        if retryTask == task { retryTask = nil; retrying = false }
    }

    private func fetch() async {
        guard !Task.isCancelled, foreground, let transport, phase != .unpaired else { return }
        let mine = generation, started = epoch
        func current() -> Bool { mine == generation && started == epoch && foreground }
        let recovering = phase != .live
        statusRefreshing = true
        defer { if current() { statusRefreshing = false } }
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
            let accessChanged = snapshot.map {
                ($0.authorizationRevision ?? 0) != (next.authorizationRevision ?? 0) ||
                Set($0.capabilities ?? []) != Set(next.capabilities ?? [])
            } ?? false
            if accessChanged {
                permissionGeneration += 1
                invalidateConversationRead()
                conversation = nil; conversationAt = nil; conversationError = nil
                drafts = [:]; sends = [:]; pendingMessages = [:]; sentTexts = [:]
                deniedConversationDisplays = []
                receiptConfirmations = [:]; staleReceipts = []; receiptError = nil
            }
            snapshot = next
            if let id = selectedSessionID,
               !next.allows("conversation:read") || !next.sessions.contains(where: { $0.id == id && $0.canRead == true }) {
                conversation = nil; conversationAt = nil; conversationFresh = false
            }
            snapshotAt = now()
            sendRefreshRequired = false
            phase = .live
            error = nil
            statusRefreshing = false
            if recovering || accessChanged { await refreshConversation() }
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
            invalidateConversationRead()
            self.error = Self.unreachableError(error, onLocalNetwork: onLocalNetwork())
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
            var misses = 0
            while !Task.isCancelled {
                guard let self else { return }
                await self.refresh()
                guard !Task.isCancelled else { return }
                misses = self.phase == .stale ? misses + 1 : 0
                // One dropped poll on Wi-Fi is common; retry a few times before calling the Mac unreachable.
                self.reconnecting = (1...Self.quickRetries).contains(misses)
                do {
                    if self.reconnecting { try await self.retryPause() }
                    else if self.statusFollowsReplies { try await self.conversationPause() }
                    else { try await self.pause() }
                } catch { return }
            }
        }
    }

    static let quickRetries = 3

    /// While an open chat's agent is working or a send is unresolved, status follows the reply pace.
    private var statusFollowsReplies: Bool {
        guard let id = selectedSessionID else { return false }
        return selectedSession?.state == "busy" || pendingMessages[id] != nil
    }

    public func enterBackground() {
        foreground = false
        invalidateRefreshes()
        conversationPoller?.cancel(); conversationPoller = nil
        invalidateConversationRead()
    }

    public func clearNotices() { notices = [] }

    /// Why the Mac didn't answer, in terms the owner can act on. The phone can't tell a sleeping
    /// Mac from one on another network, so it names both.
    public nonisolated static func unreachableError(_ error: Error, onLocalNetwork: Bool) -> String {
        guard let error = error as? URLError,
              [.timedOut, .cannotConnectToHost, .networkConnectionLost, .cannotFindHost, .dnsLookupFailed, .notConnectedToInternet].contains(error.code)
        else { return refreshError(error) }
        if !onLocalNetwork { return "Your iPhone isn’t on Wi-Fi. Join the same Wi-Fi as your Mac." }
        return "Can’t reach your Mac. It may be asleep or on a different Wi-Fi."
    }

    public nonisolated static func refreshError(_ error: Error) -> String {
        if let error = error as? DuckyError { return error.localizedDescription }
        if let error = error as? URLError {
            switch error.code {
            case .timedOut: return "Connection timed out."
            case .notConnectedToInternet: return "Your iPhone is not connected to the internet."
            case .cannotFindHost, .dnsLookupFailed: return "Your Mac’s address could not be found."
            case .cannotConnectToHost, .networkConnectionLost: return "The connection to your Mac was lost."
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateHasUnknownRoot, .serverCertificateNotYetValid: return "The secure connection to your Mac could not be verified."
            default: return "Couldn’t connect to your Mac. Try again."
            }
        }
        return "Couldn’t refresh from your Mac. Try again."
    }
    public var agentActivityLabel: String {
        let value = selectedSession?.state == "busy" ? "Working" : selectedSession?.stateLabel ?? "Unknown"
        return phase == .live ? value : "Last seen " + value.lowercased()
    }
    public func receiptIsFresh(_ receipt: MessageReceipt) -> Bool {
        guard foreground, phase == .live, !staleReceipts.contains(receipt.id),
              let at = receiptConfirmations[receipt.id] else { return false }
        return now().timeIntervalSince(at) < 55
    }
    public func receiptLabel(_ receipt: MessageReceipt) -> String {
        let fresh = receiptIsFresh(receipt)
        let label = receipt.deliveryLabel(agentState: fresh ? selectedSession?.state : nil)
        let timestamp = receipt.status == "delivered" ? " · " + Date(timeIntervalSince1970: Double(receipt.updatedAt) / 1000).formatted(date: .omitted, time: .shortened) : ""
        return (fresh ? "" : "Last confirmed: ") + label + timestamp
    }
    public var selectedSession: AgentStatus? { snapshot?.sessions.first { $0.id == selectedSessionID } }
    public var draft: String { selectedSessionID.flatMap { drafts[$0] } ?? "" }
    public var sendState: SendState? { selectedSessionID.flatMap { sends[$0] } }
    public var conversationIsLoading: Bool {
        conversation == nil && phase == .live && conversationRefreshing && conversationError == nil
    }
    public var conversationReceipts: [MessageReceipt] {
        guard let selectedSessionID, !deniedConversationDisplays.contains(selectedSessionID) else { return [] }
        var receipts = conversation?.receipts ?? []
        if case .acknowledged(let receipt) = sendState,
           !receipts.contains(where: { $0.id == receipt.id }) {
            receipts.append(receipt)
        }
        var seen: Set<String> = []
        return receipts.filter { receipt in
            guard seen.insert(receipt.id).inserted else { return false }
            return receipt.status != "delivered" || messageText(for: receipt) != nil
        }
    }
    public var canEditDraft: Bool {
        guard phase == .live, foreground, !sendRefreshRequired, snapshot?.allows("session:send") == true,
              let selectedSession, selectedSession.isActive, selectedSession.runtime != "generic",
              selectedSession.canSend == true, currentSendContext != nil else { return false }
        return true
    }
    public var canSend: Bool {
        guard canEditDraft else { return false }
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
    public func messageText(for receipt: MessageReceipt) -> String? {
        guard !deniedConversationDisplays.contains(receipt.sessionID) else { return nil }
        for text in [receipt.text, sentTexts[receipt.requestID]].compactMap({ $0 }) {
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return text }
        }
        return nil
    }
    public func setDraft(_ text: String) {
        guard let selectedSessionID else { return }
        drafts[selectedSessionID] = text
    }
    public func selectSession(_ id: String?) {
        guard id != selectedSessionID else { return }
        conversationPoller?.cancel(); conversationPoller = nil
        selectionGeneration += 1; invalidateConversationRead()
        selectedSessionID = id; conversation = nil; conversationAt = nil; conversationError = nil; receiptError = nil; conversationFresh = false
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
        if let conversationInflight { await conversationInflight.value; return }
        let task = Task { await self.fetchConversation() }
        conversationInflight = task
        await task.value
        if conversationInflight == task { conversationInflight = nil }
    }
    private func fetchConversation() async {
        guard !Task.isCancelled, foreground, phase == .live, conversationReadID == nil,
              let transport, let hostID = host?.hostID, let id = selectedSessionID, selectedSession != nil else { return }
        let readID = UUID(), mine = generation, selection = selectionGeneration, started = epoch
        let permission = permissionGeneration
        conversationReadID = readID; conversationAttempt += 1; conversationRefreshing = true
        func current() -> Bool {
            conversationReadID == readID && !Task.isCancelled && mine == generation && selection == selectionGeneration && started == epoch && permission == permissionGeneration &&
            foreground && phase == .live && host?.hostID == hostID && selectedSessionID == id && selectedSession != nil
        }
        defer { if conversationReadID == readID { conversationReadID = nil; conversationRefreshing = false } }
        guard snapshot?.allows("conversation:read") == true, selectedSession?.canRead == true else {
            if snapshot?.allows("session:send") == true, let pending = pendingMessages[id], sends[id] != .sending {
                do {
                    let receipt = try await transport.receipt(requestID: pending.requestID, sessionID: id)
                    try receipt.validate(sessionID: id, requestID: pending.requestID)
                    guard current(), snapshot?.allows("session:send") == true else { return }
                    acceptReceipt(receipt, message: pending)
                } catch {
                    guard current() else { return }
                    staleReceipts.insert(pending.requestID); receiptError = Self.refreshError(error)
                    if case DuckyError.unavailable(403) = error { sendRefreshRequired = true }
                }
            }
            return
        }
        do {
            let next = try await transport.conversation(sessionID: id)
            try next.validate(hostID: hostID, sessionID: id)
            guard current(), snapshot?.allows("conversation:read") == true, selectedSession?.canRead == true else { return }
            deniedConversationDisplays.remove(id)
            let receipts = next.receipts.map { newerReceipt($0) }
            conversation = ConversationSnapshot(version: next.version, hostID: next.hostID, sessionID: next.sessionID,
                messages: next.messages, truncated: next.truncated, receipts: receipts, canSend: next.canSend,
                sendContext: next.sendContext, sendReason: next.sendReason)
            conversationFresh = true; conversationError = nil; conversationAt = now()
            for (incoming, receipt) in zip(next.receipts, receipts) {
                if incoming == receipt { confirm(receipt) } else { staleReceipts.insert(receipt.id) }
            }
            if let pending = pendingMessages[id] {
                if let receipt = receipts.first(where: { $0.requestID == pending.requestID }) {
                    acceptReceipt(receipt, message: pending, confirmed: next.receipts.contains(receipt))
                } else if sends[id] != .sending {
                    do {
                        let receipt = try await transport.receipt(requestID: pending.requestID, sessionID: id)
                        try receipt.validate(sessionID: id, requestID: pending.requestID)
                        guard current(), snapshot?.allows("session:send") == true else { return }
                        acceptReceipt(receipt, message: pending)
                    } catch {
                        guard current() else { return }
                        staleReceipts.insert(pending.requestID); receiptError = Self.refreshError(error)
                        if case DuckyError.unavailable(403) = error { sendRefreshRequired = true }
                    }
                }
            }
        } catch {
            guard current() else { return }
            conversationFresh = false
            for receipt in conversation?.receipts ?? [] { staleReceipts.insert(receipt.id) }
            if let pending = pendingMessages[id] { staleReceipts.insert(pending.requestID) }
            if case DuckyError.unavailable(403) = error {
                sendRefreshRequired = true
                deniedConversationDisplays.insert(id)
                conversation = nil
                conversationError = "Conversation access is unavailable. Check Ducky access on your Mac."
            } else if case DuckyError.unavailable(404) = error {
                sendRefreshRequired = true
                deniedConversationDisplays.insert(id)
                conversation = nil
                conversationError = "This session is no longer available."
            } else {
                conversationError = Self.refreshError(error)
            }
        }
    }
    public func sendMessage() async {
        guard canSend, let transport, let id = selectedSessionID, let hostID = host?.hostID,
              let context = currentSendContext, OutgoingMessage.validText(draft) else { return }
        let message = OutgoingMessage(sessionID: id, sendContext: context, text: draft)
        let mine = generation, permission = permissionGeneration
        pendingMessages[id] = message; sentTexts[message.requestID] = message.text; sends[id] = .sending
        do {
            let receipt = try await transport.send(message)
            try receipt.validate(sessionID: id, requestID: message.requestID)
            guard mine == generation, permission == permissionGeneration, host?.hostID == hostID, pendingMessages[id] == message else { return }
            // A conversation/receipt GET may already have confirmed this POST
            // while its response was delayed. A queued POST is an older observation.
            if receipt.isPending, case .acknowledged(let known) = sends[id], known.id == message.requestID { return }
            acceptReceipt(receipt, message: message)
        } catch {
            guard mine == generation, permission == permissionGeneration, host?.hostID == hostID, pendingMessages[id] == message else { return }
            if case .acknowledged(let known) = sends[id], known.id == message.requestID { return }
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
    private func newerReceipt(_ receipt: MessageReceipt) -> MessageReceipt {
        var result = receipt
        var known = conversation?.receipts.filter { $0.id == receipt.id } ?? []
        if case .acknowledged(let sent) = sends[receipt.sessionID], sent.id == receipt.id { known.append(sent) }
        for old in known where old.updatedAt > result.updatedAt || (!old.isPending && result.isPending) { result = old }
        return result
    }
    private func confirm(_ receipt: MessageReceipt) {
        receiptConfirmations[receipt.id] = now(); staleReceipts.remove(receipt.id); receiptError = nil
    }
    private func acceptReceipt(_ receipt: MessageReceipt, message: OutgoingMessage, confirmed: Bool = true) {
        // A late POST or receipt read cannot regress a newer terminal acknowledgment.
        guard newerReceipt(receipt) == receipt else { return }
        if confirmed { confirm(receipt) }
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
    private func invalidateConversationRead() {
        conversationInflight?.cancel(); conversationInflight = nil
        conversationReadID = nil; conversationRefreshing = false; conversationFresh = false
    }
    private func clearConversationState() {
        conversationPoller?.cancel(); conversationPoller = nil
        selectionGeneration += 1; invalidateConversationRead()
        selectedSessionID = nil; conversation = nil; conversationAt = nil; conversationFresh = false; conversationError = nil
        receiptConfirmations = [:]; staleReceipts = []; receiptError = nil; sendRefreshRequired = false
        deniedConversationDisplays = []
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
        reconnecting = false
    }

    private func invalidateRefreshes() {
        stop()
        epoch += 1
        inflight?.cancel(); inflight = nil; statusRefreshing = false
        retryTask?.cancel(); retryTask = nil; retrying = false
        invalidateConversationRead()
        if phase == .live { phase = .stale }
    }

    private func reset(to next: Phase) {
        stop()
        clearConversationState()
        generation += 1
        inflight?.cancel(); inflight = nil; statusRefreshing = false
        retryTask?.cancel(); retryTask = nil; retrying = false
        transport?.close()
        transport = nil
        host = nil; deviceID = nil
        snapshot = nil; snapshotAt = nil
        notices = []; tracker = NoticeTracker(); cursor = nil
        phase = next
        if next == .unpaired { error = nil }
    }
}
