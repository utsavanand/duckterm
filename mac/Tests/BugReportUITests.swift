import AppKit
import WebKit

@main
struct BugReportUITests {
    static func verify(_ condition: Bool, _ message: String = "UI assertion failed", line: UInt = #line) {
        precondition(condition, message, line: line)
    }
    @MainActor static func js(_ web: WKWebView, _ script: String) async throws -> Any? {
        try await withCheckedThrowingContinuation { continuation in
            web.evaluateJavaScript(script) { value, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume(returning: value) }
            }
        }
    }

    @MainActor static func wait(_ check: () async throws -> Bool) async throws {
        for _ in 0..<100 {
            if try await check() { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw NSError(domain: "ReportUITests", code: 1, userInfo: [NSLocalizedDescriptionKey: "UI condition timed out"])
    }

    @MainActor static func run() async throws {
        fputs("UI: starting\n", stderr)
        var bridgeWindow: DashboardWindow?
        if let address = ProcessInfo.processInfo.environment["RT_REPORT_BRIDGE_TEST_URL"], let url = URL(string: address) {
            let view = DashboardWindow(url: url)
            bridgeWindow = view
            var opened = 0
            view.onReportBug = { opened += 1 }
            view.show()
            try await wait {
                await withCheckedContinuation { continuation in
                    view.evaluate("document.readyState === 'complete' && window.__rubbertermDesktop?.canReportBug === true") { result in
                        continuation.resume(returning: result as? Bool == true)
                    }
                }
            }
            view.evaluate("window.webkit.messageHandlers.remoteSession.postMessage({action:'report-bug'})")
            try await wait { opened == 1 }
            view.evaluate("window.webkit.messageHandlers.remoteSession.postMessage({action:'report-bug',extra:true});let frame=document.createElement('iframe');frame.srcdoc=\"<script>window.webkit.messageHandlers.remoteSession.postMessage({action:'report-bug'})<\\/script>\";document.body.append(frame)")
            try await Task.sleep(nanoseconds: 400_000_000)
            verify(opened == 1, "Invalid envelopes and child frames must not open native reporting")
            print("PASS: trusted dashboard report bridge; malformed and child-frame requests rejected")
        }
        defer { withExtendedLifetime(bridgeWindow) {} }
        var dashboard: DashboardWindow?
        var screenshot: NSImage?
        if let address = ProcessInfo.processInfo.environment["RT_REPORT_PREVIEW_URL"], let url = URL(string: address) {
            let view = DashboardWindow(url: url)
            dashboard = view
            view.show()
            try await wait {
                await withCheckedContinuation { continuation in
                    view.evaluate("document.querySelectorAll('.rd-group-phone').length > 0") { result in
                        continuation.resume(returning: result as? Bool == true)
                    }
                }
            }
            screenshot = await withCheckedContinuation { continuation in
                view.captureForReport { continuation.resume(returning: $0) }
            }
            verify(screenshot != nil, "Real dashboard snapshot must be available")
        }
        fputs("UI: dashboard ready\n", stderr)
        var savedURL: URL?
        var exportDestination: URL?
        var finishContext: CheckedContinuation<String?, Never>?
        var readiness = "Session 1: harness=codex; resume id=missing; cwd exists=yes"
        if let address = ProcessInfo.processInfo.environment["RT_REPORT_CONTEXT_TEST_URL"], let base = URL(string: address) {
            let transport = SessionTransport()
            let response = try await transport.perform(base: base, api: LaunchDestination(),
                params: ["method": "GET", "path": "/bugreport/context"])
            guard let text = BugReportData.resumeReadiness(from: response) else { fatalError("Live readiness item missing") }
            readiness = text
            print("PASS: native authenticated transport reads real server readiness")
        }
        let report = BugReportController(screenshot: screenshot, loadResumeReadiness: {
            await withCheckedContinuation { finishContext = $0 }
        }, chooseSaveDestination: { window, completion in
            if let exportDestination { completion(exportDestination); return }
            let picker = NSSavePanel()
            picker.beginSheetModal(for: window) { response in
                completion(response == .OK ? picker.url : nil)
            }
        }, onSaved: { savedURL = $0 })
        report.show()
        guard let panel = NSApp.windows.first(where: { $0.title == "Report a bug — DuckTerm" }),
              let web = panel.contentView as? WKWebView else { fatalError("Missing report window") }
        try await wait { try await js(web, "typeof window.updateReport === 'function'") as? Bool == true }
        try await wait { try await js(web, "document.activeElement.id === 'summary'") as? Bool == true }
        verify(report.isOpen)
        verify(try await js(web, "document.getElementById('save').disabled") as? Bool == true)
        try await wait { finishContext != nil }
        finishContext?.resume(returning: readiness)
        finishContext = nil
        try await wait { try await js(web, "!document.getElementById('save').disabled") as? Bool == true }
        let reviewedDiagnostics = try await js(web, "document.getElementById('logs').textContent") as? String ?? ""
        verify(reviewedDiagnostics.contains("Local server resume readiness:\n" + readiness))
        report.show()
        verify(NSApp.windows.filter { $0.title == panel.title && $0.isVisible }.count == 1)
        verify(try await js(web, "document.getElementById('report').checkValidity()") as? Bool == false)
        _ = try await js(web, "document.getElementById('summary').value='Bug report UI verification';document.getElementById('details').value='Local verification only. No email is sent.'")
        verify(try await js(web, "document.getElementById('report').checkValidity()") as? Bool == true)
        if screenshot == nil {
            verify(try await js(web, "document.getElementById('screenshot').disabled") as? Bool == true)
        }
        _ = try await js(web, "document.getElementById('review').click()")
        verify(try await js(web, "!document.getElementById('logs').hidden") as? Bool == true)
        _ = try await js(web, "document.getElementById('review').click()")
        // Test removing a filename containing HTML using the actual native bridge.
        _ = try await js(web, "window.updateReport({screenshot:'',diagnostics:'Local test',files:['<b>sample.txt</b>'],canEmail:false,status:'',busy:false})")
        verify(try await js(web, "document.querySelector('#files span').textContent === '<b>sample.txt</b>' && !document.querySelector('#files b')") as? Bool == true)
        verify(try await js(web, "document.getElementById('email').disabled") as? Bool == true)
        fputs("UI: testing cancelled save\n", stderr)
        // Trigger save, cancel the native sheet, and verify the form remains usable.
        _ = try await js(web, "document.getElementById('save').click()")
        try await wait { panel.attachedSheet is NSSavePanel }
        verify(!report.windowShouldClose(panel))
        (panel.attachedSheet as? NSSavePanel)?.cancel(nil)
        try await wait { try await js(web, "!document.getElementById('save').disabled") as? Bool == true }
        verify(report.windowShouldClose(panel))
        fputs("UI: testing ZIP export\n", stderr)
        // The cancelled sheet re-renders real report state, replacing the synthetic file list.
        verify(try await js(web, "document.querySelectorAll('#files .file').length") as? Int == 0)
        fputs("UI: testing layout\n", stderr)
        for width in [760, 440] {
            panel.setContentSize(NSSize(width: width, height: 720))
            try await Task.sleep(nanoseconds: 200_000_000)
            verify(try await js(web, "document.documentElement.scrollWidth <= window.innerWidth") as? Bool == true)
            _ = try await js(web, "document.getElementById('save').scrollIntoView({block:'end'})")
            verify(try await js(web, "document.getElementById('save').getBoundingClientRect().bottom <= innerHeight") as? Bool == true)
            _ = try await js(web, "window.scrollTo(0,0)")
            if let directory = ProcessInfo.processInfo.environment["RT_REPORT_SCREENSHOTS"] {
                let image: NSImage? = await withCheckedContinuation { continuation in
                    web.takeSnapshot(with: nil) { image, _ in continuation.resume(returning: image) }
                }
                if let tiff = image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
                   let png = bitmap.representation(using: .png, properties: [:]) {
                    try png.write(to: URL(fileURLWithPath: directory).appendingPathComponent("bug-report-\(width).png"))
                }
            }
        }
        // Exercise the native ZIP export, including screenshot/diagnostic opt-outs.
        let exportDirectory = FileManager.default.temporaryDirectory.appendingPathComponent("report-ui-export-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: exportDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: exportDirectory) }
        let archive = exportDirectory.appendingPathComponent("report.zip")
        exportDestination = exportDirectory // A directory cannot be replaced by the ZIP file.
        _ = try await js(web, "document.getElementById('save').click()")
        try await wait { try await js(web, "document.getElementById('status').textContent.includes('Could not save') && !document.getElementById('save').disabled") as? Bool == true }
        verify(report.isOpen, "Failed exports must keep the form available for retry")
        exportDestination = archive
        _ = try await js(web, "document.getElementById('screenshot').checked=false;document.getElementById('diagnostics').checked=false;document.getElementById('save').click()")
        try await wait { FileManager.default.fileExists(atPath: archive.path) }
        try await wait { savedURL == archive && !report.isOpen }
        fputs("UI: ZIP saved\n", stderr)
        let unzip = Process()
        unzip.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
        unzip.arguments = ["-Z1", archive.path]
        let pipe = Pipe()
        unzip.standardOutput = pipe
        try unzip.run()
        unzip.waitUntilExit()
        let listing = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        verify(unzip.terminationStatus == 0 && listing.contains("report.txt"))
        verify(!listing.contains("screenshot.png") && !listing.contains("diagnostics.txt"))
        var includedURL: URL?
        let includedArchive = exportDirectory.appendingPathComponent("included.zip")
        let secondReport = BugReportController(screenshot: nil, loadResumeReadiness: { readiness },
            chooseSaveDestination: { _, done in done(includedArchive) }, onSaved: { includedURL = $0 })
        secondReport.show()
        guard let secondPanel = NSApp.windows.first(where: { $0.title == panel.title && $0.isVisible }),
              let secondWeb = secondPanel.contentView as? WKWebView else { fatalError("Missing second report") }
        try await wait { try await js(secondWeb, "typeof window.updateReport === 'function'") as? Bool == true }
        try await wait { try await js(secondWeb, "!document.getElementById('save').disabled") as? Bool == true }
        let includedDiagnostics = try await js(secondWeb, "document.getElementById('logs').textContent") as? String ?? ""
        _ = try await js(secondWeb, "document.getElementById('summary').value='Resume verification';document.getElementById('save').click()")
        try await wait { includedURL == includedArchive && !secondReport.isOpen }
        let extract = Process()
        extract.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
        extract.arguments = ["-p", includedArchive.path, "*/diagnostics.txt"]
        let extracted = Pipe()
        extract.standardOutput = extracted
        try extract.run()
        let savedDiagnostics = String(data: extracted.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        extract.waitUntilExit()
        verify(extract.terminationStatus == 0 && savedDiagnostics == includedDiagnostics, "ZIP must contain exactly the reviewed diagnostic snapshot")
        verify(savedDiagnostics.contains(readiness))
        let failedReport = BugReportController(screenshot: nil, loadResumeReadiness: {
            throw NSError(domain: "Secret path", code: 1, userInfo: [NSLocalizedDescriptionKey: "/private/owner/token"])
        })
        failedReport.show()
        guard let failedPanel = NSApp.windows.first(where: { $0.title == panel.title && $0.isVisible }),
              let failedWeb = failedPanel.contentView as? WKWebView else { fatalError("Missing failed-context report") }
        try await wait { try await js(failedWeb, "typeof window.updateReport === 'function' && !document.getElementById('save').disabled") as? Bool == true }
        let failedDiagnostics = try await js(failedWeb, "document.getElementById('logs').textContent") as? String ?? ""
        verify(failedDiagnostics.contains("could not read local server diagnostics") && !failedDiagnostics.contains("/private/owner/token"))
        _ = try await js(failedWeb, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        try await wait { !failedReport.isOpen }
        var finishClosed: CheckedContinuation<String?, Never>?
        let closedReport = BugReportController(screenshot: nil, loadResumeReadiness: {
            await withCheckedContinuation { finishClosed = $0 }
        })
        closedReport.show()
        guard let closedPanel = NSApp.windows.first(where: { $0.title == panel.title && $0.isVisible }),
              let closedWeb = closedPanel.contentView as? WKWebView else { fatalError("Missing loading report") }
        try await wait {
            guard finishClosed != nil else { return false }
            return try await js(closedWeb, "typeof window.updateReport === 'function'") as? Bool == true
        }
        _ = try await js(closedWeb, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        try await wait { !closedReport.isOpen }
        finishClosed?.resume(returning: "Late result must not reopen the panel")
        finishClosed = nil
        await Task.yield()
        verify(!closedReport.isOpen)
        withExtendedLifetime(dashboard) {}
        print("PASS: native WK form validation, readiness loading/review, exact included ZIP, diagnostic opt-out, sanitized fetch failure, safe filenames, save cancellation/export, close guard, narrow layout, Escape")
    }

    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        Task { @MainActor in
            do { try await run(); exit(0) }
            catch { fputs("FAIL: \(error)\n", stderr); exit(1) }
        }
        Task.detached {
            try? await Task.sleep(nanoseconds: 45_000_000_000)
            fputs("FAIL: native UI test exceeded 45 seconds\n", stderr)
            exit(1)
        }
        app.run()
    }
}
