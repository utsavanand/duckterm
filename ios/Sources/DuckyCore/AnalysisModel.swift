import Combine
import Foundation

/// Only phone-created questions live here. Agent messages and desktop chat are unrelated.
@MainActor
public final class AnalysisModel: ObservableObject {
    @Published public private(set) var scope: AnalysisScope?
    @Published public private(set) var revision = 0
    public enum State: Equatable { case idle, loading, ready, submitting, unconfirmed, unavailable, denied }
    private struct Page {
        var context: AnalysisContext?
        var history: [AnalysisReceipt] = []
        var receipt: AnalysisReceipt?
        var submission: AnalysisSubmission?
        var pendingDeadline: Int64?
        var draft = ""
        var contentVersion = 0
        var state: State = .idle
        var error: String?
    }
    private let session: SessionModel
    private let makeTransport: (PairedHost) throws -> DuckyTransport
    private let now: () -> Date
    private let pause: () async throws -> Void
    private var transport: DuckyTransport?
    private var host: PairedHost?
    private var authorizationRevision: Int?
    private var pages: [AnalysisScope: Page] = [:]
    private var order: [AnalysisScope] = []
    private var generation = 0
    private var selection = 0
    private var foreground = true
    private var poller: Task<Void, Never>?
    private var observer: AnyCancellable?
    private var loading: UUID?
    private var polling: UUID?
    private var pollerID: UUID?

