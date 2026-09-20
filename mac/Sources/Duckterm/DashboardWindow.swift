import AppKit
import WebKit

/// A single window hosting the dashboard in a WKWebView. Reused across opens —
/// clicking the menu item brings the existing window forward rather than
/// spawning duplicates.
final class DashboardWindow: NSObject, NSWindowDelegate, WKNavigationDelegate {
    private var window: NSWindow?
    private let url: URL

    init(url: URL) {
        self.url = url
    }

    func show() {
        if let window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 760))
        web.navigationDelegate = self
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
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self else { return }
            webView.load(URLRequest(url: self.url))
        }
    }

    func windowWillClose(_ notification: Notification) {
        window = nil  // rebuild fresh next open so it reloads the dashboard
    }
}
