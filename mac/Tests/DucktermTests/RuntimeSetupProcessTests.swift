import Darwin
import XCTest
@testable import Duckterm

final class RuntimeSetupProcessTests: XCTestCase {
    private let ready = #"{"protocol":1,"type":"ready","runtime":"codex","action":"login"}"#
    private let exited = #"{"protocol":1,"type":"exited","exit_code":0,"cancelled":false}"#
    private let status = #"{"protocol":1,"type":"status","runtime":"codex","installed":true,"version":"1.2.3","probe":"ok","authentication":"signed_in","credential_method":"account","environment_api_key_configured":false}"#

    @MainActor func testStatusWaitsForEOFAndReapedHelperBeforeCallbacks() async throws {
        let fixture = try SetupProcessFixture(action: .status, body: """
        printf '%s' '\(status.prefix(40))'
        /bin/sleep 0.03
        printf '%s\\n' '\(status.dropFirst(40))'
        : > "$1/emitted"
        n=0
        while [ ! -f "$1/release" ] && [ "$n" -lt 200 ]; do
          /bin/sleep 0.01; n=$((n+1))
        done
        : > "$1/cleaned"
        """)
        defer { fixture.remove() }
        var order: [String] = []
        fixture.onTerminal = {
            XCTAssertTrue(fixture.exists("cleaned"))
            XCTAssertTrue(fixture.helperIsGone)
            order.append("terminal")
        }
        fixture.onEnd = { order.append("end") }
        try await fixture.start()
        await settle { fixture.exists("emitted") }
        XCTAssertTrue(fixture.events.isEmpty)
        XCTAssertFalse(fixture.ended)
        try fixture.mark("release")
        await settle { fixture.ended }
        XCTAssertEqual(order, ["terminal", "end"])
        XCTAssertEqual(fixture.events, [try RuntimeSetupEvent.decode(Data(status.utf8))])
        XCTAssertThrowsError(try fixture.transport.send(.resize(columns: 80, rows: 24)))
    }

