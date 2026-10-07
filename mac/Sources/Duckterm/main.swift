import AppKit
import UserNotifications

/// Standalone desktop app: double-click to launch, opens its own window with the
/// dashboard (an embedded web view — never your browser). Owns the local server
/// process, shows native notifications when a session needs you, and quits when
/// you close the window.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var hosts = RemoteHost.load()
    private var launchConnections: [String: RemoteConnection] = [:]
    private let launchAPI = LaunchDestination()
    private let projectTransfer = ProjectTransfer()
    private let sessionTransport = SessionTransport()
    private let collaborationSetup = CollaborationSetup()
    private let server = ServerProcess()
    private var localStart: Task<Bool, Never>?
    private var pollers: [String: SessionPoller] = [:]
    private var window: DashboardWindow?
    private var bugReport: BugReportController?
    private var capturingReport = false
    private var notified: [String: Set<String>] = [:]

    func applicationDidFinishLaunching(_ note: Notification) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        AppDiagnostics.shared.record("Application launched")
        window = DashboardWindow(url: server.url)
        window?.onReportBug = { [weak self] in self?.reportBug(nil) }
        window?.desktopHosts = hosts
        sessionTransport.onTerminal = { [weak self] event in self?.window?.dispatch(name: "remote-terminal", detail: event) }
        sessionTransport.onTerminalData = { [weak self] event in await self?.window?.dispatchAndWait(name: "remote-terminal", detail: event) }
        window?.onPageReset = { [weak self] in self?.sessionTransport.close() }
        if hosts.isEmpty, AppIdentity.isTest,
           let alias = Bundle.main.object(forInfoDictionaryKey: "DucktermTestRemoteHost") as? String,
           let host = try? RemoteHost(name: alias, target: alias) {
            hosts.append(host)
            RemoteHost.save(hosts)
        }
        window?.onChooseLaunchTarget = { [weak self] target, draft, sessionKey in
            guard let self else { return }
            if target == "add" { self.addHost(launchDraft: draft); return }
            let host = self.hosts.first { $0.target == target }
            guard target == "local" || host != nil else { return }
            self.connectHost(host, launchDraft: draft.isEmpty ? nil : draft, selectedSession: sessionKey)
        }
        window?.onLaunchRequest = { [weak self] target, operation, params in
            guard let self else { throw LaunchDestination.Failure.message("App closed") }
            if operation == "collaboration-preview" || operation == "collaboration-connect" {
                guard let coordinator = self.hosts.first(where: { $0.target == target }) else {
                    throw LaunchDestination.Failure.message("Choose a saved remote coordinator")
                }
                let source = params["source"] as? String ?? "local"
                guard source == "local" || self.hosts.contains(where: { $0.target == source }) else {
                    throw LaunchDestination.Failure.message("Choose a saved computer to connect")
                }
                _ = await self.ensureLocalServer()
                var bases = ["local": self.server.url]
                for host in self.hosts where host.target == target || host.target == source {
                    if self.launchConnections[host.target] == nil {
                        let connection = try RemoteConnection(host: host)
                        self.launchConnections[host.target] = connection
                        connection.start()
                    }
                    bases[host.target] = self.launchConnections[host.target]!.url
                }
                if operation == "collaboration-preview" {
                    return try await self.collaborationSetup.preview(
                        coordinator: coordinator, source: source,
                        sourceName: source == "local" ? "This Mac" : self.hosts.first(where: { $0.target == source })!.name,
                        connection: source == "local" ? target : (params["coordinator_ssh"] as? String ?? ""),
                        bases: bases, groups: params["groups"] as? [String: [String: String]] ?? [:], api: self.launchAPI)
                }
                let result = try await self.collaborationSetup.confirm(id: params["plan_id"] as? String ?? "", bases: bases, api: self.launchAPI) { host, key, folder in
                    guard let base = bases[host] else { throw LaunchDestination.Failure.message("Computer disconnected") }
                    let body = String(decoding: try JSONSerialization.data(withJSONObject: ["group": folder]), as: UTF8.self)
                    let response = try await self.sessionTransport.perform(base: base, api: self.launchAPI,
                        params: ["method": "PATCH", "path": "/sessions/\(key)", "body": body])
                    guard let result = response as? [String: Any], result["status"] as? Int == 200 else {
                        throw LaunchDestination.Failure.message("Could not preserve the sidebar folder arrangement")
                    }
                }
                UserDefaults.standard.set(target, forKey: "collaborationCoordinatorTarget")
                let connected = UserDefaults.standard.stringArray(forKey: "collaborationConnectedTargets") ?? []
                UserDefaults.standard.set(Array(Set(connected + [source, target])), forKey: "collaborationConnectedTargets")
                return result
            }
            if operation == "project-pause", let id = params["id"] as? String {
                self.projectTransfer.pause(id)
                return ["paused": true]
            }
            if operation == "project-preview" || operation == "project-continue" {
                _ = await self.ensureLocalServer()
                return try await self.launchAPI.perform(base: self.server.url, operation: operation == "project-preview" ? "transfer-preview" : "transfer-continue", params: params)
            }
            let base: URL
            if target == "local" {
                _ = await self.ensureLocalServer()
                base = self.server.url
            } else {
                guard let host = self.hosts.first(where: { $0.target == target }) else {
                    throw LaunchDestination.Failure.message("Unknown computer")
                }
                if self.launchConnections[target] == nil {
                    let connection = try RemoteConnection(host: host)
                    self.launchConnections[target] = connection
                    connection.start()
                    self.startPolling(host: target, base: connection.url)
                }
                base = self.launchConnections[target]!.url
            }
            if operation == "collaboration-status" {
                var status = try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/status", method: "GET")
                status["configured_coordinator"] = UserDefaults.standard.string(forKey: "collaborationCoordinatorTarget")
                return status
            }
            if operation == "collaboration-folder" {
                guard UserDefaults.standard.string(forKey: "collaborationCoordinatorTarget") != nil else { return ["handled": false] }
                _ = await self.ensureLocalServer()
                let before = try await self.collaborationSetup.call(base: self.server.url, api: self.launchAPI, route: "/collaboration/status", method: "GET")
                guard before["enabled"] as? Bool == true else { return ["handled": false] }
                let result = try await self.collaborationSetup.call(base: self.server.url, api: self.launchAPI,
                    route: "/collaboration/owner-queue", body: params)
                await self.flushCollaboration()
                let status = try await self.collaborationSetup.call(base: self.server.url, api: self.launchAPI,
                    route: "/collaboration/status", method: "GET")
                return ["handled": true, "id": result["id"] ?? "", "pending": !(status["pending_changes"] as? [[String: Any]] ?? []).isEmpty]
            }
            if operation == "collaboration-retry" {
                await self.flushCollaboration(source: base)
                return try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/sync")
            }
            if operation == "collaboration-cancel" {
                return try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/owner-cancel", body: params)
            }
            if operation == "collaboration-keep-separately" {
                let result = try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/keep-separately", body: params)
                await self.flushCollaboration(source: base)
                return result
            }
            if operation == "collaboration-disconnect" {
                guard let hubTarget = UserDefaults.standard.string(forKey: "collaborationCoordinatorTarget"),
                      let hubHost = self.hosts.first(where: { $0.target == hubTarget }), target != hubTarget else {
                    throw LaunchDestination.Failure.message("Choose a connected computer, not the coordinator")
                }
                if self.launchConnections[hubTarget] == nil {
                    let connection = try RemoteConnection(host: hubHost)
                    self.launchConnections[hubTarget] = connection
                    connection.start()
                }
                let result = try await self.collaborationSetup.disconnect(source: base,
                    coordinator: self.launchConnections[hubTarget]!.url, api: self.launchAPI)
                let connected = UserDefaults.standard.stringArray(forKey: "collaborationConnectedTargets") ?? []
                UserDefaults.standard.set(connected.filter { $0 != target }, forKey: "collaborationConnectedTargets")
                return result
            }
            if operation == "collaboration-move" {
                guard (UserDefaults.standard.stringArray(forKey: "collaborationConnectedTargets") ?? []).contains(target) else { return ["handled": false] }
                let status = try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/status", method: "GET")
                guard status["enabled"] as? Bool == true else { return ["handled": false] }
                guard let key = params["key"] as? String, key.range(of: #"^[A-Za-z0-9._-]{1,128}$"#, options: .regularExpression) != nil,
                      let folder = params["folder"] as? String,
                      let computer = status["computer_id"] as? String,
                      let hubTarget = UserDefaults.standard.string(forKey: "collaborationCoordinatorTarget"),
                      let hubHost = self.hosts.first(where: { $0.target == hubTarget }) else {
                    throw LaunchDestination.Failure.message("Open Collaboration settings to reconnect this workspace")
                }
                if self.launchConnections[hubTarget] == nil {
                    let connection = try RemoteConnection(host: hubHost)
                    self.launchConnections[hubTarget] = connection
                    connection.start()
                }
                let hubBase = self.launchConnections[hubTarget]!.url
                if let root = folder.split(separator: "/").first {
                    _ = try await self.collaborationSetup.call(base: hubBase, api: self.launchAPI, route: "/collaboration/bind", body: ["computer_id": computer, "local_path": String(root), "canonical_path": String(root)])
                    _ = try await self.collaborationSetup.call(base: base, api: self.launchAPI, route: "/collaboration/sync")
                }
                let body = String(decoding: try JSONSerialization.data(withJSONObject: ["group": folder]), as: UTF8.self)
                let response = try await self.sessionTransport.perform(base: base, api: self.launchAPI,
                    params: ["method": "PATCH", "path": "/sessions/\(key)", "body": body])
                guard let result = response as? [String: Any], result["status"] as? Int == 200 else {
                    throw LaunchDestination.Failure.message("Folder move could not be saved on this computer")
                }
                return ["handled": true, "updated": true]
            }
            if operation == "session-request" {
                return try await self.sessionTransport.perform(base: base, api: self.launchAPI, params: params)
            }
            if operation.hasPrefix("terminal-") {
                return try await self.sessionTransport.terminal(host: target, base: base, api: self.launchAPI, operation: operation, params: params)
            }
            if operation == "project-transfer" {
                guard target != "local" else { throw LaunchDestination.Failure.message("Choose a remote computer") }
                _ = await self.ensureLocalServer()
                return try await self.projectTransfer.copy(api: self.launchAPI, local: self.server.url, remote: base, params: params)
            }
            if operation.hasPrefix("project-") {
                let mapped = operation.replacingOccurrences(of: "project-", with: "transfer-")
                let result = try await self.launchAPI.perform(base: base, operation: mapped, params: params)
                if operation == "project-launch", params["source_session"] as? String != nil,
                   let row = result as? [String: Any], let key = row["session_key"] as? String, let id = params["id"] as? String {
                    _ = await self.ensureLocalServer()
                    _ = try await self.launchAPI.perform(base: self.server.url, operation: "transfer-link", params: ["id": id, "target": target, "session_key": key])
                }
                return result
            }
            return try await self.launchAPI.perform(base: base, operation: operation, params: params)
        }
        window?.desktopHosts = hosts
        window?.show()  // open the dashboard window on launch
        NSApp.activate(ignoringOtherApps: true)

        window?.setTitle(AppIdentity.name)
        Task {
            _ = await ensureLocalServer()
            startPolling()
        }
    }

    func applicationWillTerminate(_ note: Notification) {
        pollers.values.forEach { $0.stop() }
        sessionTransport.close()
        launchConnections.values.forEach { $0.stop() }
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

    private func ensureLocalServer() async -> Bool {
        if let localStart { return await localStart.value }
        let pending = Task { await server.start() }
        localStart = pending
        defer { localStart = nil }
        return await pending.value
    }

    private func startPolling(host: String = "local", base: URL? = nil) {
        guard pollers[host] == nil else { return }
        let p = SessionPoller(base: base ?? server.url)
        p.onUpdate = { [weak self] _, waiting in
            self?.notifyWaiting(waiting, host: host)
            Task { @MainActor [weak self] in await self?.flushCollaboration(source: base) }
        }
        p.start()
        pollers[host] = p
    }

    private func flushCollaboration(source: URL? = nil) async {
        guard let target = UserDefaults.standard.string(forKey: "collaborationCoordinatorTarget"),
              let host = hosts.first(where: { $0.target == target }) else { return }
        if launchConnections[target] == nil {
            guard let connection = try? RemoteConnection(host: host) else { return }
            launchConnections[target] = connection
            connection.start()
        }
        guard let base = launchConnections[target]?.url else { return }
        // A failed request leaves the durable local action available for retry.
        try? await collaborationSetup.flush(local: source ?? server.url, coordinator: base, api: launchAPI)
    }

    @objc func showSettings(_ sender: Any?) {
        let alert = NSAlert()
        alert.messageText = "Settings"
        alert.informativeText = "Manage the computers available when you create a session."
        alert.addButton(withTitle: "Remote computers…")
        alert.addButton(withTitle: "Close")
        if alert.runModal() == .alertFirstButtonReturn { chooseHost(sender) }
    }

    @objc func chooseHost(_ sender: Any?) {
        let alert = NSAlert()
        alert.messageText = "Remote computers"
        alert.informativeText = "Remote agents keep running when you close Duckterm. SSH authentication must already be configured."
        let picker = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 320, height: 28))
        picker.addItems(withTitles: ["This Mac"] + hosts.map { $0.name })
        alert.accessoryView = picker
        alert.addButton(withTitle: "Connect")
        alert.addButton(withTitle: "Add remote host…")
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Forget selected host")
        let result = alert.runModal()
        if result == .alertSecondButtonReturn { addHost(); return }
        if result.rawValue == NSApplication.ModalResponse.alertFirstButtonReturn.rawValue + 3 {
            let index = picker.indexOfSelectedItem - 1
            if hosts.indices.contains(index) {
                let removed = hosts.remove(at: index)
                RemoteHost.save(hosts)
                launchConnections.removeValue(forKey: removed.target)?.stop()
                window?.desktopHosts = hosts
                pollers.removeValue(forKey: removed.target)?.stop()
                notified.removeValue(forKey: removed.target)
                sessionTransport.close(host: removed.target)
                window?.refreshDesktop()
            }
            return
        }
        guard result == .alertFirstButtonReturn else { return }
        connectHost(picker.indexOfSelectedItem == 0 ? nil : hosts[picker.indexOfSelectedItem - 1])
    }

    private func addHost(launchDraft: [String: String]? = nil) {
        let alert = NSAlert()
        alert.messageText = "Add remote host"
        alert.informativeText = "Enter an alias from ~/.ssh/config or user@hostname. Run ssh to this host in Terminal first to authorize access and verify its host key. Duckterm must be serving on remote port 4300."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        field.placeholderString = "duckterm-dev"
        alert.accessoryView = field
        alert.addButton(withTitle: "Save and connect")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            let target = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let host = try RemoteHost(name: target, target: target)
            if !hosts.contains(host) { hosts.append(host); RemoteHost.save(hosts) }
            connectHost(host, launchDraft: launchDraft)
        } catch { showConnectionError(error) }
    }

    private func connectHost(_ host: RemoteHost?, launchDraft: [String: String]? = nil, selectedSession: String? = nil) {
        // A host is a launch/session destination, never the dashboard origin.
        // Existing WKWebView, terminals, local server and sidebar stay mounted.
        window?.desktopHosts = hosts
        window?.desktopTarget = "local"
        window?.launchDraft = launchDraft
        window?.launchTarget = host?.target ?? "local"
        if let host, launchConnections[host.target] == nil {
            do {
                let connection = try RemoteConnection(host: host)
                launchConnections[host.target] = connection
                connection.start()
                startPolling(host: host.target, base: connection.url)
            } catch { showConnectionError(error) }
        }
        window?.refreshDesktop()
        if let selectedSession {
            window?.dispatch(name: "native-select-session", detail: ["host": host?.target ?? "local", "key": selectedSession])
        }
    }

    private func showConnectionError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "Could not connect"
        alert.informativeText = error.localizedDescription
        alert.runModal()
    }

    private func notifyWaiting(_ waiting: [Session], host: String) {
        let current = Set(waiting.map(\.session_key))
        for s in waiting where !(notified[host] ?? []).contains(s.session_key) {
            notify(session: s, host: host)
        }
        notified[host] = current  // a session that waits again later re-notifies
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
                    if target.hasPrefix("~remote~") {
                        let data = try JSONSerialization.data(withJSONObject: [png.base64EncodedString(), target])
                        let json = String(decoding: data, as: UTF8.self)
                        self.window?.evaluate("window.__rtPasteImageData && window.__rtPasteImageData(...\(json))") { accepted in
                            if accepted as? Bool != true { self.pasteError(ClipboardImage.Failure.changedTarget) }
                        }
                        return
                    }
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

    private func notify(session: Session, host: String) {
        let content = UNMutableNotificationContent()
        content.title = "\(session.label) needs you"
        content.body = "A session on \(host == "local" ? "This Mac" : host) is waiting on your input."
        content.sound = .default
        let req = UNNotificationRequest(
            identifier: "waiting-\(host)-\(session.session_key)", content: content, trigger: nil
        )
        UNUserNotificationCenter.current().add(req)
    }
}

// A programmatic app has NO main menu, and on macOS ⌘C/⌘V/⌘X/⌘A/⌘Q/⌘W only
// exist as menu key equivalents — without these, copy/paste in the terminal
// was dead. The standard selectors route through the responder chain to the
// web view, which forwards them to the page (xterm handles the events).
@MainActor
private func buildMainMenu() -> NSMenu {
    let main = NSMenu()

    let appItem = NSMenuItem()
    main.addItem(appItem)
    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "Settings…", action: #selector(AppDelegate.showSettings(_:)), keyEquivalent: ",")
    appMenu.addItem(.separator())
    appMenu.addItem(
        withTitle: "Hide \(AppIdentity.name)", action: #selector(NSApplication.hide(_:)),
        keyEquivalent: "h")
    appMenu.addItem(
        withTitle: "Quit \(AppIdentity.name)", action: #selector(NSApplication.terminate(_:)),
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

MainActor.assumeIsolated {
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)  // a normal app: Dock icon + windows
app.mainMenu = buildMainMenu()
app.run()
}
