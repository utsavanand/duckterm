import Darwin
import XCTest
@testable import Duckterm

final class RuntimeSetupControllerTests: XCTestCase {
    func testInstallerAccessRejectsRemoteOriginsFramesAndReplacedWebViews() {
        let url = URL(string: "http://127.0.0.1:4301")!
        func allowed(expected: URL? = nil, target: String = "local", enabled: Bool = true,
                     current: Bool = true, mainFrame: Bool = true, scheme: String = "http",
                     host: String = "127.0.0.1", port: Int = 4301) -> Bool {
            RuntimeSetupAccess.allows(expected: expected ?? url, localPort: 4301, target: target,
                enabled: enabled, currentWebView: current, mainFrame: mainFrame, scheme: scheme, host: host, port: port)
        }
        XCTAssertTrue(allowed())
        XCTAssertFalse(allowed(mainFrame: false))
        XCTAssertFalse(allowed(current: false))
        XCTAssertFalse(allowed(enabled: false))
        XCTAssertFalse(allowed(target: "ssh.example"))
        XCTAssertFalse(allowed(port: 4300), "Another edition cannot operate this app's helper")
        XCTAssertFalse(allowed(host: "evil.example"))
        XCTAssertFalse(allowed(host: "localhost"), "Even loopback aliases must match the current origin")
        XCTAssertFalse(allowed(scheme: "https"))
        XCTAssertFalse(allowed(expected: URL(string: "https://evil.example:4301")!, scheme: "https", host: "evil.example"))
    }
    private func preferences(new: Bool = true) -> RuntimeSetupPreferences {
        let name = "RuntimeSetupTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        return RuntimeSetupPreferences(home: URL(fileURLWithPath: "/private/tmp/" + name), defaults: defaults, newWorkspace: new)
    }

    func testFirstLaunchIsReadOnlyUntilPreparedAndResumesOnlyItsOwnProfile() throws {
        let prefs = preferences()
        XCTAssertEqual(prefs.state()["show_on_launch"] as? Bool, true)
        XCTAssertNil(prefs.defaults.object(forKey: prefs.key))
        XCTAssertFalse(FileManager.default.fileExists(atPath: String(prefs.key.dropFirst("agentSetup.v1.".count))))
        _ = prefs.state(prepare: true)
        let resume = RuntimeSetupPreferences(home: URL(fileURLWithPath: String(prefs.key.dropFirst("agentSetup.v1.".count))), defaults: prefs.defaults, newWorkspace: false)
        XCTAssertEqual(resume.state()["show_on_launch"] as? Bool, true)
        let code = RuntimeSetupPreferences(home: URL(fileURLWithPath: "/private/tmp/setup-other-profile"), defaults: prefs.defaults, newWorkspace: false)
        XCTAssertEqual(code.state()["show_on_launch"] as? Bool, false)
        let done = try resume.complete(agent: "codex", command: "codex")
        XCTAssertEqual(done["show_on_launch"] as? Bool, false)
        XCTAssertEqual(done["default_command"] as? String, "codex")
        XCTAssertEqual(prefs.state()["completed"] as? Bool, true)
    }

    func testExistingAndCorruptPreferencesDoNotReopenWizardOrLoseDefaultsOnSkip() throws {
        let prefs = preferences(new: false)
        XCTAssertEqual(prefs.state(prepare: true)["completed"] as? Bool, true)
        XCTAssertNil(prefs.defaults.object(forKey: prefs.key))
        prefs.defaults.set("broken", forKey: prefs.key)
        XCTAssertEqual(prefs.state()["show_on_launch"] as? Bool, false)
        _ = try prefs.complete(agent: "custom", command: "zsh")
        XCTAssertEqual(try prefs.complete()["default_command"] as? String, "zsh")
        XCTAssertThrowsError(try prefs.complete(agent: "custom", command: "echo first\nsecond"))
    }

