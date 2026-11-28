import AppKit
import LocalAuthentication

@MainActor
final class DuckyPairingAuthentication {
    private var context: LAContext?
    func authenticate(reason: String) async -> Bool {
        let context = LAContext()
        self.context = context
        defer { if self.context === context { self.context = nil } }
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: nil) else { return false }
        return (try? await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)) == true
    }
    func cancel() { context?.invalidate(); context = nil }
}

@MainActor
protocol DuckyPairingPresenting: AnyObject {
    var onClose: (() -> Void)? { get set }
    var isKeyWindow: Bool { get }
    func show()
    func close()
    /// The account under the QR code changed.
    func refresh()
}

extension DuckyPairingPresenting {
    func refresh() {}
}

@MainActor
final class DuckyPairingController: NSObject, NSWindowDelegate, DuckyPairingPresenting {
    let panel: NSPanel
    var isKeyWindow: Bool { panel.isKeyWindow }
    let model: DuckyPairingModel
    var onClose: (() -> Void)?
    private var timer: Timer?
    private var shown = false
    private let icon: NSImage?
    private var lastState: DuckyPairingModel.State?
    /// Set for the window that allows one phone waiting on the owner's account.
    let claimID: String?
    private let account: () -> DuckyAccountStatus?
    private var signIn: DuckySignInModel?

    init(verifiedEdition: @escaping () -> DesktopEdition? = { nil }, claim claimID: String? = nil,
         account: @escaping () -> DuckyAccountStatus? = { nil }, prepare: @escaping () async throws -> Void) {
        let authentication = DuckyPairingAuthentication()
        let arguments = claimID.map { ["ducky", "claim", $0, "--json"] } ?? ["ducky", "pair", "--json"]
        model = DuckyPairingModel(makeTransport: {
            DuckyPairingProcess(command: {
                try await prepare()
                return try await DuckyPairingProcess.bundledCommand(arguments)
            })
        }, authenticate: { await authentication.authenticate(reason: $0) }, cancelAuthentication: { authentication.cancel() },
        verifiedEdition: verifiedEdition, expectsInvitation: claimID == nil)
        panel = DuckyPairingPanel(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
        icon = NSApp.applicationIconImage
        self.claimID = claimID; self.account = account
        super.init()
        let title = claimID == nil ? "Pair iPhone" : "Allow iPhone"
        panel.title = title
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true
        panel.level = .normal
        panel.delegate = self
        panel.setAccessibilitySubrole(.dialog)
        panel.setAccessibilityLabel(title)
        (panel as? DuckyPairingPanel)?.onCancel = { [weak self] in self?.close() }
        model.onChange = { [weak self] in self?.render() }
        render()
    }
    func show() {
        if !shown {
            shown = true; panel.center()
            model.start()
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    self.model.tick()
                    (self.panel.contentView as? DuckyPairingView)?.updateCountdown()
                }
            }
        }
        panel.makeKeyAndOrderFront(nil) // Only an explicit menu action raises the window.
    }
    func close() {
        timer?.invalidate(); timer = nil
        let signingIn = signIn
        signIn = nil; signingIn?.onChange = nil; signingIn?.close()
        model.close(); panel.orderOut(nil)
        onClose?(); onClose = nil
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { close(); return false }
    func refresh() {
        if case .invitation = model.state, signIn == nil { render() }
    }
    private func startSignIn() {
        let next = DuckySignInModel(makeTransport: {
            DuckyPairingProcess(command: { try await DuckyPairingProcess.bundledCommand(["ducky", "signin", "--json"]) })
        })
        next.onChange = { [weak self] in self?.render() }
        next.onCode = { NSWorkspace.shared.open($0) }
        // Signing in replaces the QR code; its invitation is withdrawn.
        signIn = next
        model.close()
        next.start()
    }
    private func render() {
        if let signIn {
            let view = DuckyPairingView(signIn: signIn.state, icon: icon)
            view.onAction = { [weak self, weak signIn] action in
                guard let self, let signIn else { return }
                switch action {
                case .copy:
                    guard case .code(let code, _) = signIn.state else { return }
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                case .restart: signIn.start()
                default: self.close()
                }
            }
            present(view)
            return
        }
        guard model.state != .closed else {
            if timer != nil { close() }
            return
        }
        let view = DuckyPairingView(state: model.state, busy: model.busy, allowsConversations: model.allowsConversations,
                                   allowsFolderQuestions: model.allowsFolderQuestions, allowsOracleQuestions: model.allowsOracleQuestions,
                                   availability: model.availability, account: account(), claim: claimID != nil, icon: icon)
        view.onConversationsChanged = { [weak self] allowed in self?.model.setAllowsConversations(allowed) }
        view.onFolderQuestionsChanged = { [weak self] allowed in self?.model.setAllowsFolderQuestions(allowed) }
        view.onOracleQuestionsChanged = { [weak self] allowed in self?.model.setAllowsOracleQuestions(allowed) }
        view.onAction = { [weak self] action in
            guard let self else { return }
            switch action {
            case .copy:
                guard case .invitation(let invitation) = self.model.state,
                      Double(invitation.expiresAt) > Date().timeIntervalSince1970 else { self.model.tick(); return }
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(invitation.link, forType: .string)
            case .approve: self.model.approve()
            case .deny: self.model.deny()
            case .restart: self.model.start()
            case .signIn: self.startSignIn()
            case .cancel, .done: self.close()
            }
        }
        present(view)
        lastState = model.state
    }
    private func present(_ view: DuckyPairingView) {
        let previous = panel.frame
        let focusedID = (panel.firstResponder as? NSButton)?.identifier
        panel.setContentSize(view.frame.size); panel.contentView = view
        if shown { panel.setFrameOrigin(NSPoint(x: previous.midX - panel.frame.width / 2, y: previous.midY - panel.frame.height / 2)) }
        panel.initialFirstResponder = view.preferredFocus
        // Approve is never a default/Return action. Preserve tab selection across countdown ticks.
        if panel.isKeyWindow {
            let retained = lastState == model.state ? view.subviews.first { $0.identifier == focusedID && focusedID != nil } : nil
            panel.makeFirstResponder(retained ?? view.preferredFocus)
        }
        panel.defaultButtonCell = nil
    }
}

private final class DuckyPairingPanel: NSPanel {
    var onCancel: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
    override func cancelOperation(_ sender: Any?) { onCancel?() }
    override func performClose(_ sender: Any?) { onCancel?() }
}