    public init(session: SessionModel, makeTransport: @escaping (PairedHost) throws -> DuckyTransport = { try DuckyClient(host: $0) },
                now: @escaping () -> Date = Date.init,
                pause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 2_000_000_000) }) {
        self.session = session; self.makeTransport = makeTransport; self.now = now; self.pause = pause
        observer = session.objectWillChange.sink { [weak self] in
            // Published values change after objectWillChange. Checks after every await also
            // read SessionModel directly, so no queued observer can admit a stale result.
            Task { @MainActor [weak self] in self?.synchronize() }
        }
    }
    public var state: State { scope.flatMap { pages[$0]?.state } ?? .idle }
    public var draft: String { scope.flatMap { pages[$0]?.draft } ?? "" }
    public var history: [AnalysisReceipt] { scope.flatMap { pages[$0]?.history } ?? [] }
    public var receipt: AnalysisReceipt? { scope.flatMap { pages[$0]?.receipt } }
    public var submission: AnalysisSubmission? { scope.flatMap { pages[$0]?.submission } }
    public var error: String? { scope.flatMap { pages[$0]?.error } }
    public var live: Bool { foreground && session.phase == .live }
    public var allowed: Bool { scope.map { session.snapshot?.allowsAnalysis($0) == true } ?? false }
    public var busy: Bool { pages.values.contains { $0.submission != nil || $0.receipt?.pending == true } }
    public var canAsk: Bool { live && allowed && !busy && state != .loading && state != .denied && AnalysisSubmission.validQuestion(draft) }
    public var canRecover: Bool { live && allowed && submission != nil && state == .unconfirmed }
    public var snapshot: HostSnapshot? { session.snapshot }

    public func synchronize() {
        if host != session.host {
            generation += 1; selection += 1
            poller?.cancel(); poller = nil; loading = nil; polling = nil
            transport?.close(); transport = nil; host = session.host; authorizationRevision = nil
            pages = [:]; order = []
            if let host { transport = try? makeTransport(host) }
        }
        if let snapshot = session.snapshot {
            let nextRevision = snapshot.authorizationRevision ?? 0
            if let authorizationRevision, authorizationRevision != nextRevision {
                generation += 1; selection += 1; loading = nil; polling = nil
                pages = [:]; order = []; poller?.cancel(); poller = nil
            }
            authorizationRevision = nextRevision
        }
        if session.phase == .denied || session.phase == .unpaired || session.phase == .awaitingApproval {
            generation += 1; loading = nil; polling = nil
            pages = [:]; order = []; poller?.cancel(); poller = nil
        } else if session.snapshot != nil {
            for key in Array(pages.keys) where session.snapshot?.allowsAnalysis(key) != true {
                pages[key] = Page(state: .denied)
            }
        }
        revision &+= 1
    }
    public func select(_ scope: AnalysisScope?) async {
        synchronize()
        guard self.scope != scope else {
            if let scope, pages[scope]?.context == nil { await load() }
            return
        }
        selection += 1; loading = nil
        self.scope = scope
        guard let scope else { return }
        if pages[scope] == nil { pages[scope] = Page() }
        order.removeAll { $0 == scope }; order.append(scope)
        // Keep four bounded histories; preserve the single accepted/uncertain job.
        while order.count > 4, let victim = order.first(where: { $0 != scope && pages[$0]?.submission == nil && pages[$0]?.receipt?.pending != true }) {
            order.removeAll { $0 == victim }; pages.removeValue(forKey: victim)
        }
        await load()
    }
    public func setDraft(_ value: String) {
        guard let scope, allowed else { return }
        // Bound retained input without changing the original bytes of an accepted question.
        pages[scope, default: Page()].draft = String(decoding: value.utf8.prefix(4097), as: UTF8.self)
        revision &+= 1
    }
    public func setForeground(_ value: Bool) {
        guard foreground != value else { return }
        foreground = value; selection += 1; loading = nil
        poller?.cancel(); poller = nil
        if value {
            synchronize(); startPolling()
            Task { [weak self] in await self?.load() }
        }
        revision &+= 1
    }
    private func current(_ expectedHost: PairedHost, scope: AnalysisScope, generation: Int, selection: Int? = nil) -> Bool {
        guard foreground, self.generation == generation, host == expectedHost, session.host == expectedHost,
              session.phase == .live, (session.snapshot?.authorizationRevision ?? 0) == authorizationRevision,
              session.snapshot?.allowsAnalysis(scope) == true else { return false }
        if let selection { return self.selection == selection && self.scope == scope }
        return true
    }
    public func load() async {
        synchronize()
        guard let scope, let host, let transport, live, allowed, loading == nil else { return }
        let mine = generation, selected = selection, id = UUID()
        let contentVersion = pages[scope]?.contentVersion ?? 0
        loading = id; pages[scope, default: Page()].state = .loading; revision &+= 1
        defer { if loading == id { loading = nil } }
        do {
            let context = try await transport.analysisContext(scope: scope)
            guard current(host, scope: scope, generation: mine, selection: selected) else { synchronize(); return }
            try context.validate(hostID: host.hostID, scope: scope)
            pages[scope, default: Page()].context = context
            if pages[scope]?.contentVersion == contentVersion {
                pages[scope, default: Page()].history = context.history
                if let submission = pages[scope]?.submission,
                   context.history.contains(where: { $0.requestID == submission.requestID && $0.contextID == submission.contextID && $0.question == submission.question }) {
                    pages[scope, default: Page()].submission = nil
                    pages[scope, default: Page()].receipt = nil
                }
                if let pending = context.pending {
                pages[scope, default: Page()].receipt = pending
                pages[scope, default: Page()].pendingDeadline = pending.expiresAt
                pages[scope, default: Page()].submission = AnalysisSubmission(requestID: pending.requestID, contextID: pending.contextID, question: pending.question)
                }
            }
            pages[scope, default: Page()].state = pages[scope]?.submission != nil && pages[scope]?.receipt == nil ? .unconfirmed : .ready
            pages[scope, default: Page()].error = nil
            revision &+= 1; startPolling()
        } catch {
            guard current(host, scope: scope, generation: mine, selection: selected) else { synchronize(); return }
            failure(error, scope: scope)
        }
    }
    public func ask() async {
        synchronize()
        guard canAsk, let scope else { return }
        let question = draft
        if pages[scope]?.context.map({ $0.expiresAt <= milliseconds }) ?? true { await load() }
        guard self.scope == scope, canAsk, draft == question, let context = pages[scope]?.context,
              context.expiresAt > milliseconds else { return }
        let submission = AnalysisSubmission(requestID: UUID().uuidString.lowercased(), contextID: context.contextID, question: question)
        pages[scope, default: Page()].submission = submission
        pages[scope, default: Page()].pendingDeadline = milliseconds + 240_000
        startPolling()
        pages[scope, default: Page()].draft = ""
        await submit(submission, scope: scope)
    }
    /// Only an explicit user retry may replay admission, with the identical UUID/body.
    public func recover() async {
        synchronize()
        guard canRecover, let scope, let submission else { return }
        await submit(submission, scope: scope)
    }
    private func submit(_ submission: AnalysisSubmission, scope: AnalysisScope) async {
        guard let host, let transport else { return }
        let mine = generation, selected = selection
        guard current(host, scope: scope, generation: mine, selection: selected) else { return }
        pages[scope, default: Page()].state = .submitting; pages[scope, default: Page()].error = nil; revision &+= 1
        do {
            let receipt = try await transport.askAnalysis(submission, scope: scope)
            guard current(host, scope: scope, generation: mine, selection: selected), pages[scope]?.submission == submission else { synchronize(); return }
            try receipt.validate(scope: scope, submission: submission)
            apply(receipt, scope: scope)
        } catch {
            guard current(host, scope: scope, generation: mine, selection: selected) else { synchronize(); return }
            guard pages[scope]?.submission == submission else { return }
            if let service = error as? AnalysisServiceError, service.status == 403 || Self.invalidatesContext(service.reason) {
                failure(service, scope: scope)
            } else if let service = error as? AnalysisServiceError, service.reason != nil, [400, 404, 409, 413, 429, 503].contains(service.status) {
                if pages[scope]?.receipt?.pending == true {
                    pages[scope, default: Page()].state = .unconfirmed; revision &+= 1
                } else {
                    pages[scope, default: Page()].submission = nil
                    pages[scope, default: Page()].state = .unavailable
                    pages[scope, default: Page()].draft = submission.question
                    pages[scope, default: Page()].context = nil
                    pages[scope, default: Page()].error = service.message
                    revision &+= 1
                }
            } else if case DuckyError.unavailable(403) = error { failure(error, scope: scope) }
            else { pages[scope, default: Page()].state = .unconfirmed; revision &+= 1 }
        }
        startPolling()
    }
    public func poll() async {
        synchronize()
        guard polling == nil, let host, let transport, live else { return }
        let readID = UUID(); polling = readID
        defer { if polling == readID { polling = nil } }
        let mine = generation
        for (scope, page) in pages {
            guard let submission = page.submission,
                  current(host, scope: scope, generation: mine) else { continue }
            // A timeout stops automatic reads; explicit recovery keeps the same identity.
            if let deadline = page.pendingDeadline, deadline < milliseconds {
                pages[scope, default: Page()].state = .unconfirmed; revision &+= 1; continue
            }
            do {
                let receipt = try await transport.analysisReceipt(requestID: submission.requestID, scope: scope)
                guard current(host, scope: scope, generation: mine), pages[scope]?.submission == submission else { synchronize(); return }
                try receipt.validate(scope: scope, submission: submission)
                apply(receipt, scope: scope)
            } catch {
                guard current(host, scope: scope, generation: mine) else { synchronize(); return }
                if let service = error as? AnalysisServiceError, service.status == 403 || Self.invalidatesContext(service.reason) { failure(error, scope: scope) }
                else if case DuckyError.unavailable(403) = error { failure(error, scope: scope) }
                else { pages[scope, default: Page()].state = .unconfirmed; revision &+= 1 }
            }
        }
    }
    private var milliseconds: Int64 { Int64(now().timeIntervalSince1970 * 1000) }
    private func apply(_ receipt: AnalysisReceipt, scope: AnalysisScope) {
        if pages[scope]?.history.contains(where: { $0.requestID == receipt.requestID }) == true {
            if pages[scope]?.submission?.requestID == receipt.requestID {
                pages[scope, default: Page()].submission = nil; pages[scope, default: Page()].receipt = nil
                pages[scope, default: Page()].state = .ready; revision &+= 1
            }
            return
        }
        if let prior = pages[scope]?.receipt, prior.requestID == receipt.requestID,
           prior.updatedAt > receipt.updatedAt || (!prior.pending && receipt.pending) { return }
        pages[scope, default: Page()].contentVersion &+= 1
        pages[scope, default: Page()].receipt = receipt
        pages[scope, default: Page()].pendingDeadline = receipt.expiresAt
        pages[scope, default: Page()].error = nil
        pages[scope, default: Page()].state = .ready
        if !receipt.pending {
            pages[scope, default: Page()].submission = nil
            if receipt.status == .complete {
                var history = pages[scope]?.history ?? []
                history.removeAll { $0.requestID == receipt.requestID }; history.append(receipt)
                pages[scope, default: Page()].history = Array(history.suffix(20))
                pages[scope, default: Page()].receipt = nil
            }
        }
        revision &+= 1
    }
    private static func invalidatesContext(_ reason: String?) -> Bool {
        ["authorization_changed", "configuration_changed", "scope_unavailable", "scope_changed", "permission_required", "disabled"].contains(reason ?? "")
    }
    private func failure(_ error: Error, scope: AnalysisScope) {
        if let service = error as? AnalysisServiceError, service.status == 403 || Self.invalidatesContext(service.reason) {
            pages[scope] = Page(state: service.status == 403 ? .denied : .unavailable, error: service.message)
        }
        else if case DuckyError.unavailable(403) = error { pages[scope] = Page(state: .denied) }
        else {
            pages[scope, default: Page()].state = .unavailable
            pages[scope, default: Page()].error = (error as? AnalysisServiceError)?.message ?? "Your Mac couldn’t answer this question."
        }
        revision &+= 1
    }
    private func startPolling() {
        guard foreground, poller == nil, busy else { return }
        let id = UUID(); pollerID = id
        poller = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do { try await self.pause() } catch { break }
                guard !Task.isCancelled else { break }
                await self.poll()
                if !self.pages.values.contains(where: { $0.submission != nil && ($0.pendingDeadline ?? 0) > self.milliseconds }) { break }
            }
            if self?.pollerID == id { self?.poller = nil; self?.pollerID = nil }
        }
    }
}
