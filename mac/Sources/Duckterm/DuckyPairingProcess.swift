import Darwin
import Foundation

/// Owner-only stdin/stdout ceremony. Invitations never enter a web view, log or argv.
@MainActor
final class DuckyPairingProcess: DuckyPairingTransport {
    struct Command: Sendable {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
    }
    var onEvent: ((DuckyPairingEvent) -> Void)?
    private let command: () async throws -> Command
    private var process: Process?
    private var input: FileHandle?
    private var frames = DuckyPairingFrames()
    private var terminal = false
    private var cancelled = false
    private var invitationReceived = false

    init(command: @escaping () async throws -> Command = DuckyPairingProcess.bundledCommand) {
        self.command = command
    }

    static func environment(inheriting inherited: [String: String], home: URL, profile: AppProfile?) -> [String: String] {
        // Do not inherit session enrollment, owner/provider tokens, DYLD/Python
        // injection, shell profiles or a different edition's instance facets.
        var safe: [String: String] = ["HOME": home.path]
        for key in ["LANG", "LC_CTYPE", "TMPDIR"] { safe[key] = inherited[key] }
        safe = ServerEnvironment.make(inheriting: safe, homeDirectory: home.path)
        safe = ServerProcess.profileEnvironment(inheriting: safe, profile: profile)
        safe["DUCKTERM_NO_KEYCHAIN"] = "1"
        safe["DUCKTERM_SUMMARIZER"] = "off"
        return safe
    }

    static func bundledCommand() async throws -> Command {
        guard let resources = Bundle.main.resourceURL else { throw DuckyPairingEvent.Failure.invalid }
        let bundle = Bundle.main.bundleURL
        let bundled = Bundle.main.object(forInfoDictionaryKey: "DucktermBundledRuntime") as? Bool == true
        let profile = AppIdentity.profile
        let home = AppIdentity.userHome(isTest: AppIdentity.isTest, environment: ProcessInfo.processInfo.environment)
        let runtime = try await Task.detached(priority: .userInitiated) {
            try StableRuntimeStore(home: home).runtime(bundle: bundle, resources: resources, bundled: bundled, profile: profile?.id)
        }.value
        let env = runtime.environment(inheriting: environment(inheriting: ProcessInfo.processInfo.environment, home: home, profile: profile))
        return Command(executable: runtime.python,
                       arguments: ["-I", "-B", "-m", "duckterm.cli", "ducky", "pair", "--json"], environment: env)
    }

    func start() async throws {
        let configuration = try await command()
        guard !cancelled else { return }
        let child = Process(), stdin = Pipe(), stdout = Pipe()
        child.executableURL = configuration.executable
        child.arguments = configuration.arguments
        child.environment = configuration.environment
        child.standardInput = stdin; child.standardOutput = stdout
        child.standardError = FileHandle.nullDevice // Never surface raw invitation/exception text.
        // A child exit racing a decision must throw, never deliver SIGPIPE to the app.
        _ = fcntl(stdin.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
        try child.run()
        process = child; input = stdin.fileHandleForWriting
        let reader = stdout.fileHandleForReading
        Task.detached { [self] in
            // FileHandle.read(upToCount:) can wait to fill the requested size on
            // a pipe. A single read must deliver each flushed JSONL frame while
            // the child is waiting for our reply.
            var bytes = [UInt8](repeating: 0, count: 4096)
            while true {
                let count = Darwin.read(reader.fileDescriptor, &bytes, bytes.count)
                if count > 0 { await self.consume(Data(bytes.prefix(count))) }
                else if count == 0 { break }
                else if errno != EINTR { await self.failed(); break }
            }
            child.waitUntilExit()
            try? reader.close()
            await self.ended()
        }
    }

    func send(action: String, deviceID: String?) throws {
        guard !cancelled, !terminal, process?.isRunning == true, let input else { throw DuckyPairingEvent.Failure.invalid }
        var body = ["action": action]
        if let deviceID { body["device_id"] = deviceID }
        guard action == "cancel" || (["approve", "deny"].contains(action) && deviceID?.range(of: "^[a-f0-9]{32}\\z", options: .regularExpression) != nil) else { throw DuckyPairingEvent.Failure.invalid }
        var data = try JSONSerialization.data(withJSONObject: body)
        data.append(10)
        try input.write(contentsOf: data)
    }

    func cancel() {
        guard !cancelled else { return }
        if !terminal { try? send(action: "cancel", deviceID: nil) }
        cancelled = true
        try? input?.close(); input = nil // EOF is also a server-side cancellation.
        guard let child = process else { return }
        // Certificate setup may still be finishing an atomic identity write.
        // Closing the panel is immediate; let setup observe EOF and clean up.
        let grace: UInt64 = invitationReceived ? 3_000_000_000 : 75_000_000_000
        Task.detached {
            try? await Task.sleep(nanoseconds: grace)
            if child.isRunning {
                child.terminate()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                if child.isRunning { kill(child.processIdentifier, SIGKILL) }
            }
        }
    }

    private func consume(_ data: Data) {
        guard !cancelled else { return }
        do {
            for event in try frames.append(data) {
                guard !terminal else { failed(); return }
                switch event {
                case .invitation: invitationReceived = true
                case .approved, .denied, .expired, .error, .cancelled: terminal = true
                default: break
                }
                onEvent?(event)
                if terminal { cancel(); return }
            }
        } catch { failed() }
    }
    private func failed() {
        if !cancelled { onEvent?(.error("pairing_failed")) }
        cancel()
    }
    private func ended() {
        if !cancelled && (!terminal || !frames.complete) { onEvent?(.error("pairing_failed")) }
        try? input?.close(); input = nil; process = nil
    }
}
