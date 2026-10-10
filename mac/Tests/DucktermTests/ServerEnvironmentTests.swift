import XCTest
@testable import Duckterm

final class ServerEnvironmentTests: XCTestCase {
    func testDockLaunchFindsCLIAndItsChildTools() throws {
        let testHome = FileManager.default.temporaryDirectory.appendingPathComponent("duckterm startup \(UUID().uuidString)")
        let bin = testHome.appendingPathComponent(".local/bin")
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: testHome) }
        for (name, script) in [
            "duckterm": "#!/bin/sh\nexec /usr/bin/env tmux\n",
            "tmux": "#!/bin/sh\nprintf 'terminal-tool-found'\n",
        ] {
            let file = bin.appendingPathComponent(name)
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
        }
        let environment = ServerEnvironment.make(
            inheriting: ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"],
            homeDirectory: testHome.path
        )
        let binary = try XCTUnwrap(ServerEnvironment.executable(named: "duckterm", environment: environment))
        XCTAssertEqual(binary, bin.appendingPathComponent("duckterm").path)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: binary)
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        try process.run()
        let output = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        XCTAssertEqual(String(data: output, encoding: .utf8), "terminal-tool-found")
    }

    func testPreservesCustomPathPriorityAndInstanceEnvironment() {
        let original = ["PATH": "/custom/tools:/opt/homebrew/bin:/usr/bin", "DUCKTERM_INSTANCE": "test", "LANG": "en_US.UTF-8"]
        let environment = ServerEnvironment.make(inheriting: original, homeDirectory: "/Users/test")
        let path = environment["PATH"]!.split(separator: ":").map(String.init)
        XCTAssertEqual(Array(path.prefix(3)), ["/custom/tools", "/opt/homebrew/bin", "/usr/bin"])
        XCTAssertEqual(path.filter { $0 == "/opt/homebrew/bin" }.count, 1)
        XCTAssertTrue(path.contains("/usr/local/bin"))
        XCTAssertTrue(path.contains("/Users/test/.local/bin"))
        XCTAssertEqual(environment["DUCKTERM_INSTANCE"], "test")
        XCTAssertEqual(environment["LANG"], original["LANG"])
    }

    func testMissingPathStillFindsSystemTools() {
        let environment = ServerEnvironment.make(inheriting: [:], homeDirectory: "/Users/test")
        XCTAssertNotNil(ServerEnvironment.executable(named: "sh", environment: environment))
        XCTAssertNil(ServerEnvironment.executable(named: "duckterm-missing-\(UUID().uuidString)", environment: environment))
    }
}
