import AppKit
import Darwin
import XCTest
@testable import Duckterm

final class DuckyPairingTests: XCTestCase {
    private let deviceID = String(repeating: "a", count: 32)
    private let now = Date(timeIntervalSince1970: 1000)
    private var invite: DuckyPairingInvitation { .init(link: "ducky://pair?invite=PREVIEW_ONLY", expiresAt: 1100) }
    private var device: DuckyPairingDevice { .init(id: deviceID, name: "Test iPhone", expiresAt: 1090) }

    func testFramingAcceptsPartialReadsAndRejectsUnboundedOrMalformedInput() throws {
        let json = #"{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}"#
        var frames = DuckyPairingFrames()
        XCTAssertEqual(try frames.append(Data(json.prefix(30).utf8)), [])
        XCTAssertFalse(frames.complete)
        XCTAssertEqual(try frames.append(Data((json.dropFirst(30) + "\n").utf8)), [.invitation(invite)])
        XCTAssertTrue(frames.complete)
        for bad in [json.replacingOccurrences(of: "\"version\":1", with: "\"version\":2"),
                    json.replacingOccurrences(of: "ducky://pair", with: "https://pair"),
                    json.replacingOccurrences(of: "PREVIEW_ONLY", with: "PREVIEW_ONLY&extra=1"),
                    #"{"version":1,"state":"error","code":"secret raw error"}"#,
                    #"{"version":1,"state":"expired","phase":"unknown"}"#] {
            XCTAssertThrowsError(try DuckyPairingEvent.decode(Data(bad.utf8)))
        }
        var oversized = DuckyPairingFrames()
        XCTAssertThrowsError(try oversized.append(Data(repeating: 97, count: 8193)))
        var excessive = DuckyPairingFrames()
        XCTAssertThrowsError(try excessive.append(Data(repeating: 10, count: 65537)))
    }

