import Darwin
import Foundation

@MainActor
protocol RuntimeSetupTransport: AnyObject {
    var onEvent: ((RuntimeSetupEvent) -> Void)? { get set }
    var onOutput: ((Data) async -> Void)? { get set }
    var onEnd: (() -> Void)? { get set }
    func start() async throws
    func send(_ command: RuntimeSetupCommand) throws
    func cancel()
}

/// An ephemeral native-owned helper. Terminal bytes never enter diagnostics or
/// agent history; a final event is delivered only after helper cleanup and exit.
@MainActor
final class RuntimeSetupProcess: RuntimeSetupTransport {
    struct Command: Sendable {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
        var selectedExecutable: RuntimeSetupExecutable? = nil
    }
    var onEvent: ((RuntimeSetupEvent) -> Void)?
    var onOutput: ((Data) async -> Void)?
    var onEnd: (() -> Void)?
    private let configuration: () async throws -> Command
    private var frames: RuntimeSetupFrames
    private var child: Process?
    private var input: FileHandle?
    private var terminal: RuntimeSetupEvent?
    private var reachedEOF = false
    private var processStatus: Int32?
    private var cancelled = false
    private var ended = false
    private var started = false
    private var protocolFailed = false
    private var pendingInput = Data()
    private var inputWriter: Task<Void, Never>?
    private static let maximumPendingInput = 65_536

    init(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction,
         configuration: @escaping () async throws -> Command) {
        self.configuration = configuration
        frames = RuntimeSetupFrames(runtime: runtime, action: action)
    }

    convenience init(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction, executable: URL? = nil) {
        self.init(runtime: runtime, action: action) {
            try await Self.preparedCommand(executable: executable) {
                try await Self.bundledCommand(runtime: runtime, action: action, executable: executable)
            }
        }
    }

    /// A native-selected executable must still be the same regular executable
    /// after potentially slow stable-runtime preparation. No browser path is used.
    static func preparedCommand(executable: URL?, prepare: () async throws -> Command) async throws -> Command {
        let before = try executable.map(RuntimeSetupExecutable.validate)
        var command = try await prepare()
        guard try executable.map(RuntimeSetupExecutable.validate) == before else {
            throw RuntimeSetupProtocolError.invalidFrame
        }
        command.selectedExecutable = before
        return command
    }

    static func bundledCommand(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction,
                               executable: URL?) async throws -> Command {
        guard let resources = Bundle.main.resourceURL else { throw RuntimeSetupProtocolError.invalidFrame }
        let bundle = Bundle.main.bundleURL
        let bundled = Bundle.main.object(forInfoDictionaryKey: "DucktermBundledRuntime") as? Bool == true
        let home = AppIdentity.userHome(isTest: AppIdentity.isTest, environment: ProcessInfo.processInfo.environment)
        let profile = AppIdentity.profile
        let stable = try await Task.detached(priority: .userInitiated) {
            try StableRuntimeStore(home: home).runtime(bundle: bundle, resources: resources,
                                                       bundled: bundled, profile: profile?.id)
        }.value
        // Preserve real harness profile/provider configuration. The helper
        // strips injection, enrollment and installation-inappropriate secrets.
        var environment = ProcessInfo.processInfo.environment
        environment["HOME"] = home.path
        environment = ServerEnvironment.make(inheriting: environment, homeDirectory: home.path)
        var args = ["-I", "-B", "-m", "duckterm.runtimes.setup", "--runtime", runtime.rawValue,
                    "--action", action.rawValue]
        if let executable { args += ["--executable", executable.path] }
        return Command(executable: stable.python, arguments: args,
                       environment: stable.environment(inheriting: environment))
    }

    func start() async throws {
        guard !started else { throw RuntimeSetupProtocolError.unexpectedEvent }
        started = true
        let command: Command
        do { command = try await configuration() }
        catch { finishWithoutProcess(.error(.setupUnavailable)); return }
        guard !cancelled else { finishWithoutProcess(.error(.cancelled)); return }
        let process = Process(), stdin = Pipe(), stdout = Pipe()
        process.executableURL = command.executable
        process.arguments = command.arguments
        process.environment = command.environment
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = FileHandle.nullDevice
        let descriptor = stdin.fileHandleForWriting.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0,
              fcntl(descriptor, F_SETNOSIGPIPE, 1) == 0 else {
            finishWithoutProcess(.error(.setupUnavailable)); return
        }
        process.terminationHandler = { [weak self] process in
            Task { @MainActor in
                self?.processStatus = process.terminationStatus
                self?.finishIfEnded()
            }
        }
        do {
            // Configuration may have suspended while preparing the bundled
            // runtime. Recheck at the final synchronous launch boundary too.
            if let selected = command.selectedExecutable {
                guard try RuntimeSetupExecutable.validate(selected.source) == selected else {
                    throw RuntimeSetupProtocolError.invalidFrame
                }
            }
            try process.run()
        }
        catch { finishWithoutProcess(.error(.setupUnavailable)); return }
        child = process
        input = stdin.fileHandleForWriting
        let reader = stdout.fileHandleForReading
        Task.detached { [weak self] in
            var bytes = [UInt8](repeating: 0, count: 4096)
            while true {
                let count = Darwin.read(reader.fileDescriptor, &bytes, bytes.count)
                if count > 0 { await self?.consume(Data(bytes.prefix(count))) }
                else if count == 0 { break }
                else if errno != EINTR { await self?.failedProtocol(); break }
            }
            try? reader.close()
            await self?.finishedReading()
        }
    }

