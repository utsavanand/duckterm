import XCTest
@testable import Duckterm

final class OwnerTokenTests: XCTestCase {
    private func workspace(_ home: URL, _ name: String, port: Int, token: String) throws {
        let credentials = home.appendingPathComponent(name).appendingPathComponent("session-credentials")
        try FileManager.default.createDirectory(at: credentials, withIntermediateDirectories: true)
        let server = #"{"url": "http://127.0.0.1:\#(port)", "instance": {"port": \#(port)}}"#
        try server.write(to: credentials.appendingPathComponent("server.json"), atomically: true, encoding: .utf8)
        try (token + "\n").write(to: home.appendingPathComponent(name).appendingPathComponent("token"), atomically: true, encoding: .utf8)
    }

    /// Runs the script the way `ssh host sh -s` does, against a fake remote home.
    private func runRemoteScript(home: URL, port: Int) throws -> (Int32, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-s"]
        process.environment = ["HOME": home.path, "PATH": "/usr/bin:/bin"]
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        try process.run()
        input.fileHandleForWriting.write(Data(OwnerToken.remoteScript(port: port).utf8))
        try input.fileHandleForWriting.close()
        process.waitUntilExit()
        let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return (process.terminationStatus, text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func testRemoteScriptPrintsTheTokenOfTheWorkspaceServingThePort() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: home) }
        try workspace(home, ".duckterm", port: 4300, token: "full-token")
        try workspace(home, ".duckterm-code", port: 4301, token: "code-token")
        XCTAssertEqual(try runRemoteScript(home: home, port: 4301).1, "code-token")
        XCTAssertEqual(try runRemoteScript(home: home, port: 4300).1, "full-token")
    }

    func testRemoteScriptFailsWhenNoWorkspaceServesThePort() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: home) }
        try workspace(home, ".duckterm", port: 4300, token: "full-token")
        // 430 is a prefix of 4300; a loose match would hand out Full's token.
        let (status, text) = try runRemoteScript(home: home, port: 430)
        XCTAssertNotEqual(status, 0)
        XCTAssertEqual(text, "")
    }

    func testTokenCommandRunsOnlyAShellReadingStdinWithoutForwarding() throws {
        let host = try RemoteHost(name: "Work", target: "duckterm@server.example")
        let args = host.tokenArguments()
        XCTAssertEqual(Array(args.suffix(3)), ["duckterm@server.example", "sh", "-s"])
        XCTAssertTrue(args.contains("StrictHostKeyChecking=yes"))
        XCTAssertTrue(args.contains("ClearAllForwardings=yes"))
        XCTAssertTrue(args.contains("ForwardAgent=no"))
        XCTAssertFalse(args.contains("-L"))
    }

    func testOnlyTokenShapedFileContentIsUsed() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: home) }
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        let file = home.appendingPathComponent("token")
        try "abc_DEF-123\n".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertEqual(OwnerToken.local(home: home), "abc_DEF-123")
        try "<html>not a token</html>".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertNil(OwnerToken.local(home: home))
    }
}
