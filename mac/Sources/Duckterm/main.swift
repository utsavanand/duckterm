import AppKit
import UserNotifications

/// Standalone desktop app: double-click to launch, opens its own window with the
/// dashboard (an embedded web view — never your browser). Owns the local server
/// process, shows native notifications when a session needs you, and quits when
/// you close the window.
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let server = ServerProcess()
    private var poller: SessionPoller?
    private var window: DashboardWindow?
    private var bugReport: BugReportController?
    private var capturingReport = false
    private var notified = Set<String>()  // waiting keys we've already alerted on

    func applicationDidFinishLaunching(_ note: Notification) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        AppDiagnostics.shared.record("Application launched")
        window = DashboardWindow(url: server.url)
        window?.show()  // open the dashboard window on launch
        NSApp.activate(ignoringOtherApps: true)

        Task {
            _ = await server.start()  // start the server, or attach to a running one
            await MainActor.run { self.startPolling() }
        }
    }

    func applicationWillTerminate(_ note: Notification) {
        poller?.stop()
        server.stop()
    }

    // Quit when the dashboard window is closed — it's the whole app.
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool {
        true
    }

    // Re-open the window if the user clicks the Dock icon after closing it.
    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window?.show() }
        return true
    }

    private func startPolling() {
        let p = SessionPoller(base: server.url)
        p.onUpdate = { [weak self] _, waiting in
            self?.notifyWaiting(waiting)
        }
        p.start()
        poller = p
    }

    private func notifyWaiting(_ waiting: [Session]) {
        let current = Set(waiting.map(\.session_key))
        for s in waiting where !notified.contains(s.session_key) {
            notify(session: s)
        }
        notified = current  // a session that waits again later re-notifies
    }

    @objc func reportBug(_ sender: Any?) {
        guard !capturingReport else { return }
        if let bugReport, bugReport.isOpen { bugReport.show(); return }
        capturingReport = true
        window?.captureForReport { [weak self] image in
            guard let self else { return }
            self.capturingReport = false
            self.bugReport = BugReportController(screenshot: image)
            self.bugReport?.show()
        }
        if window == nil {
            capturingReport = false
            bugReport = BugReportController(screenshot: nil)
            bugReport?.show()
        }
    }

    // ── Edit-menu clipboard bridge ──
    // WKWebView validates the standard copy:/paste: selectors against the DOM
    // (an xterm selection is canvas-rendered, so Copy stayed disabled and ⌘C
    // did nothing). These actions bypass that: ask the page for its selection
    // via the __rtCopy/__rtPaste globals the dashboard exposes.
    @objc func copyFromDashboard(_ sender: Any?) {
        if bugReport?.isKeyWindow == true {
            NSApp.sendAction(Selector(("copy:")), to: nil, from: sender)
            return
        }
        window?.evaluate("window.__rtCopy ? window.__rtCopy() : ''") { result in
            guard let text = result as? String, !text.isEmpty else { return }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        }
    }

    @objc func pasteToDashboard(_ sender: Any?) {
        if bugReport?.isKeyWindow == true {
            NSApp.sendAction(Selector(("paste:")), to: nil, from: sender)
            return
        }
        window?.evaluate("window.__rtPasteTarget ? window.__rtPasteTarget() : null") { [weak self] result in
            guard let self, let target = result as? String else { return }
            if target == "field" {
                NSApp.sendAction(Selector(("paste:")), to: nil, from: sender)
                return
            }
            let pasteboard = NSPasteboard.general
            do {
                if let png = try ClipboardImage.png(from: pasteboard) {
                    let home = ProcessInfo.processInfo.environment["DUCKTERM_HOME"] ?? (NSHomeDirectory() + "/.duckterm")
                    let path = try ClipboardImage.save(png, in: URL(fileURLWithPath: home).appendingPathComponent("pastes"))
                    let data = try JSONSerialization.data(withJSONObject: [path.path, target])
                    let json = String(decoding: data, as: UTF8.self)
                    self.window?.evaluate("window.__rtPasteImage && window.__rtPasteImage(...\(json))") { accepted in
                        if accepted as? Bool != true {
                            try? FileManager.default.removeItem(at: path)
                            self.pasteError(ClipboardImage.Failure.changedTarget)
                        }
                    }
                } else if let text = pasteboard.string(forType: .string), !text.isEmpty {
                    self.sendPaste(text)
                }
            } catch { self.pasteError(error) }
        }
    }

    private func pasteError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "Could not paste image"
        alert.informativeText = error.localizedDescription
        alert.runModal()
    }

    private func sendPaste(_ text: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: [text]),
            let json = String(data: data, encoding: .utf8)
        else { return }
        window?.evaluate("window.__rtPaste && window.__rtPaste((\(json))[0])")
    }

    private func notify(session: Session) {
        let content = UNMutableNotificationContent()
        content.title = "\(session.label) needs you"
        content.body = "A session is waiting on your input."
        content.sound = .default
        let req = UNNotificationRequest(
            identifier: "waiting-\(session.session_key)", content: content, trigger: nil
        )
        UNUserNotificationCenter.current().add(req)
    }
}

// A programmatic app has NO main menu, and on macOS ⌘C/⌘V/⌘X/⌘A/⌘Q/⌘W only
// exist as menu key equivalents — without these, copy/paste in the terminal
// was dead. The standard selectors route through the responder chain to the
// web view, which forwards them to the page (xterm handles the events).
private func buildMainMenu() -> NSMenu {
    let main = NSMenu()

    let appItem = NSMenuItem()
    main.addItem(appItem)
    let appMenu = NSMenu()
    appMenu.addItem(
        withTitle: "Hide RubberTerm", action: #selector(NSApplication.hide(_:)),
        keyEquivalent: "h")
    appMenu.addItem(
        withTitle: "Quit RubberTerm", action: #selector(NSApplication.terminate(_:)),
        keyEquivalent: "q")
    appItem.submenu = appMenu

    let editItem = NSMenuItem()
    main.addItem(editItem)
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    // Copy/Paste route through the dashboard bridge (nil target → responder
    // chain → AppDelegate), not the WKWebView selectors — see copyFromDashboard.
    edit.addItem(
        withTitle: "Copy", action: #selector(AppDelegate.copyFromDashboard(_:)),
        keyEquivalent: "c")
    edit.addItem(
        withTitle: "Paste", action: #selector(AppDelegate.pasteToDashboard(_:)),
        keyEquivalent: "v")
    edit.addItem(
        withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    editItem.submenu = edit

    let windowItem = NSMenuItem()
    main.addItem(windowItem)
    let windowMenu = NSMenu(title: "Window")
    windowMenu.addItem(
        withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    windowMenu.addItem(
        withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)),
        keyEquivalent: "m")
    windowItem.submenu = windowMenu
    let helpItem = NSMenuItem()
    main.addItem(helpItem)
    let helpMenu = NSMenu(title: "Help")
    helpMenu.addItem(withTitle: "Report a bug…", action: #selector(AppDelegate.reportBug(_:)), keyEquivalent: "")
    helpItem.submenu = helpMenu
    return main
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)  // a normal app: Dock icon + windows
app.mainMenu = buildMainMenu()
app.run()