    func send(_ command: RuntimeSetupCommand) throws {
        guard !cancelled, !ended, terminal == nil, child?.isRunning == true, input != nil else {
            throw RuntimeSetupProtocolError.unexpectedEvent
        }
        let bytes = try command.encode()
        guard pendingInput.count + bytes.count <= Self.maximumPendingInput else {
            throw RuntimeSetupProtocolError.tooLarge
        }
        pendingInput.append(bytes)
        do { try flushInput() }
        catch { failedProtocol(); throw RuntimeSetupProtocolError.unexpectedEvent }
        guard !pendingInput.isEmpty, inputWriter == nil else { return }
        inputWriter = Task { [weak self] in
            guard let self else { return }
            defer { inputWriter = nil }
            let deadline = ContinuousClock.now.advanced(by: .seconds(1))
            while !pendingInput.isEmpty, !cancelled, !ended, !Task.isCancelled {
                if ContinuousClock.now >= deadline { failedProtocol(); return }
                do {
                    try await Task.sleep(nanoseconds: 5_000_000)
                    try flushInput()
                } catch {
                    if !cancelled && !ended { failedProtocol() }
                    return
                }
            }
        }
    }

    private func flushInput() throws {
        guard let input else { throw RuntimeSetupProtocolError.unexpectedEvent }
        while !pendingInput.isEmpty {
            let count = pendingInput.withUnsafeBytes { bytes in
                Darwin.write(input.fileDescriptor, bytes.baseAddress, bytes.count)
            }
            if count > 0 { pendingInput.removeFirst(count) }
            else if count == -1 && (errno == EAGAIN || errno == EWOULDBLOCK) { return }
            else if count == -1 && errno == EINTR { continue }
            else { throw RuntimeSetupProtocolError.unexpectedEvent }
        }
    }

    private func closeInput() {
        inputWriter?.cancel()
        pendingInput.removeAll()
        try? input?.close(); input = nil
    }

    func cancel() {
        guard !cancelled, !ended else { return }
        cancelled = true
        // A best-effort cancel frame is useful, but EOF is authoritative even
        // when the queue is full or the helper has stopped consuming input.
        if let bytes = try? RuntimeSetupCommand.cancel.encode(),
           pendingInput.count + bytes.count <= Self.maximumPendingInput {
            pendingInput.append(bytes)
            try? flushInput()
        }
        closeInput()
        guard let process = child else { return }
        Task.detached {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            // The helper handles TERM by cancelling/reaping its owned group.
            // SIGKILL here would bypass that cleanup and strand the installer.
            if process.isRunning { process.terminate() }
        }
    }

    private func consume(_ data: Data) async {
        guard !ended, !protocolFailed else { return }
        do {
            for event in try frames.append(data) {
                switch event {
                case .status, .exited, .error: terminal = event
                case .ready: if !cancelled { onEvent?(event) }
                case .output(let bytes): if !cancelled { await onOutput?(bytes) }
                }
            }
        } catch { failedProtocol() }
    }

    private func failedProtocol() {
        guard !protocolFailed else { return }
        protocolFailed = true
        terminal = .error(.transportUnavailable)
        cancel()
    }

    private func finishedReading() {
        reachedEOF = true
        if !protocolFailed {
            do { try frames.finish() }
            catch { terminal = .error(cancelled ? .cancelled : .transportUnavailable) }
        }
        finishIfEnded()
    }

    private func finishIfEnded() {
        guard !ended, reachedEOF, let processStatus else { return }
        let result: RuntimeSetupEvent
        if protocolFailed { result = .error(.transportUnavailable) }
        else if cancelled { result = .error(.cancelled) }
        else if processStatus != 0, case .error = terminal { result = terminal! }
        else if processStatus != 0 { result = .error(.setupUnavailable) }
        else { result = terminal ?? .error(.transportUnavailable) }
        finishWithoutProcess(result)
    }

    private func finishWithoutProcess(_ event: RuntimeSetupEvent) {
        guard !ended else { return }
        ended = true
        closeInput()
        child?.terminationHandler = nil
        child = nil
        onEvent?(event)
        onEnd?()
    }
}
