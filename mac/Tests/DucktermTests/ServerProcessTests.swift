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
}