    @MainActor func testRepeatedStartCannotSpawnTwiceAndOldIDsCannotControlReplacement() async throws {
        var transports: [SetupFakeTransport] = []
        let controller = RuntimeSetupController(preferences: preferences(), factory: { _, _, _ in
            let transport = SetupFakeTransport(); transports.append(transport); return transport
        }, picker: { _ in nil })
        let request = UUID().uuidString
        let params: [String: Any] = ["runtime": "codex", "action": "login", "request_id": request]
        let first = try await controller.perform(operation: "start", params: params)
        let repeated = try await controller.perform(operation: "start", params: params)
        XCTAssertEqual(first["job_id"] as? String, repeated["job_id"] as? String)
        XCTAssertEqual(transports.count, 1)
        await rejects { _ = try await controller.perform(operation: "complete", params: ["skipped": true]) }
        transports[0].finish(.exited(code: 0, cancelled: false))
        let second = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString])
        XCTAssertNotEqual(first["job_id"] as? String, second["job_id"] as? String)
        await rejects { _ = try await controller.perform(operation: "cancel", params: ["job_id": first["job_id"]!]) }
        transports[1].finish(.exited(code: 0, cancelled: false))
        await rejects { _ = try await controller.perform(operation: "start", params: params) }
        XCTAssertEqual(transports.count, 2)
    }

    @MainActor func testStatusWaitsForCleanupAndCancellationDoesNotWritePreferences() async throws {
        let transport = SetupFakeTransport(), prefs = preferences()
        let controller = RuntimeSetupController(preferences: prefs, factory: { _, _, _ in transport }, picker: { _ in nil })
        let request = UUID().uuidString
        var result: Result<[String: Any], Error>?
        let task = Task { @MainActor in
            do { result = .success(try await controller.perform(operation: "status", params: ["runtime": "codex", "request_id": request])) }
            catch { result = .failure(error) }
        }
        await settle { transport.started }
        transport.onEvent?(.status(Self.signedIn))
        XCTAssertNil(result)
        _ = try await controller.perform(operation: "cancel", params: ["request_id": request])
        XCTAssertTrue(transport.cancelled)
        await rejects { _ = try await controller.perform(operation: "complete", params: ["skipped": true]) }
        transport.finish(.error(.cancelled))
        await task.value
        guard case .failure = result else { return XCTFail("Cancelled status must not become ready") }
        XCTAssertNil(prefs.defaults.object(forKey: prefs.key))
        XCTAssertFalse(controller.isRunning)
    }

    @MainActor func testGenerationDropsOldOutputAndShutdownWaitsForOwnedCleanup() async throws {
        let transport = SetupFakeTransport()
        let controller = RuntimeSetupController(preferences: preferences(), factory: { _, _, _ in transport }, picker: { _ in nil })
        var events: [[String: Any]] = [], outputs: [[String: Any]] = []
        controller.onEvent = { events.append($0) }
        controller.onOutput = { outputs.append($0) }
        _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString])
        await settle { transport.started }
        var quit = false
        let shutdown = Task { @MainActor in await controller.shutdown(); quit = true }
        await settle { transport.cancelled }
        transport.onEvent?(.ready(runtime: .codex, action: .login))
        await transport.onOutput?(Data("private terminal text".utf8))
        XCTAssertTrue(events.isEmpty); XCTAssertTrue(outputs.isEmpty); XCTAssertFalse(quit)
        transport.finish(.error(.cancelled))
        await shutdown.value
        XCTAssertTrue(quit)
    }

    @MainActor func testNativePickerBindsRuntimeAndRejectsReplacedExecutable() async throws {
        let directory = (try runtimeSetupTestTemporaryDirectory()).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("codex' fixture")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        var selected: URL?
        let controller = RuntimeSetupController(preferences: preferences(), factory: { _, _, path in
            selected = path; return SetupFakeTransport()
        }, picker: { _ in executable })
        let choice = try await controller.perform(operation: "choose-executable", params: ["runtime": "codex"])
        let handle = try XCTUnwrap(choice["selection_handle"] as? String)
        await rejects { _ = try await controller.perform(operation: "start", params: ["runtime": "claude-code", "action": "login", "request_id": UUID().uuidString, "selection_handle": handle]) }
        let defaults = try await controller.perform(operation: "complete", params: ["skipped": false, "agent": "codex", "selection_handle": handle])
        XCTAssertEqual(defaults["default_command"] as? String, "'" + executable.path.replacingOccurrences(of: "'", with: "'\\''") + "'")
        let repeated = try await controller.perform(operation: "complete", params: ["skipped": false, "agent": "codex"])
        XCTAssertEqual(repeated["default_command"] as? String, defaults["default_command"] as? String)
        try Data("#!/bin/sh\nexit 11\n".utf8).write(to: executable)
        await rejects { _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString, "selection_handle": handle]) }
        XCTAssertNil(selected)
    }

    @MainActor func testPickerAndStaleSelectionRejectWritableExecutableOrParent() async throws {
        let directory = (try runtimeSetupTestTemporaryDirectory()).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("codex")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        var calls = 0
        let prefs = preferences()
        let controller = RuntimeSetupController(preferences: prefs, factory: { _, _, _ in
            calls += 1; return SetupFakeTransport()
        }, picker: { _ in executable })
        XCTAssertEqual(chmod(executable.path, 0o720), 0)
        await rejects { _ = try await controller.perform(operation: "choose-executable", params: ["runtime": "codex"]) }
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        let choice = try await controller.perform(operation: "choose-executable", params: ["runtime": "codex"])
        let handle = try XCTUnwrap(choice["selection_handle"] as? String)
        for path in [executable.path, directory.path] {
            XCTAssertEqual(chmod(path, 0o770), 0)
            await rejects { _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString, "selection_handle": handle]) }
            await rejects { _ = try await controller.perform(operation: "complete", params: ["skipped": false, "agent": "codex", "selection_handle": handle]) }
            XCTAssertEqual(chmod(path, 0o700), 0)
        }
        XCTAssertEqual(calls, 0)
        XCTAssertNil(prefs.defaults.object(forKey: prefs.key))
    }

    @MainActor func testUnsafeSavedChoiceCannotSilentlyRunDifferentPATHExecutable() async throws {
        let directory = try runtimeSetupTestTemporaryDirectory().appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("codex")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        let prefs = preferences()
        _ = try prefs.complete(agent: "codex", command: RuntimeSetupPreferences.quote(executable.path), executable: executable)
        XCTAssertEqual(chmod(executable.path, 0o720), 0)
        var calls = 0
        let controller = RuntimeSetupController(preferences: prefs, factory: { _, _, _ in
            calls += 1; return SetupFakeTransport()
        }, picker: { _ in nil })
        await rejects { _ = try await controller.perform(operation: "status", params: ["runtime": "codex", "request_id": UUID().uuidString]) }
        await rejects { _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString]) }
        XCTAssertEqual(calls, 0)
        XCTAssertNil(controller.state["default_command"])
    }

    @MainActor func testCompletedNativeSelectionSurvivesRelaunchAndInstallStillUsesFixedPath() async throws {
        let prefs = preferences(), directory = (try runtimeSetupTestTemporaryDirectory()).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("chosen-codex")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        _ = try prefs.complete(agent: "codex", command: RuntimeSetupPreferences.quote(executable.path), executable: executable)
        XCTAssertNil(prefs.state()["default_executable"], "Browser cannot supply or replace the persisted native executable")
        var choices: [URL?] = [], transports: [SetupFakeTransport] = []
        let controller = RuntimeSetupController(preferences: prefs, factory: { _, _, path in
            choices.append(path); let transport = SetupFakeTransport(); transports.append(transport); return transport
        }, picker: { _ in nil })
        _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString])
        XCTAssertEqual(choices[0], executable)
        transports[0].finish(.exited(code: 0, cancelled: false))
        _ = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "install", "request_id": UUID().uuidString])
        XCTAssertNil(choices[1])
        transports[1].finish(.exited(code: 0, cancelled: false))
        _ = try prefs.complete()
        XCTAssertEqual(prefs.selectedExecutable(agent: "codex"), executable)
        _ = try prefs.complete(agent: "claude", command: "claude")
        XCTAssertNil(prefs.selectedExecutable(agent: "codex"))
        XCTAssertNil(prefs.selectedExecutable(agent: "claude"))
    }

    @MainActor func testBridgeArgumentsCannotSelectArbitraryCommandsOrCoerceBooleans() async throws {
        var calls = 0
        let controller = RuntimeSetupController(preferences: preferences(), factory: { _, _, _ in calls += 1; return SetupFakeTransport() }, picker: { _ in nil })
        for params: [String: Any] in [
            ["runtime": "codex", "action": "login", "request_id": UUID().uuidString, "executable": "/bin/sh"],
            ["runtime": "custom", "action": "login", "request_id": UUID().uuidString],
            ["runtime": "codex", "action": "arbitrary", "request_id": UUID().uuidString],
            ["runtime": "codex", "action": "login", "request_id": "not-a-uuid"],
            ["runtime": "codex", "action": "install", "request_id": UUID().uuidString, "selection_handle": UUID().uuidString]
        ] { await rejects { _ = try await controller.perform(operation: "start", params: params) } }
        await rejects { _ = try await controller.perform(operation: "complete", params: ["skipped": 1]) }
        await rejects { _ = try await controller.perform(operation: "complete", params: ["skipped": false, "agent": "codex", "command": "other command"]) }
        XCTAssertEqual(calls, 0)
    }

    @MainActor func testTerminalControlsRejectInvalidGeometryAndBoundInput() async throws {
        let transport = SetupFakeTransport()
        let controller = RuntimeSetupController(preferences: preferences(), factory: { _, _, _ in transport }, picker: { _ in nil })
        let started = try await controller.perform(operation: "start", params: ["runtime": "codex", "action": "login", "request_id": UUID().uuidString])
        let id = try XCTUnwrap(started["job_id"] as? String)
        _ = try await controller.perform(operation: "resize", params: ["job_id": id, "cols": 80, "rows": 24])
        _ = try await controller.perform(operation: "input", params: ["job_id": id, "data": Data("hello".utf8).base64EncodedString()])
        await rejects { _ = try await controller.perform(operation: "resize", params: ["job_id": id, "cols": true, "rows": 24]) }
        await rejects { _ = try await controller.perform(operation: "resize", params: ["job_id": id, "cols": 80, "rows": 301]) }
        await rejects { _ = try await controller.perform(operation: "input", params: ["job_id": id, "data": Data(repeating: 1, count: 32769).base64EncodedString()]) }
        XCTAssertEqual(transport.commands, [.resize(columns: 80, rows: 24), .input(Data("hello".utf8))])
        transport.finish(.exited(code: 0, cancelled: false))
    }

    private static let signedIn = RuntimeSetupStatus(runtime: .codex, installed: true, version: "0.155.1", probe: .ok, authentication: .signedIn, credentialMethod: .account, environmentAPIKeyConfigured: false, reason: nil)
    @MainActor private func rejects(_ operation: () async throws -> Void, file: StaticString = #filePath, line: UInt = #line) async {
        do { try await operation(); XCTFail("Expected rejected request", file: file, line: line) } catch {}
    }
    @MainActor private func settle(_ condition: () -> Bool) async {
        for _ in 0..<200 { if condition() { return }; try? await Task.sleep(nanoseconds: 5_000_000) }
        XCTFail("Fixture did not reach expected lifecycle state")
    }
}

@MainActor private final class SetupFakeTransport: RuntimeSetupTransport {
    var onEvent: ((RuntimeSetupEvent) -> Void)?
    var onOutput: ((Data) async -> Void)?
    var onEnd: (() -> Void)?
    var started = false, cancelled = false
    var commands: [RuntimeSetupCommand] = []
    func start() async throws { started = true }
    func send(_ command: RuntimeSetupCommand) throws { commands.append(command) }
    func cancel() { cancelled = true }
    func finish(_ event: RuntimeSetupEvent) { onEvent?(event); onEnd?() }
}
