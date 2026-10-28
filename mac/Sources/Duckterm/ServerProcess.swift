import Foundation

/// Owns the app's private backend process; existing matching servers are reused.
final class ServerProcess {
    let url = URL(string: "http://127.0.0.1:\(AppIdentity.localPort)")!
    private var task: Process?
    private(set) var startupError = "The bundled local server could not start. Check Duckterm's diagnostics and try reopening the app."

    /// Returns true once the server is reachable. Starts it if it isn't already
    /// running (an external `duckterm serve` is reused, not duplicated).
    func start() async -> Bool {
        if await isUp() {
            guard await matchesEdition() else { return false }
            AppDiagnostics.shared.record("Connected to existing local server")
            return true
        }
        var env = ServerEnvironment.make(inheriting: ProcessInfo.processInfo.environment)
        guard let resources = Bundle.main.resourceURL else { return false }
        let runtime = BundledRuntime(resources: resources)
        do {
            try runtime.validate()
        } catch {
            startupError = error.localizedDescription
            AppDiagnostics.shared.record("Bundled runtime is incomplete")
            return false
        }
        if AppIdentity.isTest,
           Bundle.main.object(forInfoDictionaryKey: "DucktermTestSystemTools") as? Bool == false {
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        }
        env = runtime.environment(inheriting: env)
        let proc = Process()
        proc.executableURL = runtime.python
        proc.arguments = ["-I", "-B", "-m", "duckterm.cli", "serve"]
        if let edition = AppIdentity.edition {
            proc.arguments?.append(contentsOf: ["--edition", edition])
        }
        proc.environment = Self.profileEnvironment(inheriting: env, profile: AppIdentity.profile)
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
                guard await matchesEdition() else { return false }
                AppDiagnostics.shared.record("Local server ready")
                return true
            }
            if !proc.isRunning { break }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        AppDiagnostics.shared.record("Server startup timed out")
        return false
    }

    /// Inherited profile facets or a shell's session enrollment (from launchctl,
    /// a terminal or an agent pane) must never retarget this app's server.
    static func profileEnvironment(inheriting environment: [String: String], profile: AppProfile?) -> [String: String] {
        var result = environment
        for key in ["DUCKTERM_HOME", "DUCKTERM_TMUX_SOCKET", "DUCKTERM_PORT", "DUCKTERM_URL",
                    "DUCKTERM_INSTANCE", "DUCKTERM_DESKTOP_PROFILE", "DUCKTERM_APP_PROFILE", "DUCKTERM_EDITION",
                    "DUCKTERM_HOSTED", "DUCKTERM_HOST", "DUCKTERM_SESSION_KEY", "DUCKTERM_SESSION_TOKEN_FILE",
                    "DUCKTERM_INTERNAL"] {
            result.removeValue(forKey: key)
        }
        result.merge(profile?.environment ?? [:]) { _, new in new }
        // The app is the dashboard window; serve must not also open a browser.
        result["DUCKTERM_NO_BROWSER"] = "1"
        return result
    }

    private func matchesEdition() async -> Bool {
        guard AppIdentity.edition != nil else { return true }
        do {
            let response = try await LaunchDestination().perform(base: url, operation: "edition", params: [:])
            try DesktopEdition(response: response).validate(edition: AppIdentity.edition, profile: AppIdentity.profile)
            return true
        } catch {
            startupError = error.localizedDescription
            AppDiagnostics.shared.record("Local server edition could not be verified")
            return false
        }
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
