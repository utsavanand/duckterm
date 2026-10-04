import XCTest
@testable import Duckterm

final class TmuxAvailabilityTests: XCTestCase {
    func testMissingThenRecheckRecoversWithoutCaching() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let python = dir.appendingPathComponent("python stub")
        func write(_ exit: Int) throws {
            try "#!/bin/sh\necho duckterm-tmux:\(exit == 0 ? "available" : "missing")\nexit \(exit)\n".write(to: python, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: python.path)
        }
        try write(1)
        let missing = await TmuxAvailability.probe(python: python.path, environment: ["PATH": "/usr/bin:/bin"])
        XCTAssertEqual(missing, .missing)
        try write(0)
        let found = await TmuxAvailability.probe(python: python.path, environment: ["PATH": "/usr/bin:/bin"])
        XCTAssertEqual(found, .available)
        try write(2)
        let socketError = await TmuxAvailability.probe(python: python.path, environment: ["PATH": "/usr/bin:/bin"])
        XCTAssertEqual(socketError, .checkFailed)
        try "#!/bin/sh\nexit 1\n".write(to: python, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: python.path)
        let brokenImport = await TmuxAvailability.probe(python: python.path, environment: [:])
        XCTAssertEqual(brokenImport, .checkFailed)
    }

    func testLaunchFailureAndTimeoutAreNotInstallationAdvice() async throws {
        let missingPython = await TmuxAvailability.probe(python: "/nonexistent/test/python", environment: [:])
        XCTAssertEqual(missingPython, .checkFailed)
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let python = dir.appendingPathComponent("slow probe")
        try "#!/bin/sh\nexec /bin/sleep 5\n".write(to: python, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: python.path)
        let timedOut = await TmuxAvailability.probe(python: python.path, environment: [:], timeout: 0.03)
        XCTAssertEqual(timedOut, .checkFailed)
    }

    @MainActor func testGuidanceDoesNotMistakeSocketFailureForMissingDependency() {
        let setup = LocalSessionSetup()
        let missing = setup.makeAlert(state: .missing)
        XCTAssertEqual(missing.messageText, "Set up local sessions")
        XCTAssertEqual(missing.buttons.map(\.title), ["Recheck", "Later"])
        XCTAssertGreaterThan(missing.accessoryView!.frame.width, 300)
        XCTAssertGreaterThan(missing.accessoryView!.frame.height, 70)
        let failure = setup.makeAlert(state: .checkFailed, checked: true)
        XCTAssertFalse(failure.informativeText.contains("Run this command"))
        XCTAssertTrue(failure.informativeText.contains("does not mean tmux is missing"))
    }
    @MainActor func testLaterDoesNotProbeAndRecheckCanRecover() async {
        let setup = LocalSessionSetup()
        var probes = 0
        await setup.present(initial: .missing, recheck: { probes += 1; return .available },
                            respond: { _ in .alertSecondButtonReturn })
        XCTAssertEqual(probes, 0)
        XCTAssertFalse(setup.isPresented)
        var titles: [String] = []
        await setup.present(initial: .missing, recheck: { probes += 1; return .available },
                            respond: { alert in titles.append(alert.messageText); return .alertFirstButtonReturn })
        XCTAssertEqual(probes, 1)
        XCTAssertEqual(titles, ["Set up local sessions", "Local sessions are ready"])
        XCTAssertFalse(setup.isPresented)
    }

}
