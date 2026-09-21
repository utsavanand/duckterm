import AppKit
import WebKit

/// A single window hosting the dashboard in a WKWebView. Reused across opens —
/// clicking the menu item brings the existing window forward rather than
/// spawning duplicates.
final class DashboardWindow: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var web: WKWebView?
    private var url: URL

    init(url: URL) {
        self.url = url
    }

    func connect(url: URL, remote: Bool, title: String) {
        self.url = url
        web?.stopLoading()
        // Replace the web view to drop old sockets, callbacks and page state.
        // Remote sessions use an ephemeral browser store, never local cookies.
        guard let window else { return }
        let config = WKWebViewConfiguration()
        if remote { config.websiteDataStore = .nonPersistent() }
        let replacement = WKWebView(frame: window.contentView?.frame ?? .zero, configuration: config)
        replacement.navigationDelegate = self
        replacement.uiDelegate = self
        self.web = replacement
        window.contentView = replacement
        replacement.load(URLRequest(url: url))
        window.title = title
    }

    func setTitle(_ title: String) { window?.title = title }

    /// Run JS in the dashboard page — the Edit-menu clipboard bridge.
    func evaluate(_ js: String, done: ((Any?) -> Void)? = nil) {
        web?.evaluateJavaScript(js) { result, _ in done?(result) }
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
        let failedURL = url
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self, self.url == failedURL, self.web === webView else { return }
            webView.load(URLRequest(url: self.url))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let destination = navigationAction.request.url else { decisionHandler(.cancel); return }
        if destination.scheme == url.scheme && destination.host == url.host && destination.port == url.port {
            decisionHandler(.allow)
        } else {
            if ["https", "http"].contains(destination.scheme ?? "") { NSWorkspace.shared.open(destination) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let destination = navigationAction.request.url,
           ["https", "http"].contains(destination.scheme ?? "") { NSWorkspace.shared.open(destination) }
        return nil
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