    @MainActor func testInteractiveBytesAndInputKeepOrderAndReportProviderExitCode() async throws {
        let fixture = try SetupProcessFixture(body: """
        printf '%s\\n' '\(ready)'
        IFS= read -r first
        IFS= read -r second
        printf '%s\\n%s\\n' "$first" "$second" > "$1/commands"
        printf '%s\\n' '{"protocol":1,"type":"output","data":"aGVsbG8K"}'
        printf '%s\\n' '{"protocol":1,"type":"exited","exit_code":17,"cancelled":false}'
        : > "$1/cleaned"
        """)
        defer { fixture.remove() }
        try await fixture.start()
        await settle { fixture.events == [.ready(runtime: .codex, action: .login)] }
        try fixture.transport.send(.resize(columns: 100, rows: 30))
        try fixture.transport.send(.input(Data("answer\n".utf8)))
        await settle { fixture.ended }
        XCTAssertEqual(fixture.output, Data("hello\n".utf8))
        XCTAssertEqual(fixture.events, [.ready(runtime: .codex, action: .login), .exited(code: 17, cancelled: false)])
        let commands = try String(contentsOf: fixture.url("commands"), encoding: .utf8).split(separator: "\n")
        XCTAssertEqual(commands.count, 2)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: Data(commands[0].utf8)) as? NSDictionary,
                       ["type": "resize", "cols": 100, "rows": 30] as NSDictionary)
        XCTAssertEqual(try JSONSerialization.jsonObject(with: Data(commands[1].utf8)) as? NSDictionary,
                       ["type": "input", "data": Data("answer\n".utf8).base64EncodedString()] as NSDictionary)
        XCTAssertTrue(fixture.helperIsGone)
    }

    @MainActor func testOutputBackpressureDelaysCompletionUntilBytesAreConsumed() async throws {
        let fixture = try SetupProcessFixture(body: """
        printf '%s\\n' '\(ready)'
        printf '%s\\n' '{"protocol":1,"type":"output","data":"eA=="}'
        printf '%s\\n' '\(exited)'
        """)
        defer { fixture.remove() }
        let gate = SetupProcessGate()
        var consuming = false
        fixture.transport.onOutput = { _ in consuming = true; await gate.wait() }
        try await fixture.start()
        await settle { consuming && fixture.helperIsGone }
        XCTAssertFalse(fixture.ended)
        XCTAssertEqual(fixture.events, [.ready(runtime: .codex, action: .login)])
        gate.release()
        await settle { fixture.ended }
        XCTAssertEqual(fixture.events.last, .exited(code: 0, cancelled: false))
    }

    @MainActor func testTruncatedMissingAndTrailingFramesFailClosedWithoutRawOutput() async throws {
        let scripts = [
            "printf '%s' '{\"protocol\":1,\"type\":\"error\"'",
            "printf '%s\\n' '\(ready)'",
            "printf '%s\\n' '\(ready)' '\(exited)' '{\"token\":\"fixture-secret\"}'",
            "printf '%s\\n' '{\"protocol\":1,\"type\":\"error\",\"reason\":\"fixture-secret\"}'",
            "printf '%s\\n' '{\"protocol\":1,\"type\":\"ready\",\"runtime\":\"claude-code\",\"action\":\"login\"}'",
        ]
        for script in scripts {
            let fixture = try SetupProcessFixture(body: script)
            defer { fixture.remove() }
            try await fixture.start()
            await settle { fixture.ended }
            XCTAssertEqual(fixture.events.last, .error(.transportUnavailable))
            XCTAssertTrue(fixture.output.isEmpty)
            XCTAssertTrue(fixture.helperIsGone)
            XCTAssertEqual(fixture.endCount, 1)
        }
    }

    @MainActor func testOversizedPartialFrameCancelsAndReapsHelper() async throws {
        let fixture = try SetupProcessFixture(body: """
        printf '%s\\n' '\(ready)'
        /usr/bin/printf '%070000d' 0
        /bin/cat > "$1/commands"
        : > "$1/cleaned"
        """)
        defer { fixture.remove() }
        try await fixture.start()
        await settle { fixture.ended }
        XCTAssertEqual(fixture.events.last, .error(.transportUnavailable))
        XCTAssertTrue(fixture.output.isEmpty)
        XCTAssertTrue(fixture.exists("cleaned"))
        XCTAssertTrue(fixture.helperIsGone)
    }

    @MainActor func testNonzeroHelperExitCannotReportSuccessAndPreservesSafeError() async throws {
        for (frame, expected) in [
            (exited, RuntimeSetupEvent.error(.setupUnavailable)),
            (#"{"protocol":1,"type":"error","reason":"cleanup_failed"}"#, .error(.cleanupFailed)),
        ] {
            let fixture = try SetupProcessFixture(body: """
            printf '%s\\n' '\(ready)' '\(frame)'
            exit 7
            """)
            defer { fixture.remove() }
            try await fixture.start()
            await settle { fixture.ended }
            XCTAssertEqual(fixture.events.last, expected)
            XCTAssertTrue(fixture.helperIsGone)
        }
    }

    @MainActor func testCancelClosesStdinAndWaitsForCleanup() async throws {
        let fixture = try SetupProcessFixture(body: """
        printf '%s\\n' '\(ready)'
        /bin/cat > "$1/commands"
        : > "$1/eof"
        /bin/sleep 0.1
        : > "$1/cleaned"
        printf '%s\\n' '{"protocol":1,"type":"error","reason":"cancelled"}'
        """)
        defer { fixture.remove() }
        fixture.onTerminal = {
            XCTAssertTrue(fixture.exists("eof"))
            XCTAssertTrue(fixture.exists("cleaned"))
            XCTAssertTrue(fixture.helperIsGone)
        }
        try await fixture.start()
        await settle { !fixture.events.isEmpty }
        fixture.transport.cancel()
        fixture.transport.cancel()
        await settle { fixture.ended }
        XCTAssertEqual(fixture.events.last, .error(.cancelled))
        XCTAssertEqual(fixture.endCount, 1)
        XCTAssertThrowsError(try fixture.transport.send(.input(Data())))
    }

    @MainActor func testCancelFallsBackToTERMWithoutBypassingHelperCleanup() async throws {
        let fixture = try SetupProcessFixture(body: """
        trap ': > "$1/term"; : > "$1/cleaned"; exit 0' TERM
        printf '%s\\n' '\(ready)'
        n=0
        while [ "$n" -lt 500 ]; do /bin/sleep 0.01; n=$((n+1)); done
        """)
        defer { fixture.remove() }
        fixture.onTerminal = {
            XCTAssertTrue(fixture.exists("cleaned"))
            XCTAssertTrue(fixture.helperIsGone)
        }
        try await fixture.start()
        await settle { !fixture.events.isEmpty }
        fixture.transport.cancel()
        await settle { fixture.ended }
        XCTAssertTrue(fixture.exists("term"))
        XCTAssertEqual(fixture.events.last, .error(.cancelled))
    }

    @MainActor func testCancelDuringConfigurationDoesNotLaunchHelper() async throws {
        let root = try SetupProcessFixture.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let gate = SetupProcessGate()
        var preparing = false, events: [RuntimeSetupEvent] = [], ended = false
        let transport = RuntimeSetupProcess(runtime: .codex, action: .login) {
            preparing = true
            await gate.wait()
            return .init(executable: URL(fileURLWithPath: "/bin/sh"),
                         arguments: ["-c", ": > \"$1/spawned\"", "setup-fixture", root.path],
                         environment: ["HOME": root.path, "PATH": "/usr/bin:/bin"])
        }
        transport.onEvent = { events.append($0) }; transport.onEnd = { ended = true }
        let start = Task { try await transport.start() }
        await settle { preparing }
        transport.cancel()
        gate.release()
        try await start.value
        XCTAssertTrue(ended)
        XCTAssertEqual(events, [.error(.cancelled)])
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("spawned").path))
    }

    @MainActor func testConfigurationAndSpawnErrorsAreRedactedAndSingleStartOnly() async throws {
        for failConfiguration in [true, false] {
            var events: [RuntimeSetupEvent] = [], ends = 0
            let transport = RuntimeSetupProcess(runtime: .codex, action: .status) {
                if failConfiguration { throw NSError(domain: "fixture-secret", code: 1) }
                return .init(executable: URL(fileURLWithPath: "/private/tmp/nonexistent-runtime-\(UUID().uuidString)"),
                             arguments: [], environment: [:])
            }
            transport.onEvent = { events.append($0) }; transport.onEnd = { ends += 1 }
            try await transport.start()
            XCTAssertEqual(events, [.error(.setupUnavailable)])
            XCTAssertEqual(ends, 1)
            do { try await transport.start(); XCTFail("A transport must not be restarted") }
            catch { XCTAssertEqual(error as? RuntimeSetupProtocolError, .unexpectedEvent) }
            transport.cancel()
            XCTAssertEqual(ends, 1)
        }
    }

    @MainActor func testLargeInputCannotBlockMainActorWhenHelperStopsReading() async throws {
        let fixture = try SetupProcessFixture(body: """
        trap ': > "$1/cleaned"; exit 0' TERM
        printf '%s\\n' '\(ready)'
        n=0
        while [ "$n" -lt 250 ]; do /bin/sleep 0.01; n=$((n+1)); done
        : > "$1/cleaned"
        """)
        defer { fixture.remove() }
        try await fixture.start()
        await settle { !fixture.events.isEmpty }
        let before = Date()
        var refused = 0
        for _ in 0..<8 {
            do { try fixture.transport.send(.input(Data(repeating: 120, count: 32_768))) }
            catch { refused += 1 }
        }
        XCTAssertLessThan(Date().timeIntervalSince(before), 0.25, "Backpressure must not block the main actor")
        XCTAssertGreaterThan(refused, 0, "Queued input must be bounded")
        fixture.transport.cancel()
        await settle { fixture.ended }
        XCTAssertTrue(fixture.helperIsGone)
    }

    @MainActor func testLargestAllowedPasteArrivesIntactAfterTemporaryBackpressure() async throws {
        let fixture = try SetupProcessFixture(body: """
        printf '%s\\n' '\(ready)'
        /bin/sleep 0.1
        IFS= read -r command
        printf '%s' "$command" > "$1/command"
        printf '%s\\n' '\(exited)'
        """)
        defer { fixture.remove() }
        try await fixture.start()
        await settle { !fixture.events.isEmpty }
        let bytes = Data(repeating: 65, count: RuntimeSetupEvent.maximumTerminalBytes)
        try fixture.transport.send(.input(bytes))
        await settle { fixture.ended }
        let command = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: fixture.url("command"))) as? [String: String])
        XCTAssertEqual(command, ["type": "input", "data": bytes.base64EncodedString()])
        XCTAssertEqual(fixture.events.last, .exited(code: 0, cancelled: false))
        XCTAssertTrue(fixture.helperIsGone)
    }

    @MainActor func testPermanentlyStalledInputCancelsWithoutUnboundedQueueOrWait() async throws {
        let fixture = try SetupProcessFixture(body: """
        trap ': > "$1/cleaned"; exit 0' TERM
        printf '%s\\n' '\(ready)'
        n=0
        while [ "$n" -lt 500 ]; do /bin/sleep 0.01; n=$((n+1)); done
        """)
        defer { fixture.remove() }
        try await fixture.start()
        await settle { !fixture.events.isEmpty }
        let before = ContinuousClock.now
        for _ in 0..<8 { try? fixture.transport.send(.input(Data(repeating: 120, count: 32_768))) }
        await settle { fixture.ended }
        XCTAssertLessThan(before.duration(to: .now), .seconds(4))
        XCTAssertEqual(fixture.events.last, .error(.transportUnavailable))
        XCTAssertTrue(fixture.exists("cleaned"))
        XCTAssertTrue(fixture.helperIsGone)
    }

    @MainActor func testPickedExecutableIsRevalidatedAcrossAsynchronousPreparation() async throws {
        for replacement in ["replace", "symlink", "rewrite", "permission"] {
            let root = try SetupProcessFixture.makeRoot()
            defer { try? FileManager.default.removeItem(at: root) }
            let chosen = root.appendingPathComponent("chosen executable")
            try Data("#!/bin/sh\nexit 0\n".utf8).write(to: chosen)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: chosen.path)
            let gate = SetupProcessGate()
            var preparing = false
            let start = Task {
                try await RuntimeSetupProcess.preparedCommand(executable: chosen) {
                    preparing = true
                    await gate.wait()
                    return .init(executable: chosen, arguments: [], environment: [:])
                }
            }
            await settle { preparing }
            switch replacement {
            case "replace", "symlink":
                let saved = root.appendingPathComponent("original")
                try FileManager.default.moveItem(at: chosen, to: saved)
                if replacement == "symlink" {
                    try FileManager.default.createSymbolicLink(at: chosen, withDestinationURL: saved)
                } else {
                    try FileManager.default.copyItem(at: saved, to: chosen)
                }
            case "rewrite": try Data("#!/bin/sh\nexit 1\n".utf8).write(to: chosen)
            default: try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: chosen.path)
            }
            gate.release()
            do { _ = try await start.value; XCTFail("Changed executable must be rejected: \(replacement)") }
            catch { XCTAssertEqual(error as? RuntimeSetupProtocolError, .invalidFrame) }
        }
    }

    @MainActor func testUnchangedPickedExecutablePassesAndInvalidChoiceNeverPrepares() async throws {
        let root = try SetupProcessFixture.makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let chosen = root.appendingPathComponent("chosen executable")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: chosen)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: chosen.path)
        let command = try await RuntimeSetupProcess.preparedCommand(executable: chosen) {
            await Task.yield()
            return .init(executable: chosen, arguments: ["fixed-action"], environment: ["HOME": root.path])
        }
        XCTAssertEqual(command.executable, chosen)
        XCTAssertEqual(command.arguments, ["fixed-action"])
        var prepared = false
        do {
            _ = try await RuntimeSetupProcess.preparedCommand(executable: root) {
                prepared = true
                return command
            }
            XCTFail("A directory cannot be selected as a CLI")
        } catch { XCTAssertEqual(error as? RuntimeSetupProtocolError, .invalidFrame) }
        XCTAssertFalse(prepared)
    }

    @MainActor private func settle(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<1400 {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Timed out awaiting private setup fixture", file: file, line: line)
    }
}

