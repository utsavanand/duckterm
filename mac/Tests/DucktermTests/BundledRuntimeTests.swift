import XCTest
@testable import Duckterm

final class BundledRuntimeTests: XCTestCase {
    func testPrivateCLILeavesProjectPythonEnvironmentAlone() {
        let runtime = BundledRuntime(resources: URL(fileURLWithPath: "/Applications/DuckTerm Lite.app/Contents/Resources"))
        let original = [
            "PATH": "/project/.venv/bin:/custom/bin:/usr/bin",
            "PYTHONHOME": "/project/python",
            "PYTHONPATH": "/project/modules",
            "VIRTUAL_ENV": "/project/.venv",
        ]
        let environment = runtime.environment(inheriting: original)
        XCTAssertEqual(environment["PATH"], runtime.resources.path + "/bin:" + original["PATH"]!)
        XCTAssertEqual(environment["PYTHONHOME"], original["PYTHONHOME"])
        XCTAssertEqual(environment["PYTHONPATH"], original["PYTHONPATH"])
        XCTAssertEqual(environment["VIRTUAL_ENV"], original["VIRTUAL_ENV"])
        XCTAssertEqual(environment["DUCKTERM_BUNDLED_TMUX"], runtime.tmux.path)
        XCTAssertEqual(environment["DUCKTERM_BUNDLED_CLI"], runtime.cli.path)
        XCTAssertFalse(environment["PATH"]!.contains("python/bin"))
    }

    func testIncompleteBundleFailsWithoutUsingInstalledCLI() {
        let runtime = BundledRuntime(resources: URL(fileURLWithPath: "/missing-bundle-\(UUID().uuidString)"))
        XCTAssertThrowsError(try runtime.validate())
    }
}
