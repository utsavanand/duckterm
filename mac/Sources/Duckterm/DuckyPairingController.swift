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
final class DuckyPairingController: NSObject, NSWindowDelegate {
    let panel: NSPanel
    let model: DuckyPairingModel
    var onClose: (() -> Void)?
    private var timer: Timer?
    private var shown = false
    private let icon: NSImage?
    private var lastState: DuckyPairingModel.State?

    init(prepare: @escaping () async throws -> Void) {
        let authentication = DuckyPairingAuthentication()
        model = DuckyPairingModel(makeTransport: {
            DuckyPairingProcess(command: {
                try await prepare()
                return try await DuckyPairingProcess.bundledCommand()
            })
        }, authenticate: { await authentication.authenticate(reason: $0) }, cancelAuthentication: { authentication.cancel() })
        panel = DuckyPairingPanel(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
        icon = NSApp.applicationIconImage
        super.init()
        panel.title = "Pair iPhone"
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true
        panel.level = .normal
        panel.delegate = self
        panel.setAccessibilitySubrole(.dialog)
        panel.setAccessibilityLabel("Pair iPhone")
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
        model.close(); panel.orderOut(nil)
        onClose?(); onClose = nil
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { close(); return false }
    private func render() {
        guard model.state != .closed else {
            if timer != nil { close() }
            return
        }
        let previous = panel.frame
        let focusedID = (panel.firstResponder as? NSButton)?.identifier
        let view = DuckyPairingView(state: model.state, busy: model.busy, icon: icon)
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
            case .cancel, .done: self.close()
            }
        }
        panel.setContentSize(view.frame.size); panel.contentView = view
        if shown { panel.setFrameOrigin(NSPoint(x: previous.midX - panel.frame.width / 2, y: previous.midY - panel.frame.height / 2)) }
        panel.initialFirstResponder = view.preferredFocus
        // Approve is never a default/Return action. Preserve tab selection across countdown ticks.
        if panel.isKeyWindow {
            let retained = lastState == model.state ? view.subviews.first { $0.identifier == focusedID && focusedID != nil } : nil
            panel.makeFirstResponder(retained ?? view.preferredFocus)
        }
        panel.defaultButtonCell = nil
        lastState = model.state
    }
}

private final class DuckyPairingPanel: NSPanel {
    var onCancel: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
    override func cancelOperation(_ sender: Any?) { onCancel?() }
    override func performClose(_ sender: Any?) { onCancel?() }
}
