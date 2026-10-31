import XCTest
@testable import Duckterm

final class ServerProcessTests: XCTestCase {
    /// An app opened from an enrolled agent shell, with another profile's facets.
    private let inherited = [
        "PATH": "/usr/bin", "HOME": "/Users/me", "TMUX_TMPDIR": "/private/qa",
        "DUCKTERM_HOME": "/Users/me/.duckterm-code", "DUCKTERM_TMUX_SOCKET": "duckterm-code",
        "DUCKTERM_PORT": "4800", "DUCKTERM_URL": "http://127.0.0.1:4800",
        "DUCKTERM_INSTANCE": "code", "DUCKTERM_DESKTOP_PROFILE": "code", "DUCKTERM_APP_PROFILE": "code",
        "DUCKTERM_EDITION": "code", "DUCKTERM_HOSTED": "1", "DUCKTERM_HOST": "remote",
        "DUCKTERM_SESSION_KEY": "fixture-session", "DUCKTERM_SESSION_TOKEN_FILE": "/tmp/fixture/credential.json",
        "DUCKTERM_INTERNAL": "1",
    ]
    private let lite = AppProfile(id: "lite", edition: "lite", instance: "lite",
                                  home: URL(fileURLWithPath: "/Users/me/.duckterm-lite"), tmuxSocket: "duckterm-lite", port: 4519)

    func testAppSuppliesItsCompleteProfileAndDropsShellEnrollment() {
        let env = ServerProcess.profileEnvironment(inheriting: inherited, profile: lite)
        XCTAssertEqual(env["DUCKTERM_DESKTOP_PROFILE"], "lite")
        XCTAssertEqual(env["DUCKTERM_EDITION"], "lite")
        XCTAssertEqual(env["DUCKTERM_INSTANCE"], "lite")
        XCTAssertEqual(env["DUCKTERM_HOME"], "/Users/me/.duckterm-lite")
        XCTAssertEqual(env["DUCKTERM_TMUX_SOCKET"], "duckterm-lite")
        XCTAssertEqual(env["DUCKTERM_PORT"], "4519")
        XCTAssertEqual(env["DUCKTERM_URL"], "http://127.0.0.1:4519")
        XCTAssertEqual(env["DUCKTERM_NO_BROWSER"], "1")
        for key in ["DUCKTERM_APP_PROFILE", "DUCKTERM_HOSTED", "DUCKTERM_HOST", "DUCKTERM_SESSION_KEY",
                    "DUCKTERM_SESSION_TOKEN_FILE", "DUCKTERM_INTERNAL"] {
            XCTAssertNil(env[key], key)
        }
        XCTAssertEqual(env["PATH"], "/usr/bin")
        XCTAssertEqual(env["TMUX_TMPDIR"], "/private/qa")
    }

    func testLegacyFullClearsAnInheritedInstance() {
        let full = AppProfile(id: "full", edition: "full", instance: nil,
                              home: URL(fileURLWithPath: "/Users/me/.duckterm"), tmuxSocket: "duckterm", port: 4300)
        let env = ServerProcess.profileEnvironment(inheriting: inherited, profile: full)
        XCTAssertEqual(env["DUCKTERM_INSTANCE"], "")
        XCTAssertEqual(env["DUCKTERM_HOME"], "/Users/me/.duckterm")
        XCTAssertEqual(env["DUCKTERM_PORT"], "4300")
    }

    func testUnlabelledBundleOnlyClearsInheritedContext() {
        let env = ServerProcess.profileEnvironment(inheriting: inherited, profile: nil)
        XCTAssertTrue(env.keys.filter { $0.hasPrefix("DUCKTERM_") } == ["DUCKTERM_NO_BROWSER"])
    }

    func testOnlyTestBuildsHonorAPrivateHome() {
        let env = ["HOME": "/private/qa/home"]
        XCTAssertEqual(AppIdentity.userHome(isTest: true, environment: env).path, "/private/qa/home")
        XCTAssertEqual(AppIdentity.userHome(isTest: false, environment: env).path, NSHomeDirectory())
        XCTAssertEqual(AppIdentity.userHome(isTest: true, environment: ["HOME": "relative"]).path, NSHomeDirectory())
    }

    func testRefusalExplainsItselfInsteadOfTimingOut() {
        let refused = "Traceback...\nduckterm: Existing workspace /Users/me/.duckterm-lite is not owned by this desktop profile.\n"
        XCTAssertEqual(ServerProcess.startupReason(stderr: refused, port: 4519),
                       "Existing workspace /Users/me/.duckterm-lite is not owned by this desktop profile.")
        XCTAssertEqual(ServerProcess.startupReason(stderr: "OSError: [Errno 48] Address already in use", port: 4519),
                       "Port 4519 is in use by another program. Quit it, then reopen this app.")
        XCTAssertNil(ServerProcess.startupReason(stderr: "serving on http://127.0.0.1:4519\n", port: 4519))
    }

