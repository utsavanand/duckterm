import XCTest
@testable import DuckyCore

final class TLSIntegrationTests: XCTestCase {
    func testNativeClientPairsWithRealTLSHostAndHonorsRevocation() async throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", root.appendingPathComponent("tests/fixtures/ducky_bridge.py").path]
        // Do not inherit session credentials, provider keys, instance settings or agent homes.
        process.environment = ["PATH": ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin", "PYTHONPATH": root.appendingPathComponent("src").path, "PYTHONDONTWRITEBYTECODE": "1", "DUCKTERM_NO_KEYCHAIN": "1", "DUCKTERM_SUMMARIZER": "off"]
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input; process.standardOutput = output; process.standardError = errors
        try process.run()
        defer {
            try? input.fileHandleForWriting.close()
            // The fixture closes its bridge, purges test sessions and removes its temp home on EOF.
            process.waitUntilExit()
        }
        func line() throws -> String {
            var data = Data()
            while let byte = try output.fileHandleForReading.read(upToCount: 1), !byte.isEmpty {
                if byte == Data([10]) { break }
                data.append(byte)
                guard data.count < 8192 else { throw DuckyError.invalidResponse }
            }
            guard !data.isEmpty else { throw DuckyError.invalidResponse }
            return String(decoding: data, as: UTF8.self)
        }
        let invitation = try Invitation.parse(line())
        let host = PairedHost(invitation: invitation, token: try DuckyClient.newCredential())
        let client = try DuckyClient(host: host)
        defer { client.close() }
        let pending = try await client.claim(invitation, name: "Test iPhone")
        XCTAssertEqual(pending.status, "pending")
        do { _ = try await client.snapshot(cursor: nil); XCTFail("Unapproved device read status") }
        catch DuckyError.unavailable(let code) { XCTAssertEqual(code, 403) }
        try input.fileHandleForWriting.write(contentsOf: Data("approve \(pending.id)\n".utf8))
        XCTAssertEqual(try line(), "ok")
        let snapshot = try await client.snapshot(cursor: nil)
        XCTAssertEqual(snapshot.sessions.first?.name, "Native protocol test")
        XCTAssertEqual(snapshot.folders, ["Test folder"])
        let wrong = Invitation(version: invitation.version, hostID: invitation.hostID, invite: invitation.invite, expiresAt: invitation.expiresAt, url: invitation.url, certificateSHA256: String(repeating: "0", count: 64))
        let untrusted = try DuckyClient(host: PairedHost(invitation: wrong, token: host.token))
        defer { untrusted.close() }
        do { _ = try await untrusted.snapshot(cursor: nil); XCTFail("Wrong certificate pin accepted") }
        catch { /* TLS must fail before the device credential reaches another host. */ }
        try input.fileHandleForWriting.write(contentsOf: Data("revoke \(pending.id)\n".utf8))
        XCTAssertEqual(try line(), "ok")
        do { _ = try await client.snapshot(cursor: nil); XCTFail("Revoked device read status") }
        catch DuckyError.unavailable(let code) { XCTAssertEqual(code, 403) }
    }
}
