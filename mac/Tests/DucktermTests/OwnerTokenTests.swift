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

    private final class Counter: @unchecked Sendable {
        private let lock = NSLock()
        private var value = 0
        func next() -> Int { lock.withLock { value += 1; return value } }
        var count: Int { lock.withLock { value } }
    }

    private var clock = Date(timeIntervalSince1970: 1_000_000)

    override func tearDown() {
        OwnerToken.fetch = { _ in throw LaunchDestination.Failure.message("not in tests") }
        OwnerToken.now = Date.init
        super.tearDown()
    }

    /// Registers a tunnel whose SSH read always fails, counting the reads.
    private func failingRemote(port: Int, delay: UInt64 = 0) throws -> Counter {
        let reads = Counter()
        OwnerToken.fetch = { _ in
            _ = reads.next()
            if delay > 0 { try await Task.sleep(nanoseconds: delay) }
            throw LaunchDestination.Failure.message("no server.json")
        }
        OwnerToken.now = { [unowned self] in self.clock }
        OwnerToken.register(tunnelPort: port, host: try RemoteHost(name: "Work", target: "duckterm@server.example"))
        return reads
    }

    private func ask(_ port: Int, open: Bool = false) async -> Bool {
        (try? await OwnerToken.token(base: URL(string: "http://127.0.0.1:\(port)")!, open: open)) != nil
    }

    func testAFailedReadIsNotRetriedUntilTheBackoffEnds() async throws {
        let reads = try failingRemote(port: 50101)
        for _ in 0..<20 { _ = await ask(50101) }
        XCTAssertEqual(reads.count, 1)
        clock += 29
        _ = await ask(50101)
        XCTAssertEqual(reads.count, 1)
        clock += 2
        _ = await ask(50101)
        XCTAssertEqual(reads.count, 2)
        // The second failure waits twice as long: 60 s.
        clock += 59
        _ = await ask(50101)
        XCTAssertEqual(reads.count, 2)
        clock += 2
        _ = await ask(50101)
        XCTAssertEqual(reads.count, 3)
    }

    func testTheBackoffStopsAtFiveMinutes() async throws {
        let reads = try failingRemote(port: 50102)
        for _ in 0..<8 { _ = await ask(50102); clock += OwnerToken.maxBackoff + 1 }
        XCTAssertEqual(reads.count, 8)
    }

    func testConcurrentCallsShareOneRead() async throws {
        let reads = try failingRemote(port: 50103, delay: 200_000_000)
        await withTaskGroup(of: Bool.self) { group in
            for _ in 0..<10 { group.addTask { await self.ask(50103) } }
        }
        XCTAssertEqual(reads.count, 1)
    }

    func testANewTunnelRetriesAtOnce() async throws {
        let reads = try failingRemote(port: 50104)
        _ = await ask(50104)
        OwnerToken.reset(tunnelPort: 50104)
        _ = await ask(50104)
        XCTAssertEqual(reads.count, 2)
    }

    func testAnOlderServerIsReportedWithoutSSH() async throws {
        let reads = try failingRemote(port: 50105)
        do {
            _ = try await OwnerToken.token(base: URL(string: "http://127.0.0.1:50105")!, open: true)
            XCTFail("an older server has no token to read")
        } catch {
            XCTAssertEqual(error.localizedDescription, "DuckTerm on Work is older than this app. Update DuckTerm there, then reconnect.")
        }
        XCTAssertEqual(reads.count, 0)
    }

    func testASuccessfulReadIsCached() async throws {
        let reads = Counter()
        OwnerToken.fetch = { _ in _ = reads.next(); return "remote-token" }
        OwnerToken.register(tunnelPort: 50106, host: try RemoteHost(name: "Work", target: "duckterm@server.example"))
        let base = URL(string: "http://127.0.0.1:50106")!
        for _ in 0..<5 {
            let token = try await OwnerToken.token(base: base)
            XCTAssertEqual(token, "remote-token")
        }
        XCTAssertEqual(reads.count, 1)
    }
}
