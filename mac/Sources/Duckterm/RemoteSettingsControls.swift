import AppKit

/// Both native cloud entry points use the same server-verified capability.
/// Hide them before the first successful edition response and on read failure.
@MainActor
final class RemoteSettingsControls {
    let menuItem: NSMenuItem
    private weak var window: NSWindow?
    private var accessory: NSTitlebarAccessoryViewController?
    private var allowed = false

    init() {
        menuItem = NSMenuItem(title: "Settings…", action: NSSelectorFromString("showSettings:"), keyEquivalent: ",")
        menuItem.isHidden = true
        menuItem.isEnabled = false
    }

    func attach(to window: NSWindow) {
        removeAccessory()
        self.window = window
        update(allowed: allowed)
    }

    func update(allowed: Bool) {
        self.allowed = allowed
        menuItem.isHidden = !allowed
        menuItem.isEnabled = allowed
        guard allowed, let window else { removeAccessory(); return }
        guard accessory == nil else { return }
        let button = NSButton(title: "Settings…", target: nil, action: NSSelectorFromString("showSettings:"))
        button.bezelStyle = .rounded
        button.frame = NSRect(x: 0, y: 2, width: 100, height: 26)
        button.toolTip = "Manage remote computers"
        let control = NSTitlebarAccessoryViewController()
        control.layoutAttribute = .right
        control.view = NSView(frame: NSRect(x: 0, y: 0, width: 110, height: 30))
        control.view.addSubview(button)
        window.addTitlebarAccessoryViewController(control)
        accessory = control
    }

    private func removeAccessory() {
        if let window, let accessory,
           let index = window.titlebarAccessoryViewControllers.firstIndex(where: { $0 === accessory }) {
            window.removeTitlebarAccessoryViewController(at: index)
        }
        accessory = nil
    }
}
