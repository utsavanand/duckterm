import XCTest
@testable import DuckyCore
final class ProtocolTests: XCTestCase {
    func invitation(url: String = "https://192.168.1.4:4430", expires: Int = 1000, tlsName: String? = nil) throws -> String {
        var object: [String: Any] = ["version": 1, "url": url, "host_id": String(repeating: "a", count: 32), "certificate_sha256": String(repeating: "b", count: 64), "invite": String(repeating: "c", count: 43), "expires_at": expires]
        if let tlsName { object["tls_name"] = tlsName }
        let encoded = try JSONSerialization.data(withJSONObject: object).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        return "ducky://pair?invite=" + encoded
    }
    func testInvitationRequiresPrivateTLSAndRejectsURLsWithAuthorityOrActions() throws {
        let now = Date(timeIntervalSince1970: 500)
        XCTAssertEqual(try Invitation.parse(invitation(), now: now).url.absoluteString, "https://192.168.1.4:4430")
        for url in ["http://192.168.1.4:4430", "https://example.com:4430", "https://8.8.8.8:4430", "https://user:password@192.168.1.4:4430", "https://192.168.1.4:4430/sessions/launch", "https://192.168.1.4:4430?token=owner", "https://0.0.0.0:4430", "https://169.254.1.1:4430"] {
            XCTAssertThrowsError(try Invitation.parse(invitation(url: url), now: now), url)
        }
        XCTAssertThrowsError(try Invitation.parse(invitation(expires: 499), now: now))
        XCTAssertThrowsError(try Invitation.parse(invitation() + "&invite=duplicate", now: now))
    }
    func testLocalEndpointAndTLSIdentityAreNarrowlyValidated() throws {
        let now = Date(timeIntervalSince1970: 500)
        for name in ["mac.local", "My-Mac.local", "a.local", String(repeating: "a", count: 63) + ".local"] {
            XCTAssertNoThrow(try Invitation.parse(invitation(url: "https://\(name):4430"), now: now))
        }
        for name in ["mac.local.evil", "nested.mac.local", "-mac.local", "mac-.local", "mac_.local", "mac.local.", "mác.local", String(repeating: "a", count: 64) + ".local"] {
            XCTAssertThrowsError(try Invitation.parse(invitation(url: "https://\(name):4430"), now: now), name)
        }
        for name in ["127.0.0.1", "192.168.1.4", "::1", "fd00::123", "mac.local"] {
            XCTAssertNoThrow(try Invitation.parse(invitation(url: "https://mac.local:4430", tlsName: name), now: now))
        }
        for name in ["8.8.8.8", "169.254.1.1", "fe80::1", "127.1", "other.local", "https://mac.local", "mac.local.", "[::1]", "::ffff:127.0.0.1", "mac.local\n", "K.local"] {
            XCTAssertThrowsError(try Invitation.parse(invitation(url: "https://mac.local:4430", tlsName: name), now: now), name)
        }
    }
    func testSavedHostsRemainCompatibleAndPersistLegacyTLSIdentity() throws {
        let legacy = try Invitation.parse(invitation(), now: Date(timeIntervalSince1970: 500))
        let host = PairedHost(invitation: legacy, token: String(repeating: "t", count: 43))
        let bytes = try JSONEncoder().encode(host)
        XCTAssertNil((try JSONSerialization.jsonObject(with: bytes) as? [String: Any])?["tlsName"])
        XCTAssertEqual(try JSONDecoder().decode(PairedHost.self, from: bytes), host)
        let updated = try Invitation.parse(invitation(url: "https://mac.local:4430", tlsName: "192.168.1.4"), now: Date(timeIntervalSince1970: 500))
        let saved = PairedHost(invitation: updated, token: host.token)
        XCTAssertEqual(try JSONDecoder().decode(PairedHost.self, from: JSONEncoder().encode(saved)).tlsName, "192.168.1.4")
        XCTAssertEqual(try JSONDecoder().decode(Invitation.self, from: JSONEncoder().encode(updated)).tlsName, "192.168.1.4")
    }
    func testResolutionFiltersEveryAnswerAndIsDeterministicAndBounded() async throws {
        let url = URL(string: "https://mac.local:4430")!
        let addresses = try await LocalEndpoint.addresses(for: url) { name in
            XCTAssertEqual(name, "mac.local")
            return ["8.8.8.8", "fe80::1%en0", "169.254.1.1", "::ffff:127.0.0.1", "10.0.0.3", "fd00::1", "10.0.0.1", "10.0.0.2", "10.0.0.1"]
        }
        XCTAssertEqual(addresses, ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        for answers in [[], ["8.8.8.8"], ["169.254.1.1", "fe80::1"], ["another.local"], ["0.0.0.0"]] {
            do { _ = try await LocalEndpoint.addresses(for: url) { _ in answers }; XCTFail("Forbidden resolution accepted") }
            catch { XCTAssertEqual((error as? URLError)?.code, .cannotFindHost) }
        }
        let literal = try await LocalEndpoint.addresses(for: URL(string: "https://[::1]:4430")!) { _ in
            XCTFail("Literal endpoint must not resolve DNS"); return []
        }
        XCTAssertEqual(literal, ["::1"])
    }
    func testCredentialsAreUnpredictableAndSeparateFromInvitation() throws {
        let invitation = try Invitation.parse(invitation(), now: Date(timeIntervalSince1970: 500))
        let a = try DuckyClient.newCredential(), b = try DuckyClient.newCredential()
        XCTAssertEqual(a.count, 43); XCTAssertNotEqual(a, b); XCTAssertNotEqual(a, invitation.invite)
        XCTAssertNoThrow(try DuckyClient(host: PairedHost(invitation: invitation, token: a)))
        XCTAssertThrowsError(try DuckyClient(host: PairedHost(invitation: invitation, token: "owner-token")))
    }
    func testSnapshotValidatesHostAndReadOnlyContractAndDeduplicatesNotices() throws {
        var body: [String: Any] = ["version": 1, "host_id": "host", "generated_at": 1000, "read_only": true, "sessions": [], "folders": ["Work"], "truncated": false, "cursor": "event", "reset": false, "notices": [["id": "event", "session_id": "agent", "kind": "turn_finished", "at": 1000]], "notification_delivery": "foreground_refresh"]
        func decode() throws -> HostSnapshot { try JSONDecoder().decode(HostSnapshot.self, from: JSONSerialization.data(withJSONObject: body)) }
        let snapshot = try decode()
        XCTAssertNoThrow(try snapshot.validate(hostID: "host")); XCTAssertThrowsError(try snapshot.validate(hostID: "another-host"))
        var tracker = NoticeTracker()
        XCTAssertEqual(tracker.newNotices(in: snapshot).count, 1); XCTAssertEqual(tracker.newNotices(in: snapshot).count, 0)
        XCTAssertFalse(snapshot.notices[0].genericText.contains("task completed"))
        body["read_only"] = false
        XCTAssertThrowsError(try decode().validate(hostID: "host"))
        body["read_only"] = true; body["reset"] = true
        var freshTracker = NoticeTracker()
        XCTAssertEqual(freshTracker.newNotices(in: try decode()).count, 0)
    }
}