@MainActor private final class SetupProcessGate {
    private var continuation: CheckedContinuation<Void, Never>?
    func wait() async { await withCheckedContinuation { continuation = $0 } }
    func release() { continuation?.resume(); continuation = nil }
}

@MainActor private final class SetupProcessFixture {
    let root: URL
    let transport: RuntimeSetupProcess
    var events: [RuntimeSetupEvent] = []
    var output = Data()
    var endCount = 0
    var onTerminal: (() -> Void)?
    var onEnd: (() -> Void)?
    var ended: Bool { endCount != 0 }

    init(action: RuntimeSetupAction = .login, body: String) throws {
        root = try Self.makeRoot()
        let home = root
        let script = "umask 077\nprintf '%s' \"$$\" > \"$1/child.pid\"\n" + body
        transport = RuntimeSetupProcess(runtime: .codex, action: action) {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "setup-fixture", home.path],
                  environment: ["HOME": home.path, "TMPDIR": home.path, "PATH": "/usr/bin:/bin"])
        }
        transport.onEvent = { [weak self] event in
            guard let self else { return }
            events.append(event)
            switch event { case .status, .exited, .error: onTerminal?(); default: break }
        }
        transport.onOutput = { [weak self] bytes in self?.output.append(bytes) }
        transport.onEnd = { [weak self] in self?.endCount += 1; self?.onEnd?() }
    }

    static func makeRoot() throws -> URL {
        let root = URL(fileURLWithPath: "/private/tmp").appendingPathComponent("runtime-setup-native-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        return root
    }
    func start() async throws { try await transport.start() }
    func url(_ name: String) -> URL { root.appendingPathComponent(name) }
    func exists(_ name: String) -> Bool { FileManager.default.fileExists(atPath: url(name).path) }
    func mark(_ name: String) throws { try Data().write(to: url(name)) }
    var helperIsGone: Bool {
        guard let text = try? String(contentsOf: url("child.pid"), encoding: .utf8), let pid = Int32(text) else { return false }
        return kill(pid, 0) == -1 && errno == ESRCH
    }
    func remove() {
        transport.cancel()
        transport.onEvent = nil; transport.onOutput = nil; transport.onEnd = nil
        try? FileManager.default.removeItem(at: root)
    }
}
