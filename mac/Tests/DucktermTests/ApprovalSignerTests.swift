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
}
