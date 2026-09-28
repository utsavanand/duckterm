import Foundation

/// Starts and stops the `duckterm serve` process so the app owns the server's
/// lifecycle. Finds the binary on PATH (or common install dirs), spawns it, and
/// polls until the dashboard answers.
final class ServerProcess {
    let url = URL(string: "http://127.0.0.1:\(AppIdentity.localPort)")!
    private var task: Process?
    private(set) var missingTmux = false

    private var bundledPython: String? {
        guard let path = Bundle.main.resourceURL?
            .appendingPathComponent("python/bin/python3.13").path,
            FileManager.default.isExecutableFile(atPath: path) else { return nil }
        return path
    }

    private func serverEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let resources = Bundle.main.resourceURL!.path
        let search = [resources + "/bin", env["PATH"] ?? "",
                      "/opt/homebrew/bin", "/usr/local/bin", NSHomeDirectory() + "/.local/bin",
                      "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        env["PATH"] = search.filter { !$0.isEmpty }.joined(separator: ":")
        env["DUCKTERM_BUNDLED_TMUX"] = resources + "/tmux/bin/tmux"
        if bundledPython != nil {
            env.removeValue(forKey: "PYTHONHOME")
            env["PYTHONPATH"] = resources + "/backend"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        }
        if AppIdentity.isTest {
            for key in ["DUCKTERM_HOME", "DUCKTERM_TMUX_SOCKET", "DUCKTERM_URL", "DUCKTERM_PORT", "DUCKTERM_HOSTED"] {
                env.removeValue(forKey: key)
            }
            env["DUCKTERM_INSTANCE"] = Bundle.main.object(forInfoDictionaryKey: "DucktermTestInstance") as? String ?? "test"
        }
        // A test-only allowlist proves first launch without host-installed tools.
        if AppIdentity.isTest,
           Bundle.main.object(forInfoDictionaryKey: "DucktermTestSystemTools") as? Bool == false {
            env["PATH"] = resources + "/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        }
        missingTmux = !env["PATH"]!.split(separator: ":").contains {
            FileManager.default.isExecutableFile(atPath: String($0) + "/tmux")
        }
        if let python = bundledPython {
            let probe = Process()
            probe.executableURL = URL(fileURLWithPath: python)
            probe.arguments = ["-s", "-B", "-m", "duckterm.agents.tmux"]
            probe.environment = env
            probe.standardOutput = FileHandle.nullDevice
            probe.standardError = FileHandle.nullDevice
            do {
                try probe.run()
                let deadline = Date().addingTimeInterval(15)
                while probe.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
                if probe.isRunning { probe.terminate() }
                else { missingTmux = probe.terminationStatus == 1 }
            } catch { AppDiagnostics.shared.record("tmux availability check failed") }
        }
        if missingTmux { AppDiagnostics.shared.record("No usable bundled or system tmux") }
        env["DUCKTERM_NO_BROWSER"] = "1"
        return env
    }

    /// Locate the `duckterm` executable. We can't rely on a GUI app inheriting
    /// the user's shell PATH, so check the usual install locations explicitly.
    private func findBinary() -> String? {
        if let python = bundledPython { return python }
        // A malformed test bundle must never touch the installed production CLI.
        if AppIdentity.isTest { return nil }
        let candidates = [
            "/opt/homebrew/bin/duckterm",
            "/usr/local/bin/duckterm",
            "\(NSHomeDirectory())/.local/bin/duckterm",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        // Fall back to `which` via a login shell, which sources the user's PATH.
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: "/bin/zsh")
        probe.arguments = ["-lc", "command -v duckterm"]
        let pipe = Pipe()
        probe.standardOutput = pipe
        try? probe.run()
        probe.waitUntilExit()
        let out = String(
            data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
        )?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let out, !out.isEmpty, FileManager.default.isExecutableFile(atPath: out) {
            return out
        }
        return nil
    }

    /// Returns true once the server is reachable. Starts it if it isn't already
    /// running (an external `duckterm serve` is reused, not duplicated).
    func start() async -> Bool {
        let env = serverEnvironment()
        if await isUp() {
            AppDiagnostics.shared.record("Connected to existing local server")
            return true
        }
        guard let bin = findBinary() else {
            AppDiagnostics.shared.record("Server CLI not found")
            return false
        }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: bin)
        proc.arguments = bundledPython != nil ? ["-s", "-B", "-m", "duckterm.cli", "serve"] : ["serve"]
        // The app IS the dashboard window — without this, serve would also
        // open the default browser on the same URL.
        proc.environment = env
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
        } catch {
            AppDiagnostics.shared.record("Server launch failed", code: (error as NSError).code)
            return false
        }
        AppDiagnostics.shared.record("Server process started")
        task = proc
        for _ in 0..<40 {  // up to ~8s for the server to bind
            if await isUp() {
                AppDiagnostics.shared.record("Local server ready")
                return true
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        AppDiagnostics.shared.record("Server startup timed out")
        return false
    }

    func stop() {
        task?.terminate()
        task = nil
    }

    private func isUp() async -> Bool {
        var req = URLRequest(url: url)
        req.timeoutInterval = 1
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
            let http = resp as? HTTPURLResponse
        else { return false }
        return http.statusCode == 200 && http.value(forHTTPHeaderField: "X-Duckterm") == "1"
    }
}
