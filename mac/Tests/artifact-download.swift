// Run with scripts/test_artifact_download.sh on macOS. Uses the production
// WKDownloadDelegate with a test destination; no Save panel or real user files.
import AppKit
import WebKit

@main struct ArtifactDownloadProbe {
    static func main() throws {
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("duckterm-download-probe-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        let destination = directory.appendingPathComponent("report.html")
        try Data("previous bytes".utf8).write(to: destination)
        var errors: [Error] = []
        var chosenName = ""
        let downloader = ArtifactDownloads(chooseDestination: { name, done in
            chosenName = name
            done(destination)
        }, showError: { errors.append($0) })
        let delegate = ProbeDelegate(downloader)
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        web.navigationDelegate = delegate
        let window = NSWindow(contentRect: web.frame, styleMask: [], backing: .buffered, defer: false)
        window.contentView = web
        web.loadHTMLString("""
            <a id="save" download="report.html">Save</a><script>
            const bytes = new Blob(['<h1>Saved artifact</h1>'], {type:'text/html'});
            document.querySelector('#save').href = URL.createObjectURL(bytes);
            document.querySelector('#save').click();
            </script>
            """, baseURL: URL(string: "http://127.0.0.1/"))
        let expected = Data("<h1>Saved artifact</h1>".utf8)
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline && errors.isEmpty {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            if (try? Data(contentsOf: destination)) == expected { break }
        }
        guard errors.isEmpty, (try? Data(contentsOf: destination)) == expected,
              chosenName == "report.html", delegate.converted else {
            print("FAIL: native artifact download", errors, chosenName)
            exit(1)
        }
        print("PASS: real WKWebView blob download saved original bytes and replaced only the chosen test file")
        window.close()
    }
}

final class ProbeDelegate: NSObject, WKNavigationDelegate {
    let downloader: ArtifactDownloads
    var converted = false
    init(_ downloader: ArtifactDownloads) { self.downloader = downloader }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                 didBecome download: WKDownload) {
        converted = true
        download.delegate = downloader
    }
}
