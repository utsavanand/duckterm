import XCTest
@testable import Duckterm

final class ServerProcessTests: XCTestCase {
    private let inherited = [
        "PATH": "/usr/bin", "HOME": "/Users/me",
        "DUCKTERM_HOME": "/Users/me/.duckterm", "DUCKTERM_TMUX_SOCKET": "duckterm",
        "DUCKTERM_PORT": "4300", "DUCKTERM_URL": "http://127.0.0.1:4300",
        "DUCKTERM_INSTANCE": "dev", "DUCKTERM_DESKTOP_PROFILE": "full",
        "DUCKTERM_EDITION": "full", "DUCKTERM_HOSTED": "1",
    ]

    func testAppSuppliesOnlyItsOwnProfile() {
        let env = ServerProcess.profileEnvironment(inheriting: inherited, profile: "lite", edition: "lite", port: 4519)
        XCTAssertEqual(env["DUCKTERM_DESKTOP_PROFILE"], "lite")
        XCTAssertEqual(env["DUCKTERM_EDITION"], "lite")
        XCTAssertEqual(env["DUCKTERM_NO_BROWSER"], "1")
        for key in ["DUCKTERM_HOME", "DUCKTERM_TMUX_SOCKET", "DUCKTERM_PORT", "DUCKTERM_URL", "DUCKTERM_INSTANCE", "DUCKTERM_HOSTED"] {
            XCTAssertNil(env[key], key)
        }
        XCTAssertEqual(env["PATH"], "/usr/bin")
        XCTAssertEqual(env["HOME"], "/Users/me")
    }

    func testUnlabelledBundleClearsInheritedFacets() {
        let env = ServerProcess.profileEnvironment(inheriting: inherited, profile: nil, edition: nil, port: 4300)
        for key in ["DUCKTERM_HOME", "DUCKTERM_PORT", "DUCKTERM_INSTANCE", "DUCKTERM_DESKTOP_PROFILE", "DUCKTERM_EDITION"] {
            XCTAssertNil(env[key], key)
        }
    }

    func testHarnessInstanceReplacesTheProfile() {
        let env = ServerProcess.profileEnvironment(
            inheriting: inherited, profile: "test-lite", edition: "lite", testInstance: "bundle-1", port: 4444)
        XCTAssertEqual(env["DUCKTERM_INSTANCE"], "bundle-1")
        XCTAssertEqual(env["DUCKTERM_PORT"], "4444")
        XCTAssertNil(env["DUCKTERM_DESKTOP_PROFILE"])
        XCTAssertNil(env["DUCKTERM_HOME"])
    }
}
