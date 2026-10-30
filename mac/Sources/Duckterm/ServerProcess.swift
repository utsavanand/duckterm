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
        // Drain stderr for the server's lifetime, keeping only the tail that
        // explains a refusal (a workspace or port owned by someone else).
        let log = StartupLog()
        let errors = Pipe()
        errors.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil } else { log.append(data) }
        }
        proc.standardError = errors
        do {
            try proc.run()
        } catch {
            AppDiagnostics.shared.record("Server launch failed", code: (error as NSError).code)
            return false
        }
        AppDiagnostics.shared.record("Server process started")
        task = proc
        for _ in 0..<40 {  // bounded attempts; each health request can take up to 3s
            if await isUp() {
                guard await matchesEdition() else { return false }
                if !proc.isRunning { task = nil }
                AppDiagnostics.shared.record("Local server ready")
                return true
            }
            if !proc.isRunning { break }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        if !proc.isRunning {
            errors.fileHandleForReading.readabilityHandler = nil
            log.append(errors.fileHandleForReading.readDataToEndOfFile())
            task = nil // Only the exited child was ours; never own the existing server.
            let stderr = log.text
            if let reason = Self.startupReason(stderr: stderr, port: AppIdentity.localPort) {
                startupError = reason
                if await Self.reuseExistingServer(
                    stderr: stderr, port: AppIdentity.localPort,
                    isUp: { await self.isUp() }, matchesEdition: { await self.matchesEdition() }
                ) {
                    AppDiagnostics.shared.record("Connected to existing local server after startup refusal")
                    return true
                }
                AppDiagnostics.shared.record("Server refused to start")
                return false
            }
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

    /// The backend reports a refusal as "duckterm: <reason>"; a foreign program
    /// on the port surfaces as a bind error.
    static func startupReason(stderr: String, port: Int) -> String? {
        let lines = stderr.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
        if let line = lines.last(where: { $0.hasPrefix("duckterm: ") }) {
            // Shown in an alert: printable text only, one line, bounded.
            let printable = line.dropFirst("duckterm: ".count).unicodeScalars
                .map { CharacterSet.controlCharacters.contains($0) ? " " : String($0) }.joined()
            let reason = printable.split(separator: " ").joined(separator: " ")
            return reason.isEmpty ? nil : String(reason.prefix(300))
        }
        if let line = lines.last(where: {
            $0 == "Port \(port) is already in use." ||
                ($0.hasPrefix("another DuckTerm server") && $0.contains("already owns"))
        }) {
            return startupReason(stderr: "duckterm: " + line, port: port)
        }
        if stderr.contains("Address already in use") {
            return "Port \(port) is in use by another program. Quit it, then reopen this app."
        }
        return nil
    }

    /// A slow existing server can miss the first probe, then refuse our child.
    /// Retry only ownership/bind refusals, with health and edition checks. The
    /// caller must clear its exited child: reusing a server never owns its PID.
    static func reuseExistingServer(
        stderr: String, port: Int,
        isUp: () async -> Bool, matchesEdition: () async -> Bool,
        pause: () async throws -> Void = { try await Task.sleep(nanoseconds: 500_000_000) }
    ) async -> Bool {
        let collision = stderr.contains("Port \(port) is already in use.") ||
            stderr.contains("Address already in use") ||
            (stderr.contains("another DuckTerm server") && stderr.contains("already owns"))
        guard collision else { return false }
        for attempt in 0..<5 {
            guard !Task.isCancelled else { return false }
            if await isUp() { return await matchesEdition() }
            if attempt < 4 {
                do { try await pause() } catch { return false }
            }
        }
        return false
    }

    func stop() {
        task?.terminate()
        task = nil
    }

    private func isUp() async -> Bool {
        var req = URLRequest(url: url)
        req.timeoutInterval = 3
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
            let http = resp as? HTTPURLResponse
        else { return false }
        // 401 is the locked page: the app signs in with the token file.
        return [200, 401].contains(http.statusCode) && http.value(forHTTPHeaderField: "X-Duckterm") == "1"
    }
}

/// Bounded and thread-safe: the pipe handler runs off the main thread.
final class StartupLog: @unchecked Sendable {
    private let lock = NSLock()
    private var tail = ""

    func append(_ data: Data) {
        lock.lock()
        tail = String((tail + String(decoding: data, as: UTF8.self)).suffix(4096))
        lock.unlock()
    }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return tail
    }
}
