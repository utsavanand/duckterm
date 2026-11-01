import Darwin
import XCTest
@testable import DuckyCore

private final class TLSFixture {
    let process = Process()
    private let scratch = FileManager.default.temporaryDirectory.appendingPathComponent("ducky-tls-fixture-" + UUID().uuidString)
    let input = Pipe(), output = Pipe(), errors = Pipe()
    private(set) var invitation: Invitation!

    init(mode: String = "literal") throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["python3", root.appendingPathComponent("tests/fixtures/ducky_bridge.py").path, mode]
        // No inherited owner credentials, provider keys, instance settings or agent homes.
        process.environment = ["PATH": ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin", "PYTHONPATH": root.appendingPathComponent("src").path, "PYTHONDONTWRITEBYTECODE": "1", "DUCKTERM_NO_KEYCHAIN": "1", "DUCKTERM_SUMMARIZER": "off", "HOME": scratch.path, "TMPDIR": scratch.path, "DUCKTERM_HOME": scratch.path, "PYTHONNOUSERSITE": "1"]
        process.standardInput = input; process.standardOutput = output; process.standardError = errors
        do {
            try process.run()
            invitation = try Invitation.parse(line())
        } catch {
            if process.processIdentifier > 0 { close() }
            else { try? FileManager.default.removeItem(at: scratch) }
            throw error
        }
    }
    func line() throws -> String {
        var data = Data()
        let deadline = ProcessInfo.processInfo.systemUptime + 15
        while ProcessInfo.processInfo.systemUptime < deadline {
            var descriptor = pollfd(fd: output.fileHandleForReading.fileDescriptor, events: Int16(POLLIN), revents: 0)
            if poll(&descriptor, 1, 100) == 0 { continue }
            guard let byte = try output.fileHandleForReading.read(upToCount: 1), !byte.isEmpty else { throw DuckyError.invalidResponse }
            if byte == Data([10]) { return String(decoding: data, as: UTF8.self) }
            data.append(byte)
            guard data.count < 8192 else { throw DuckyError.invalidResponse }
        }
        throw URLError(.timedOut)
    }
    @discardableResult func command(_ text: String) throws -> String {
        try input.fileHandleForWriting.write(contentsOf: Data((text + "\n").utf8))
        return try line()
    }
    func counts() throws -> (requests: Int, handshakes: Int) {
        let raw = try command("stats -")
        let object = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as! [String: Any]
        XCTAssertEqual(object["ca"] as? Bool, false)
        return (object["requests"] as! Int, object["handshakes"] as! Int)
    }
    func close() {
        try? input.fileHandleForWriting.close()
        let deadline = ProcessInfo.processInfo.systemUptime + 4
        while process.isRunning && ProcessInfo.processInfo.systemUptime < deadline { Thread.sleep(forTimeInterval: 0.02) }
        if process.isRunning {
            process.terminate()
            let stop = ProcessInfo.processInfo.systemUptime + 1
            while process.isRunning && ProcessInfo.processInfo.systemUptime < stop { Thread.sleep(forTimeInterval: 0.02) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            XCTFail("TLS fixture needed forced cleanup")
        }
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        try? FileManager.default.removeItem(at: scratch)
    }
}

private actor AddressSequence {
    var values: [String]
    var lookups = 0
    init(_ values: [String]) { self.values = values }
    func resolve(_ name: String) -> [String] { lookups += 1; return values }
    func update(_ values: [String]) { self.values = values }
}

final class TLSIntegrationTests: XCTestCase {
    func testNativeClientPairsWithRealTLSHostAndHonorsRevocation() async throws {
        let fixture = try TLSFixture()
        defer { fixture.close() }
        let invitation = fixture.invitation!
        let host = PairedHost(invitation: invitation, token: try DuckyClient.newCredential())
        let client = try DuckyClient(host: host)
        defer { client.close() }
        let pending = try await client.claim(invitation, name: "Test iPhone")
        XCTAssertEqual(pending.status, "pending")
        do { _ = try await client.snapshot(cursor: nil); XCTFail("Unapproved device read status") }
        catch DuckyError.unavailable(let code) { XCTAssertEqual(code, 403) }
        XCTAssertEqual(try fixture.command("approve \(pending.id)"), "ok")
        let snapshot = try await client.snapshot(cursor: nil)
        XCTAssertEqual(snapshot.sessions.first?.name, "Native protocol test")
        XCTAssertEqual(snapshot.folders, ["Test folder"])
        XCTAssertEqual(try fixture.command("revoke \(pending.id)"), "ok")
        do { _ = try await client.snapshot(cursor: nil); XCTFail("Revoked device read status") }
        catch DuckyError.unavailable(let code) { XCTAssertEqual(code, 403) }
        _ = try fixture.counts() // Actual self-signed CA:FALSE leaf passed Apple trust evaluation.
    }

