import XCTest
@testable import Duckterm

final class ApprovalSignerTests: XCTestCase {
    private let head = String(repeating: "ab", count: 20)

    private func payload(_ overrides: [String: String] = [:], header: String = "duckterm-approve v1") -> String {
        var values = ["repo": "/Users/me/duckterm", "github": "o/duckterm", "pr": "312", "head": head,
                      "folder": "Team/features", "release": "rel-1", "nonce": "n1", "expires": "1800000000"]
        values.merge(overrides) { _, new in new }
        return ([header] + ApprovalSigner.fields.map { "\($0)=\(values[$0]!)" }).joined(separator: "\n")
    }

    func testPromptReasonComesFromTheSignedPayload() throws {
        let request = try XCTUnwrap(ApprovalSigner.parse(payload()))
        XCTAssertEqual(request.reason, "approve PR #312 in o/duckterm at abababa: merge and release")
        XCTAssertEqual(request.folder, "Team/features")
    }

    func testAnythingButAServerChallengeIsRefused() {
        XCTAssertNil(ApprovalSigner.parse(payload(header: "duckterm-approve v2")))
        XCTAssertNil(ApprovalSigner.parse(payload(["pr": "312; rm"])))
        XCTAssertNil(ApprovalSigner.parse(payload(["head": "abc"])))
        XCTAssertNil(ApprovalSigner.parse(payload(["github": "not-a-slug"])))
        XCTAssertNil(ApprovalSigner.parse(payload(["folder": ""])))
        XCTAssertNil(ApprovalSigner.parse(payload() + "\nextra=1"))
        XCTAssertNil(ApprovalSigner.parse(payload().replacingOccurrences(of: "nonce=", with: "once=")))
        XCTAssertNil(ApprovalSigner.parse(String(repeating: "x", count: 5000)))
    }

    func testBoundKeyFollowsDucktermHomeAndPemWrapsAt64() {
        XCTAssertEqual(ApprovalSigner.boundKeyURL(environment: ["DUCKTERM_HOME": "/tmp/dt-home"]).path, "/tmp/dt-home/approval-key.pem")
        let pem = ApprovalSigner.pem(Data(repeating: 7, count: 91))
        let lines = pem.split(separator: "\n")
        XCTAssertEqual(lines.first, "-----BEGIN PUBLIC KEY-----")
        XCTAssertEqual(lines.last, "-----END PUBLIC KEY-----")
        XCTAssertEqual(lines.dropFirst().dropLast().map(\.count), [64, 60])
        XCTAssertEqual(Data(base64Encoded: lines.dropFirst().dropLast().joined()), Data(repeating: 7, count: 91))
    }

    func testAnExistingBindingOrPlantedLinkIsNeverReplaced() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("approval \(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("approval-key.pem")
        try "existing".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertFalse(try ApprovalSigner.bindIfUnbound(at: file))
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "existing")
        let link = dir.appendingPathComponent("linked.pem")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: dir.appendingPathComponent("nowhere"))
        XCTAssertFalse(try ApprovalSigner.bindIfUnbound(at: link))
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("nowhere").path))
    }
}
