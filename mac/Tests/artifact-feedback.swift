// Uses the actual sanitized srcdoc emitted by e2e/artifact-feedback.spec.ts.
import AppKit
import WebKit

@main struct ArtifactFeedbackProbe {
    static func main() throws {
        let fixture = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [String: String]
        let encoded = String(data: try JSONSerialization.data(withJSONObject: [fixture["source"]!]), encoding: .utf8)!.replacingOccurrences(of: "<", with: "\\u003c")
        NSApplication.shared.setActivationPolicy(.prohibited)
        let result = FeedbackProbeResult()
        let config = WKWebViewConfiguration()
        config.userContentController.add(result, name: "probe")
        config.userContentController.addUserScript(WKUserScript(source: """
          if (window !== top) setTimeout(() => {
            let opaque = false;
            try { parent.document.body; } catch { opaque = true; }
            window.webkit.messageHandlers.probe.postMessage({opaque, attacked: !!window.artifactAttack});
            const node = document.querySelector('h1'), range = document.createRange();
            range.selectNodeContents(node); const selection = getSelection();
            selection.removeAllRanges(); selection.addRange(range);
            node.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
          }, 200);
          """, injectionTime: .atDocumentEnd, forMainFrameOnly: false))
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 800, height: 700), configuration: config)
        let window = NSWindow(contentRect: web.frame, styleMask: [], backing: .buffered, defer: false)
        window.contentView = web
        web.loadHTMLString("""
          <script>
          const source = \(encoded)[0], frame = document.createElement('iframe');
          frame.sandbox = 'allow-scripts';
          const nonce = source.match(/nonce="([a-f0-9]+)"/)[1];
          addEventListener('message', event => {
            if (event.source === frame.contentWindow && event.origin === 'null' && event.data.channel === nonce)
              window.webkit.messageHandlers.probe.postMessage({quote:event.data.quote});
          });
          frame.srcdoc = source; document.documentElement.append(frame);
          </script>
          """, baseURL: URL(string: fixture["origin"]!))
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline && !(result.opaque && result.quote == "Layout heading") {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        guard result.opaque, !result.attacked, result.quote == "Layout heading" else {
            print("FAIL: WebKit artifact selection", result.opaque, result.attacked, result.quote)
            exit(1)
        }
        print("PASS: real Mac WebKit reports selected artifact text while retaining opaque-origin isolation and blocking artifact scripts")
        window.close()
    }
}
final class FeedbackProbeResult: NSObject, WKScriptMessageHandler {
    var opaque = false
    var attacked = false
    var quote = ""
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let value = message.body as? [String: Any] else { return }
        if let found = value["opaque"] as? Bool { opaque = found }
        if let found = value["attacked"] as? Bool { attacked = found }
        if let found = value["quote"] as? String { quote = found }
    }
}
