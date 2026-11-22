// Standalone native installer integration fixture. No DuckTerm workspace,
// owner credentials, Keychain keys, or production app paths are used.
import AppKit
import Foundation

enum LaunchDestination {
    enum Failure: LocalizedError {
        case message(String)
        var errorDescription: String? { if case .message(let message) = self { return message }; return nil }
    }
}
@MainActor final class Probe: NSObject, NSApplicationDelegate {
    var updater: AppUpdater!
    var root: URL!
    func applicationDidFinishLaunching(_ notification: Notification) {
        root = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "DuckTermUpdateProbeRoot") as! String)
        let defaults = UserDefaults.standard
        updater = AppUpdater(defaults: defaults)
        updater.preflight = {}
        updater.prepareRestart = { [self] in try Data("ready".utf8).write(to: root.appendingPathComponent("restart-ready")) }
        updater.onChange = { [self] state in
            let data = try! JSONSerialization.data(withJSONObject: state)
            try! data.write(to: root.appendingPathComponent("state.json"))
            let phase = state["phase"] as? String
            if phase == "available" { Task { _ = try await self.updater.perform("install") } }
            if phase == "succeeded" || phase == "failed" { NSApp.terminate(nil) }
        }
        let backend = try? String(contentsOf: Bundle.main.resourceURL!.appendingPathComponent("backend-version"), encoding: .utf8)
        updater.verifyRelaunch(backend: backend)
        if updater.state["phase"] as? String != "succeeded" {
            Task { _ = try await updater.perform("check") }
        }
    }
}
let app = NSApplication.shared
let delegate = MainActor.assumeIsolated { Probe() }
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