    func testDeviceNamesCannotInjectControlsOrBidiAndIDsAreExact() throws {
        for name in ["", "Phone\nApprove", "Phone\u{202e}Approve", String(repeating: "a", count: 81)] {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "state": "pending",
                "device": ["id": deviceID, "name": name, "expires_at": 1090]])
            XCTAssertThrowsError(try DuckyPairingEvent.decode(data))
        }
        XCTAssertFalse(DuckyPairingDevice(id: deviceID + "\n", name: "Phone", expiresAt: 1090).valid)
        XCTAssertTrue(DuckyPairingDevice(id: deviceID, name: "José’s iPhone 📱", expiresAt: 1090).valid)
    }

    @MainActor func testApprovalRequiresAuthenticationAndMatchingBackendConfirmation() async {
        let transport = FakePairingTransport()
        var authenticate = false, authCalls = 0
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in authCalls += 1; return authenticate }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        XCTAssertEqual(transport.commands, [])
        model.approve(); await settle { !model.busy }
        XCTAssertEqual(authCalls, 1); XCTAssertEqual(transport.commands, [])
        authenticate = true
        model.approve(); model.approve()
        await settle { transport.commands.count == 1 }
        XCTAssertEqual(authCalls, 2)
        XCTAssertEqual(transport.commands, ["approve:\(deviceID)"])
        transport.onEvent?(.approved(.init(id: deviceID, name: device.name, expiresAt: nil)))
        XCTAssertEqual(model.state, .approved(.init(id: deviceID, name: device.name, expiresAt: nil)))
        XCTAssertTrue(transport.cancelled, "Terminal success must retire the owned process")
        model.close()
    }

    @MainActor func testUnsolicitedApprovalIsRejectedAndCeremonyCancelled() async {
        let transport = FakePairingTransport()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        transport.onEvent?(.approved(device))
        XCTAssertEqual(model.state, .error("approval_unavailable")); XCTAssertTrue(transport.cancelled)
        XCTAssertEqual(transport.commands, [])
    }

    @MainActor func testExpiryDuringAuthenticationCannotApprove() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        var clock = now
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { clock })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { auth.continuation != nil }
        clock = Date(timeIntervalSince1970: 1090); model.tick(); auth.finish(true)
        await Task.yield()
        XCTAssertEqual(model.state, .expired(pending: true)); XCTAssertEqual(transport.commands, [])
        XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testDenyWhileAuthenticationPendingWinsAndCloseInvalidatesOldCallbacks() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { auth.continuation != nil }
        model.deny(); auth.finish(true); await Task.yield()
        XCTAssertEqual(transport.commands, ["deny:\(deviceID)"])
        let oldCallback = transport.onEvent
        model.close(); oldCallback?(.approved(device))
        XCTAssertEqual(model.state, .closed); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testSentDecisionAwaitsAuthoritativeResultAcrossExpiryButHasDeadline() async {
        let transport = FakePairingTransport()
        var clock = Date(timeIntervalSince1970: 1089)
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { clock })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { transport.commands.count == 1 }
        clock = Date(timeIntervalSince1970: 1091); model.tick()
        XCTAssertEqual(model.state, .pending(device))
        clock = Date(timeIntervalSince1970: 1095); model.tick()
        XCTAssertEqual(model.state, .error("approval_unavailable")); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testMissingListenerExpiresAndCancelsStartup() async {
        let transport = FakePairingTransport()
        var clock = now
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { clock })
        model.start(); clock = Date(timeIntervalSince1970: 1046); model.tick()
        XCTAssertEqual(model.state, .error("listener_unavailable")); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testPendingViewUsesDenyAsDefaultAndCountdownPreservesControls() async {
        let view = DuckyPairingView(state: .pending(device), icon: nil, now: now)
        let controls = view.subviews.compactMap { $0 as? NSButton }
        let deny = controls.first { $0.identifier?.rawValue == "deny" }
        let approve = controls.first { $0.identifier?.rawValue == "approve" }
        XCTAssertEqual(deny?.keyEquivalent, "\r"); XCTAssertEqual(approve?.keyEquivalent, "")
        XCTAssertTrue(view.preferredFocus === deny)
        view.updateCountdown(now: Date(timeIntervalSince1970: 1001))
        XCTAssertTrue(view.subviews.contains { $0 === deny }); XCTAssertTrue(view.subviews.contains { $0 === approve })
        XCTAssertEqual(view.frame.width, 406)
        XCTAssertNil(view.window, "View inspection must never create a window")
        let busy = DuckyPairingView(state: .pending(device), busy: true, icon: nil, now: now)
        XCTAssertFalse((busy.subviews.first { $0.identifier?.rawValue == "approve" } as? NSButton)?.isEnabled ?? true)
    }

    @MainActor func testEnvironmentDoesNotInheritSessionCredentialsOrForeignProfiles() async {
        let root = URL(fileURLWithPath: "/private/tmp/pairing-env-fixture")
        let profile = AppProfile(id: "test-lite", edition: "lite", instance: "test-lite", home: root,
                                 tmuxSocket: "test-lite", port: 12345)
        let inherited = ["HOME": "/owner", "PATH": "/evil", "PYTHONPATH": "/evil", "DYLD_INSERT_LIBRARIES": "/evil",
            "DUCKTERM_OWNER_TOKEN": "secret", "DUCKTERM_SESSION_TOKEN": "secret", "DUCKTERM_HOME": "/wrong",
            "OPENAI_API_KEY": "secret", "CODEX_HOME": "/wrong", "LANG": "en_US.UTF-8"]
        let env = DuckyPairingProcess.environment(inheriting: inherited, home: root, profile: profile)
        XCTAssertEqual(env["HOME"], root.path); XCTAssertEqual(env["DUCKTERM_HOME"], root.path)
        XCTAssertEqual(env["DUCKTERM_NO_KEYCHAIN"], "1"); XCTAssertEqual(env["DUCKTERM_SUMMARIZER"], "off")
        for key in ["PYTHONPATH", "DYLD_INSERT_LIBRARIES", "DUCKTERM_OWNER_TOKEN", "DUCKTERM_SESSION_TOKEN", "OPENAI_API_KEY", "CODEX_HOME"] {
            XCTAssertNil(env[key], key)
        }
        XCTAssertFalse(env["PATH"]?.contains("/evil") ?? true)
    }

    @MainActor func testOwnedSubprocessReceivesCancelAndCleansItsFixture() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-native-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("cleanup")
        let pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$2"
        printf '%s\n' '{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}'
        IFS= read -r command
        printf '%s' "$command" > "$1"
        printf '%s\n' '{"version":1,"state":"cancelled"}'
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", marker.path, pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        var events: [DuckyPairingEvent] = []
        transport.onEvent = { events.append($0) }
        try await transport.start()
        await settle { events == [.invitation(self.invite)] }
        transport.cancel()
        await settle { FileManager.default.fileExists(atPath: marker.path) }
        let data = try Data(contentsOf: marker)
        let command = try JSONSerialization.jsonObject(with: data) as? [String: String]
        XCTAssertEqual(command, ["action": "cancel"])
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle { kill(pid, 0) == -1 && errno == ESRCH }
    }

    @MainActor func testTerminalFrameReapsAChildThatNeverExits() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-reaper-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$1"
        printf '%s\n' '{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}'
        IFS= read -r command
        printf '%s\n' '{"version":1,"state":"cancelled"}'
        trap '' TERM
        exec /bin/sleep 30
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        var events: [DuckyPairingEvent] = []
        transport.onEvent = { events.append($0) }
        try await transport.start()
        await settle { events == [.invitation(self.invite)] }
        try transport.send(action: "cancel", deviceID: nil)
        await settle { events.last == .cancelled }
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle({ kill(pid, 0) == -1 && errno == ESRCH }, attempts: 1800)
    }

    @MainActor func testStartupCancellationAllowsPreparationToFinishThenObserveEOF() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-startup-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("cleanup"), pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$2"
        /bin/sleep 3.2
        /bin/cat > /dev/null
        printf 'cleaned' > "$1"
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", marker.path, pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        try await transport.start(); transport.cancel()
        await settle({ FileManager.default.fileExists(atPath: marker.path) }, attempts: 1200)
        XCTAssertEqual(try String(contentsOf: marker, encoding: .utf8), "cleaned")
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle { kill(pid, 0) == -1 && errno == ESRCH }
    }

    @MainActor private func settle(_ condition: () -> Bool, attempts: Int = 200, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<attempts {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Timed out awaiting fixture state", file: file, line: line)
    }
}

@MainActor private final class FakePairingTransport: DuckyPairingTransport {
    var onEvent: ((DuckyPairingEvent) -> Void)?
    var commands: [String] = []
    var cancelled = false
    func start() async throws {}
    func send(action: String, deviceID: String?) throws { commands.append("\(action):\(deviceID ?? "")") }
    func cancel() { cancelled = true }
}

@MainActor private final class SuspendedPairingAuthentication {
    var continuation: CheckedContinuation<Bool, Never>?
    func wait() async -> Bool { await withCheckedContinuation { continuation = $0 } }
    func finish(_ result: Bool) { continuation?.resume(returning: result); continuation = nil }
}
