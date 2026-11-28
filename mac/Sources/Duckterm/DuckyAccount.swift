import AppKit

/// DuckTerm account screens, behind the owner-only `ducky_remote` flag. The same
/// owner-only `ducky` process transport as pairing: signing in shows GitHub's
/// device code, and a watcher reports phones on the account waiting for this Mac.
enum DuckyAccount {
    static let feature = "ducky_remote"
    /// GitHub logins: 1-39 letters, digits or single hyphens.
    static func validLogin(_ login: String) -> Bool {
        login.range(of: "^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}\\z", options: .regularExpression) != nil
    }
}

/// What the Pair iPhone window shows under the QR code.
enum DuckyAccountStatus: Equatable {
    case signedOut, signedIn(String)
}

@MainActor
final class DuckySignInModel {
    enum State: Equatable { case opening, code(String, URL), signedIn(String), error(String), closed }
    private(set) var state: State = .opening
    var onChange: (() -> Void)?
    /// Opens GitHub's device page once the code is known.
    var onCode: ((URL) -> Void)?
    private let makeTransport: () -> DuckyPairingTransport
    private var transport: DuckyPairingTransport?
    private var generation = 0

    init(makeTransport: @escaping () -> DuckyPairingTransport) { self.makeTransport = makeTransport }

    func start() {
        stop()
        generation += 1
        let current = generation
        state = .opening
        let next = makeTransport()
        transport = next
        next.onEvent = { [weak self] event in
            guard let self, self.generation == current else { return }
            self.receive(event)
        }
        onChange?()
        Task { [weak self, weak next] in
            do { try await next?.start() }
            catch {
                guard let self, self.generation == current, self.state != .closed else { return }
                self.state = .error("service_unavailable"); self.stop(); self.onChange?()
            }
        }
    }

    func receive(_ event: DuckyPairingEvent) {
        switch (state, event) {
        case (.opening, .signInCode(let code, let url)):
            state = .code(code, url); onCode?(url)
        case (.code, .signedIn(let login)):
            state = .signedIn(login); stop()
        case (.closed, _): return
        case (_, .error(let code)):
            state = .error(code); stop()
        case (_, .cancelled):
            close(); return
        default:
            state = .error("service_unavailable"); stop()
        }
        onChange?()
    }

    func close() {
        generation += 1; state = .closed; stop(); onChange?()
    }

    private func stop() {
        transport?.onEvent = nil; transport?.cancel(); transport = nil
    }
}

/// Keeps `ducky claims --json` running while the flag is on, and reports the
/// account and the phones waiting so the app can post one notification each.
@MainActor
final class DuckyAccountWatcher {
    private(set) var status: DuckyAccountStatus?
    private(set) var waiting: [DuckyPairingDevice] = []
    var onChange: (() -> Void)?
    private let makeTransport: () -> DuckyPairingTransport
    private var transport: DuckyPairingTransport?
    private var generation = 0
    private var restart: Task<Void, Never>?

    init(makeTransport: @escaping () -> DuckyPairingTransport) { self.makeTransport = makeTransport }

    var running: Bool { transport != nil || restart != nil }

    func start() {
        guard transport == nil else { return }
        restart?.cancel(); restart = nil
        generation += 1
        let current = generation
        let next = makeTransport()
        transport = next
        next.onEvent = { [weak self] event in
            guard let self, self.generation == current else { return }
            self.receive(event)
        }
        Task { [weak self, weak next] in
            do { try await next?.start() }
            catch {
                guard let self, self.generation == current else { return }
                self.ended()
            }
        }
    }

    func receive(_ event: DuckyPairingEvent) {
        guard case .claims(let login, let devices) = event else { ended(); return }
        status = login.map(DuckyAccountStatus.signedIn) ?? .signedOut
        waiting = devices
        onChange?()
    }

    func stop() {
        generation += 1
        restart?.cancel(); restart = nil
        transport?.onEvent = nil; transport?.cancel(); transport = nil
        status = nil; waiting = []
        onChange?()
    }

    /// The process ended on its own (a crash, a bad frame): try again in a minute
    /// rather than spinning, and show nothing stale meanwhile.
    private func ended() {
        transport?.onEvent = nil; transport?.cancel(); transport = nil
        status = nil; waiting = []
        onChange?()
        let current = generation
        restart = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 60_000_000_000)
            guard let self, !Task.isCancelled, self.generation == current else { return }
            self.restart = nil
            self.start()
        }
    }
}
