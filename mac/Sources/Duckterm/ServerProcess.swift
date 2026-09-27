import Foundation

/// Starts and stops the `duckterm serve` process so the app owns the server's
/// lifecycle. Finds the binary on PATH (or common install dirs), spawns it, and
/// polls until the dashboard answers.
final class ServerProcess {
    let url = URL(string: "http://127.0.0.1:\(AppIdentity.localPort)")!
    private var task: Process?

    /// Locate the `duckterm` executable. We can't rely on a GUI app inheriting
    /// the user's shell PATH, so check the usual install locations explicitly.
    private func findBinary() -> String? {
        if AppIdentity.isTest {
            guard let python = Bundle.main.object(forInfoDictionaryKey: "DucktermTestPython") as? String,
                  FileManager.default.isExecutableFile(atPath: python) else { return nil }
            return python
        }
        let candidates = [
            "/opt/homebrew/bin/duckterm",
            "/usr/local/bin/duckterm",
            "\(NSHomeDirectory())/.local/bin/duckterm",
            // Dev checkout: the venv the dashboard is developed against.
            "\(NSHomeDirectory())/workspace-2026/duckterm/.venv/bin/duckterm",
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
        proc.arguments = AppIdentity.isTest ? ["-m", "duckterm.cli", "serve"] : ["serve"]
        // The app IS the dashboard window — without this, serve would also
        // open the default browser on the same URL.
        var env = ProcessInfo.processInfo.environment
        if AppIdentity.isTest {
            for key in ["DUCKTERM_HOME", "DUCKTERM_TMUX_SOCKET", "DUCKTERM_URL", "DUCKTERM_PORT", "DUCKTERM_HOSTED"] {
                env.removeValue(forKey: key)
            }
            env["DUCKTERM_INSTANCE"] = "test"
            env["PYTHONPATH"] = Bundle.main.resourceURL!.appendingPathComponent("backend").path
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        }
        env["DUCKTERM_NO_BROWSER"] = "1"
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
