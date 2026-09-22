import AppKit
import WebKit

/// A single window hosting the dashboard in a WKWebView. Reused across opens —
/// clicking the menu item brings the existing window forward rather than
/// spawning duplicates.
final class DashboardWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var web: WKWebView?
    private let url: URL

    init(url: URL) {
        self.url = url
    }

    /// Run JS in the dashboard page — the Edit-menu clipboard bridge.
    func evaluate(_ js: String, done: ((Any?) -> Void)? = nil) {
        web?.evaluateJavaScript(js) { result, _ in done?(result) }
    }

    func captureForReport(_ done: @escaping (NSImage?) -> Void) {
        guard let web else { done(nil); return }
        // Capture only our dashboard, before the report UI opens. No desktop capture.
        web.takeSnapshot(with: nil) { image, error in
            if let error { AppDiagnostics.shared.record("Report screenshot failed", code: (error as NSError).code) }
            done(image)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        AppDiagnostics.shared.record("Dashboard loaded")
    }

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 760))
        if #available(macOS 13.3, *) {
            web.isInspectable = true  // debuggable from Safari's Develop menu
        }
        web.navigationDelegate = self
        // Without a UI delegate, WKWebView silently no-ops window.confirm
        // (returns false) and window.prompt (returns null) — which made the
        // dashboard's delete-folder ✕ and rename prompts dead in the app
        // while working fine in a browser.
        web.uiDelegate = self
        self.web = web
        web.load(URLRequest(url: url))

        let win = NSWindow(
            contentRect: web.frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.title = "RubberTerm"
        win.contentView = web
        win.center()
        win.delegate = self
        win.isReleasedWhenClosed = false
        window = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // The first load races the server the app is itself starting (the window
    // opens immediately; the server takes a few seconds to bind). A failed
    // local load otherwise stays a blank page forever — keep retrying until
    // the server answers.
    func webView(
        _ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
        withError error: Error
    ) {
        AppDiagnostics.shared.record("Dashboard connection failed", code: (error as NSError).code)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self else { return }
            webView.load(URLRequest(url: self.url))
        }
    }

    func windowWillClose(_ notification: Notification) {
        window = nil  // rebuild fresh next open so it reloads the dashboard
        web = nil
    }

    // ── JS dialog panels (WKUIDelegate) — native sheets for alert/confirm/prompt ──

    private func sheetHost() -> NSWindow? {
        window ?? NSApp.mainWindow
    }

    func webView(
        _ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        guard let host = sheetHost() else { return completionHandler() }
        alert.beginSheetModal(for: host) { _ in completionHandler() }
    }

    func webView(
        _ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        guard let host = sheetHost() else { return completionHandler(false) }
        alert.beginSheetModal(for: host) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(
        _ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?, initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (String?) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = prompt
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        guard let host = sheetHost() else { return completionHandler(nil) }
        alert.beginSheetModal(for: host) { response in
            completionHandler(
                response == .alertFirstButtonReturn ? field.stringValue : nil)
        }
    }
}
