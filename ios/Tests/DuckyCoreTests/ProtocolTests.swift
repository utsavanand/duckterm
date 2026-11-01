import XCTest
@testable import DuckyCore
final class ProtocolTests: XCTestCase {
    func invitation(url: String = "https://192.168.1.4:4430", expires: Int = 1000) throws -> String {
        let object: [String: Any] = ["version": 1, "url": url, "host_id": String(repeating: "a", count: 32), "certificate_sha256": String(repeating: "b", count: 64), "invite": String(repeating: "c", count: 43), "expires_at": expires]
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
