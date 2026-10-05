import AppKit
import WebKit

@main struct SettingsPageUITests {
    @MainActor static func run() async throws {
        guard let address = ProcessInfo.processInfo.environment["RT_SETTINGS_TEST_URL"], let url = URL(string: address) else { fatalError("Isolated dashboard URL required") }
        let window = DashboardWindow(url: url)
        var states: [[String: Any]] = [], copied: [String] = []
        window.onSettingsState = { states.append($0) }
        window.onCopyDiagnostics = { copied.append($0) }
        window.show()
        func js(_ code: String) async -> Any? { await withCheckedContinuation { continuation in window.evaluate(code) { continuation.resume(returning: $0) } } }
        func wait(_ check: () async -> Bool) async throws {
            for _ in 0..<100 { if await check() { return }; try await Task.sleep(nanoseconds: 100_000_000) }
            throw NSError(domain: "SettingsUI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Native Settings condition timed out"])
        }
        try await wait { !states.isEmpty }
        window.dispatch(name: "duckterm-menu", detail: ["action": "settings", "value": "Appearance"])
        try await wait { await js("document.querySelector('.rd-settings-content h1')?.textContent === 'Appearance'") as? Bool == true }
        window.dispatch(name: "duckterm-menu", detail: ["action": "theme", "value": "light"])
        try await wait { states.last?["theme"] as? String == "light" }
        window.dispatch(name: "duckterm-menu", detail: ["action": "density", "value": "compact"])
        try await wait { states.last?["density"] as? String == "compact" }
        window.dispatch(name: "duckterm-menu", detail: ["action": "diagnostics"])
        try await wait { copied.count == 1 }
        precondition(copied[0].contains("Surface: Mac app"))
        let before = states.count
        _ = await js("window.webkit.messageHandlers.remoteSession.postMessage({action:'settings-state',theme:'bad'});window.webkit.messageHandlers.remoteSession.postMessage({action:'copy-diagnostics',text:'bad',extra:true});const f=document.createElement('iframe');f.srcdoc=\"<script>window.webkit.messageHandlers.remoteSession.postMessage({action:'copy-diagnostics',text:'frame'})<\\/script>\";document.body.append(f)")
        try await Task.sleep(nanoseconds: 300_000_000)
        precondition(states.count == before && copied.count == 1, "Malformed and child-frame messages must be rejected")
        if let directory = ProcessInfo.processInfo.environment["RT_SETTINGS_SCREENSHOTS"] {
            for theme in ["dark", "light"] {
                window.dispatch(name: "duckterm-menu", detail: ["action": "theme", "value": theme])
                try await wait { states.last?["theme"] as? String == theme }
                try await Task.sleep(nanoseconds: 200_000_000)
                let image: NSImage? = await withCheckedContinuation { c in window.captureForReport { c.resume(returning: $0) } }
                if let tiff = image?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) { try png.write(to: URL(fileURLWithPath: directory).appendingPathComponent("settings-native-\(theme).png")) }
            }
        }
        print("PASS: native Settings navigation, theme/density synchronization, clipboard bridge and frame validation")
        withExtendedLifetime(window) {}
    }
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        Task { @MainActor in
            do { try await run(); exit(0) } catch { fputs("\(error)\n", stderr); exit(1) }
        }
        app.run()
    }
}
