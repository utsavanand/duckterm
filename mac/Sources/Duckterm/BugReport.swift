import AppKit
import WebKit

/// A local-only report editor. Nothing is uploaded. Sharing opens a mail draft;
/// only the user can send it. A report remains exportable without a mail account.
final class BugReportController: NSObject, WKScriptMessageHandler, WKNavigationDelegate, NSWindowDelegate {
    private var panel: NSPanel?
    private var web: WKWebView?
    private var screenshot: Data?
    private var diagnostics = ""
    private var report = BugReportData()
    private var preparing = false
    private var share: NSSharingService?
    private let recipient: String
    private let onSaved: (URL) -> Void
    private let chooseSaveDestination: (NSWindow, @escaping (URL?) -> Void) -> Void
    private let temporary = FileManager.default.temporaryDirectory
        .appendingPathComponent("RubberTerm-report-\(UUID().uuidString)", isDirectory: true)

    init(screenshot: NSImage?, chooseSaveDestination: @escaping (NSWindow, @escaping (URL?) -> Void) -> Void = { panel, completion in
        let picker = NSSavePanel()
        picker.nameFieldStringValue = "RubberTerm-bug-report.zip"
        picker.beginSheetModal(for: panel) { response in
            completion(response == .OK ? picker.url : nil)
        }
    }, onSaved: @escaping (URL) -> Void = {
        NSWorkspace.shared.activateFileViewerSelecting([$0])
    }) {
        self.onSaved = onSaved
        self.chooseSaveDestination = chooseSaveDestination
        recipient = UserDefaults.standard.string(forKey: "SupportEmail")
            ?? Bundle.main.object(forInfoDictionaryKey: "RubberTermSupportEmail") as? String ?? ""
        if let tiff = screenshot?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff) {
            self.screenshot = bitmap.representation(using: .png, properties: [:])
        }
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "development"
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        diagnostics = "RubberTerm \(version)\n\(ProcessInfo.processInfo.operatingSystemVersionString)\nArchitecture: \(architecture)\n\nRecent app events (up to 100):\n\(AppDiagnostics.shared.text())\n"
        super.init()
    }

    var isOpen: Bool { panel != nil }
    var isKeyWindow: Bool { panel?.isKeyWindow == true }

    func windowShouldClose(_ sender: NSWindow) -> Bool { !preparing }

    func show() {
        if let panel { panel.makeKeyAndOrderFront(nil); return }
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "bugReport")
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 820), configuration: config)
        view.navigationDelegate = self
        let panel = NSPanel(contentRect: view.frame, styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        panel.title = "Report a bug — RubberTerm"
        panel.contentMinSize = NSSize(width: 440, height: 460)
        panel.contentView = view
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        panel.center()
        self.panel = panel
        web = view
        view.loadHTMLString(Self.html, baseURL: nil)
        panel.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        web?.configuration.userContentController.removeScriptMessageHandler(forName: "bugReport")
        web = nil
        panel = nil
        // A mail composer may still be reading its files. Reports handed to Mail
        // live in the OS temporary directory, not in the repo or agent storage.
        if share == nil { try? FileManager.default.removeItem(at: temporary) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(action.request.url?.absoluteString == "about:blank" ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        update()
        webView.evaluateJavaScript("document.getElementById('summary').focus()", completionHandler: nil)
    }

    private func update(status: String = "") {
        let data: [String: Any] = [
            "screenshot": screenshot.map { "data:image/png;base64," + $0.base64EncodedString() } ?? "",
            "diagnostics": diagnostics,
            "files": report.attachments.map(\.name),
            "canEmail": !recipient.isEmpty,
            "status": status,
            "busy": preparing,
        ]
        guard let json = try? JSONSerialization.data(withJSONObject: data),
              let text = String(data: json, encoding: .utf8) else { return }
        web?.evaluateJavaScript("window.updateReport(\(text))", completionHandler: nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.webView === web,
              let body = message.body as? [String: Any], let action = body["action"] as? String,
              !preparing else { return }
        switch action {
        case "cancel": panel?.performClose(nil)
        case "add": chooseFiles()
        case "remove":
            if let index = body["index"] as? Int, report.attachments.indices.contains(index) {
                report.attachments.remove(at: index); update()
            }
        case "preview":
            if let screenshot {
                do {
                    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                    let path = temporary.appendingPathComponent("preview.png")
                    try screenshot.write(to: path, options: .atomic)
                    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
                    NSWorkspace.shared.open(path)
                } catch { update(status: "Could not open the screenshot preview.") }
            }
        case "email", "save": prepare(body, email: action == "email")
        default: break
        }
    }

    private func chooseFiles() {
        guard let panel else { return }
        preparing = true
        update()
        let picker = NSOpenPanel()
        picker.canChooseFiles = true
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = true
        picker.resolvesAliases = false
        picker.beginSheetModal(for: panel) { [weak self] response in
            guard let self else { return }
            self.preparing = false
            guard response == .OK else { self.update(); return }
            do {
                var updated = self.report
                for url in picker.urls { try updated.add(url) }
                self.report = updated
                self.update()
            } catch { self.update(status: error.localizedDescription) }
        }
    }

    private func prepare(_ body: [String: Any], email: Bool) {
        let summary = String((body["summary"] as? String ?? "").prefix(200))
        let details = String((body["details"] as? String ?? "").prefix(20000))
        let reply = String((body["reply"] as? String ?? "").prefix(254))
        do {
            try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let directory = temporary.appendingPathComponent(UUID().uuidString, isDirectory: true)
            let files = try report.write(to: directory, summary: summary, details: details, replyTo: reply,
                screenshot: body["screenshot"] as? Bool == true ? screenshot : nil,
                diagnostics: body["diagnostics"] as? Bool == true ? diagnostics : nil)
            if email {
                guard !recipient.isEmpty, let service = NSSharingService(named: .composeEmail),
                      service.canPerform(withItems: files) else {
                    try? FileManager.default.removeItem(at: directory)
                    update(status: "No compatible email app is available. Save the report and attach it to an email instead.")
                    return
                }
                service.recipients = [recipient]
                service.subject = "[RubberTerm bug] " + summary.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ")
                share = service
                service.perform(withItems: ["RubberTerm bug report\n\n\(summary)\n\n\(details)"] + files.map { $0 as Any })
                update(status: "Email draft requested. Review the attachments and click Send in your email app. Nothing has been sent by RubberTerm.")
            } else { save(directory) }
        } catch { update(status: error.localizedDescription) }
    }

    private func save(_ directory: URL) {
        guard let panel else { return }
        preparing = true
        update()
        chooseSaveDestination(panel) { [weak self] destination in
            guard let self else { return }
            guard let destination else {
                try? FileManager.default.removeItem(at: directory)
                self.preparing = false
                self.update()
                return
            }
            self.preparing = true
            self.update(status: "Saving report…")
            DispatchQueue.global(qos: .userInitiated).async {
                let archive = self.temporary.appendingPathComponent("export-\(UUID().uuidString).zip")
                let task = Process()
                task.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
                task.arguments = ["-c", "-k", "--keepParent", directory.path, archive.path]
                task.standardOutput = FileHandle.nullDevice
                task.standardError = FileHandle.nullDevice
                var succeeded = false
                do {
                    try task.run(); task.waitUntilExit()
                    guard task.terminationStatus == 0 else { throw BugReportError.archiveFailed }
                    try Data(contentsOf: archive).write(to: destination, options: .atomic)
                    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destination.path)
                    succeeded = true
                } catch { }
                try? FileManager.default.removeItem(at: archive)
                try? FileManager.default.removeItem(at: directory)
                DispatchQueue.main.async {
                    self.preparing = false
                    if succeeded {
                        self.panel?.performClose(nil)
                        self.onSaved(destination)
                    } else {
                        self.update(status: "Could not save the report. Try another location.")
                    }
                }
            }
        }
    }

    private static let html = #"""
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'"><style>
*{box-sizing:border-box}body{margin:0;padding:24px;background:#15191f;color:#e4e7eb;font:14px -apple-system,BlinkMacSystemFont,sans-serif}h1{font-size:24px;margin:0 0 8px}p{color:#a8afbc;line-height:1.5;margin:0 0 18px}label{display:block;font-weight:600;margin:14px 0 7px}small{color:#98a2b1;font-weight:400}input,textarea{width:100%;font:inherit;color:#e0e5ed;background:#0d1117;border:1px solid #414a56;border-radius:7px;padding:10px}textarea{height:88px;resize:vertical}input::placeholder,textarea::placeholder{color:#8993a2}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{border:1px solid #3d4653;border-radius:8px;padding:12px}.card label{margin:0 0 10px}.card input{width:auto;accent-color:#2dc590;margin-right:7px}.thumb{width:100%;height:105px;object-fit:cover;object-position:top;cursor:pointer;border-radius:4px;background:#0d1117}button{padding:9px 13px;border-radius:6px;border:1px solid #4a5665;background:#202630;color:#e0e6ef;font:inherit;cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid #36d9a5;outline-offset:2px}button:disabled{opacity:.5;cursor:default}.primary{background:#2cc68f;color:#061810;border-color:#2cc68f;font-weight:600}.link{border:0;background:none;color:#46d9ae;padding:6px 0}.upload{border:1px dashed #556170;border-radius:7px;padding:10px;margin-top:15px}footer{display:flex;gap:8px;justify-content:flex-end;border-top:1px solid #343d48;padding-top:16px;margin-top:15px}pre{font:12px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word;background:#0d1117;padding:12px;max-height:180px;overflow:auto}.file{display:flex;align-items:center;gap:8px;padding:6px 0}.file span{flex:1;overflow-wrap:anywhere}#status{color:#ffd18b;line-height:1.5;margin:12px 0}#privacy{display:block;margin-top:14px;line-height:1.5}@media(max-width:560px){body{padding:18px}.grid{grid-template-columns:1fr}footer{flex-wrap:wrap}h1{font-size:22px}}
</style></head><body><h1>Report a bug</h1><p>Tell us what went wrong. Review what’s included before creating an email.</p><form id="report"><label for="summary">Summary</label><input id="summary" required maxlength="200" placeholder="For example: the terminal does not respond after resuming"><label for="details">What happened? <small>Optional</small></label><textarea id="details" maxlength="20000" placeholder="What were you doing, what did you expect, and what happened instead? Add steps to reproduce if you have them."></textarea><label for="reply">Your email <small>Optional · so we can reply</small></label><input id="reply" type="email" maxlength="254" placeholder="you@example.com"><label>Included with your report</label><div class="grid"><div class="card"><label><input id="screenshot" type="checkbox" checked>Current window screenshot</label><button type="button" class="link" id="preview" aria-label="Preview screenshot"><img class="thumb" id="image" alt="Screenshot of your current RubberTerm window"></button><small id="captureNote">Captured before this form opened · Click to preview</small></div><div class="card"><label><input id="diagnostics" type="checkbox" checked>App diagnostics</label><p>App and macOS versions<br>Recent app event log<br>Connection status</p><button type="button" class="link" id="review">Review diagnostics</button><small style="display:block">No credentials or agent conversations collected in diagnostics</small></div></div><pre id="logs" hidden></pre><div class="upload"><button type="button" class="link" id="add">＋ Add files</button> <small>Optional · up to 5 files, 5 MB each, 15 MB total</small><div id="files"></div></div><small id="privacy">Your screenshot and added files may contain private information. Review them before sending. Nothing is uploaded by this form.</small><div id="status" role="status" aria-live="polite"></div><footer><button type="button" id="cancel">Cancel</button><button type="submit" id="save" value="save">Save ZIP</button><button type="submit" class="primary" id="email" value="email">Create email</button></footer></form><script>
const el=id=>document.getElementById(id);const send=(action,data={})=>window.webkit.messageHandlers.bugReport.postMessage({action,...data});
el('add').onclick=()=>send('add');el('preview').onclick=()=>send('preview');el('cancel').onclick=()=>send('cancel');el('review').onclick=()=>{el('logs').hidden=!el('logs').hidden};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();send('cancel')}});
el('report').onsubmit=e=>{e.preventDefault();send(e.submitter?.value||'save',{summary:el('summary').value,details:el('details').value,reply:el('reply').value,screenshot:el('screenshot').checked,diagnostics:el('diagnostics').checked})};
window.updateReport=d=>{el('status').textContent=d.status;el('logs').textContent=d.diagnostics;el('image').src=d.screenshot;el('preview').hidden=!d.screenshot;if(!d.screenshot){el('screenshot').checked=false;el('screenshot').disabled=true;el('captureNote').textContent='Screenshot unavailable. You can add one manually.'}else{el('screenshot').disabled=false;el('captureNote').textContent='Captured before this form opened · Click to preview'}el('email').disabled=!d.canEmail||d.busy;el('save').disabled=d.busy;el('add').disabled=d.busy;el('cancel').disabled=d.busy;if(!d.canEmail&&!d.status)el('status').textContent='No support email is configured for this build. You can still save your report.';el('files').replaceChildren();d.files.forEach((name,index)=>{const row=document.createElement('div');row.className='file';const span=document.createElement('span');span.textContent=name;const b=document.createElement('button');b.type='button';b.textContent='Remove';b.onclick=()=>send('remove',{index});row.append(span,b);el('files').append(row)})};
</script></body></html>
"""#
}