    func testActualCLIAndHomeLockRefusalsExplainTheFailure() {
        for prefix in ["", "duckterm: "] {
            let cli = "\(prefix)Port 4300 is already in use.\n\nAnother Duckterm is already running:\n  open http://127.0.0.1:4300\n"
            XCTAssertEqual(ServerProcess.startupReason(stderr: cli, port: 4300), "Port 4300 is already in use.")
            let lock = "\(prefix)another DuckTerm server already owns /private/test-home"
            XCTAssertEqual(ServerProcess.startupReason(stderr: lock, port: 4300),
                           "another DuckTerm server already owns /private/test-home")
        }
    }

    func testBusyThenReadyServerIsReusedAfterCollision() async {
        var probes = 0
        var pauses = 0
        var editionChecks = 0
        let reused = await ServerProcess.reuseExistingServer(
            stderr: "duckterm: Port 4300 is already in use.", port: 4300,
            isUp: { probes += 1; return probes == 3 },
            matchesEdition: { editionChecks += 1; return true },
            pause: { pauses += 1 }
        )
        XCTAssertTrue(reused)
        XCTAssertEqual(probes, 3)
        XCTAssertEqual(pauses, 2)
        XCTAssertEqual(editionChecks, 1)
    }

    func testHomeLockAndBindRaceAlsoRetry() async {
        for refusal in ["duckterm: another DuckTerm server already owns /private/test-home",
                        "duckterm: another DuckTerm server (pid 123) already owns /private/test-home",
                        "OSError: [Errno 48] Address already in use"] {
            var probes = 0
            let reused = await ServerProcess.reuseExistingServer(
                stderr: refusal, port: 4300, isUp: { probes += 1; return probes == 2 },
                matchesEdition: { true }, pause: {}
            )
            XCTAssertTrue(reused, refusal)
            XCTAssertEqual(probes, 2)
        }
    }

    func testWrongEditionIsNeverReused() async {
        var probes = 0
        var pauses = 0
        let reused = await ServerProcess.reuseExistingServer(
            stderr: "Port 4300 is already in use.", port: 4300,
            isUp: { probes += 1; return true }, matchesEdition: { false }, pause: { pauses += 1 }
        )
        XCTAssertFalse(reused)
        XCTAssertEqual(probes, 1)
        XCTAssertEqual(pauses, 0)
    }

    func testUnresponsiveOrForeignServerExhaustsBoundedRetries() async {
        var probes = 0
        var pauses = 0
        var editionChecks = 0
        let reused = await ServerProcess.reuseExistingServer(
            stderr: "Port 4300 is already in use.", port: 4300,
            isUp: { probes += 1; return false },
            matchesEdition: { editionChecks += 1; return true }, pause: { pauses += 1 }
        )
        XCTAssertFalse(reused)
        XCTAssertEqual(probes, 5)
        XCTAssertEqual(pauses, 4)
        XCTAssertEqual(editionChecks, 0)
    }

    func testUnrelatedStartupFailureDoesNotConnectToAnotherServer() async {
        var probes = 0
        let reused = await ServerProcess.reuseExistingServer(
            stderr: "duckterm: Existing workspace is not owned by this desktop profile.", port: 4300,
            isUp: { probes += 1; return true }, matchesEdition: { true }, pause: {}
        )
        XCTAssertFalse(reused)
        XCTAssertEqual(probes, 0)
    }

    func testCancellationDuringRetryStopsRecovery() async {
        var probes = 0
        let reused = await ServerProcess.reuseExistingServer(
            stderr: "Port 4300 is already in use.", port: 4300,
            isUp: { probes += 1; return false }, matchesEdition: { true },
            pause: { throw CancellationError() }
        )
        XCTAssertFalse(reused)
        XCTAssertEqual(probes, 1)
    }

    func testStartupLogKeepsOnlyTheTail() {
        let log = StartupLog()
        log.append(Data(String(repeating: "x", count: 10_000).utf8))
        log.append(Data("\nduckterm: last reason\n".utf8))
        XCTAssertEqual(log.text.count, 4096)
        XCTAssertTrue(log.text.hasSuffix("duckterm: last reason\n"))
    }

    func testRefusalReasonIsSanitizedAndBounded() {
        let hostile = "duckterm: bad\u{1b}[31m workspace\tname " + String(repeating: "y", count: 1000)
        let reason = ServerProcess.startupReason(stderr: hostile, port: 4519)!
        XCTAssertFalse(reason.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) })
        XCTAssertTrue(reason.hasPrefix("bad [31m workspace name y"))
        XCTAssertEqual(reason.count, 300)
        XCTAssertNil(ServerProcess.startupReason(stderr: "duckterm: \u{7}\n", port: 4519))
    }
}
