// Run with scripts/test_artifact_preview.sh on macOS. Loads a real sandboxed
// srcdoc preview in WKWebView behind the production navigation policy.
import AppKit
import WebKit

@main struct ArtifactPreviewProbe {
    static func main() {
        let dashboard = URL(string: "http://127.0.0.1:4300/")!
        let rules: [(String, Bool, Bool)] = [
            ("about:srcdoc", false, true),
            ("about:blank", false, true),
            ("about:srcdoc", true, false),
            ("http://127.0.0.1:4300/sessions", true, true),
            ("http://127.0.0.1:9999/", false, false),
            ("https://example.com/", false, false),
        ]
        for (destination, mainFrame, expected) in rules
        where dashboardAllowsNavigation(to: URL(string: destination)!, dashboard: dashboard,
                                        inMainFrame: mainFrame) != expected {
            print("FAIL: \(destination) mainFrame=\(mainFrame) should be \(expected ? "allowed" : "refused")")
            exit(1)
        }

        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        let delegate = PolicyDelegate(dashboard)
        let config = WKWebViewConfiguration()
        config.userContentController.add(delegate, name: "probe")
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 400, height: 300), configuration: config)
        web.navigationDelegate = delegate
        let window = NSWindow(contentRect: web.frame, styleMask: [], backing: .buffered, defer: false)
        window.contentView = web
        // Same shape as ArtifactsView: sandboxed srcdoc that reports back by postMessage.
        web.loadHTMLString("""
            <script>addEventListener('message', e => webkit.messageHandlers.probe.postMessage(e.data));</script>
            <iframe sandbox="allow-scripts" srcdoc="<h1>Roadmap</h1><script>parent.postMessage('rendered', '*')</script>"></iframe>
            """, baseURL: dashboard)
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline && delegate.received == nil {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        guard delegate.received == "rendered" else {
            print("FAIL: sandboxed srcdoc preview never rendered; refused:", delegate.refused)
            exit(1)
        }
        print("PASS: navigation rules hold and a real WKWebView rendered the sandboxed srcdoc preview")
        window.close()
    }
}

final class PolicyDelegate: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let dashboard: URL
    var received: String?
    var refused: [String] = []
    init(_ dashboard: URL) { self.dashboard = dashboard }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let destination = action.request.url else { decisionHandler(.cancel); return }
        let allowed = dashboardAllowsNavigation(to: destination, dashboard: dashboard,
                                                inMainFrame: action.targetFrame?.isMainFrame ?? true)
        if !allowed { refused.append(destination.absoluteString) }
        decisionHandler(allowed ? .allow : .cancel)
    }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        received = message.body as? String
    }
}