    func testLocalHostnameSurvivesAddressChurnAndConnectionOnlyFallback() async throws {
        let fixture = try TLSFixture(mode: "local")
        defer { fixture.close() }
        let invitation = fixture.invitation!
        XCTAssertEqual(invitation.url.host, "ducky-native-fixture.local")
        XCTAssertNil(invitation.tlsName)
        let addresses = AddressSequence(["8.8.8.8", "fe80::1", "127.0.0.1"])
        let host = PairedHost(invitation: invitation, token: try DuckyClient.newCredential())
        let client = try DuckyClient(host: host, resolver: { await addresses.resolve($0) })
        defer { client.close() }
        let pending = try await client.claim(invitation, name: "Test iPhone")
        XCTAssertEqual(try fixture.command("approve \(pending.id)"), "ok")
        await addresses.update(["::1"])
        let snapshot = try await client.snapshot(cursor: nil)
        XCTAssertEqual(snapshot.sessions.first?.name, "Native protocol test")
        let lookups = await addresses.lookups
        XCTAssertEqual(lookups, 2, "Resolve again rather than cache the old DHCP address")
        // IPv4 is now closed. A connection failure may try the other private literal.
        XCTAssertEqual(try fixture.command("stop-v4 -"), "ok")
        await addresses.update(["127.0.0.1", "::1"])
        let current = try await client.pairingStatus()
        XCTAssertEqual(current.status, "active")
        XCTAssertEqual(try fixture.counts().requests, 3)
    }

    func testPinAndOriginalNameFailuresNeverSendCredentialsOrTryAnotherAddress() async throws {
        let fixture = try TLSFixture(mode: "local")
        defer { fixture.close() }
        let original = fixture.invitation!
        let token = try DuckyClient.newCredential()
        let wrongPin = Invitation(version: original.version, hostID: original.hostID, invite: original.invite,
            expiresAt: original.expiresAt, url: original.url, certificateSHA256: String(repeating: "0", count: 64))
        let wrongName = Invitation(version: original.version, hostID: original.hostID, invite: original.invite,
            expiresAt: original.expiresAt, url: URL(string: "https://other-fixture.local:\(original.url.port!)")!, certificateSHA256: original.certificateSHA256)
        for invitation in [wrongPin, wrongName] {
            let before = try fixture.counts()
            let client = try DuckyClient(host: PairedHost(invitation: invitation, token: token), resolver: { _ in ["127.0.0.1", "::1"] })
            defer { client.close() }
            do { _ = try await client.claim(invitation, name: "Test iPhone"); XCTFail("Invalid TLS identity accepted") }
            catch { XCTAssertTrue(error is URLError) }
            let after = try fixture.counts()
            XCTAssertEqual(after.requests, before.requests, "No HTTP credential/body may reach a rejected TLS host")
            XCTAssertEqual(after.handshakes, before.handshakes + 1, "TLS rejection must not retry another address")
        }
    }

    func testRedirectNeverForwardsTheDeviceCredential() async throws {
        let fixture = try TLSFixture(mode: "local")
        defer { fixture.close() }
        let invitation = fixture.invitation!
        let client = try DuckyClient(host: PairedHost(invitation: invitation, token: try DuckyClient.newCredential()), resolver: { _ in ["127.0.0.1"] })
        defer { client.close() }
        XCTAssertEqual(try fixture.command("redirect -"), "ok")
        do { _ = try await client.snapshot(cursor: nil); XCTFail("Redirect was followed") }
        catch DuckyError.unavailable(let status) { XCTAssertEqual(status, 302) }
        XCTAssertEqual(try fixture.counts().requests, 1)
    }

    func testLegacyIPCertificateCanBeReusedByStableNameWithoutChangingPin() async throws {
        let fixture = try TLSFixture(mode: "legacy-local")
        defer { fixture.close() }
        let invitation = fixture.invitation!
        XCTAssertEqual(invitation.tlsName, "127.0.0.1")
        let host = PairedHost(invitation: invitation, token: try DuckyClient.newCredential())
        // Connect to a DIFFERENT private address; verify the old certificate identity and pin.
        let client = try DuckyClient(host: host, resolver: { _ in ["::1"] })
        defer { client.close() }
        let pending = try await client.claim(invitation, name: "Test iPhone")
        XCTAssertEqual(try fixture.command("approve \(pending.id)"), "ok")
        let snapshot = try await client.snapshot(cursor: nil)
        XCTAssertEqual(snapshot.hostID, invitation.hostID)
        let before = try fixture.counts()
        let altered = Invitation(version: invitation.version, hostID: invitation.hostID, invite: invitation.invite,
            expiresAt: invitation.expiresAt, url: invitation.url, certificateSHA256: invitation.certificateSHA256, tlsName: "::1")
        do { _ = try await client.claim(altered, name: "Test iPhone"); XCTFail("Claim changed stored TLS identity") }
        catch DuckyError.invalidInvitation { }
        XCTAssertEqual(try fixture.counts().requests, before.requests)
        let wrong = try DuckyClient(host: PairedHost(invitation: altered, token: host.token), resolver: { _ in ["::1"] })
        defer { wrong.close() }
        do { _ = try await wrong.snapshot(cursor: nil); XCTFail("Connection IP substituted for pinned TLS identity") }
        catch { XCTAssertTrue(error is URLError) }
        XCTAssertEqual(try fixture.counts().requests, before.requests)
    }
}
