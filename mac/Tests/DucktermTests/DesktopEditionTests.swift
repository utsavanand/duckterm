import XCTest
@testable import Duckterm

final class DesktopEditionTests: XCTestCase {
    func testAvailabilityComesFromServerFeatures() throws {
        let code = try DesktopEdition(response: ["id": "code", "features": ["terminal", "inbox", "pr_review"]])
        XCTAssertTrue(code.includes("pr_review"))
        XCTAssertFalse(code.includes("remote_workspaces"))
        XCTAssertFalse(code.includes("artifacts"))
        let full = try DesktopEdition(response: ["id": "full", "features": ["terminal", "remote_workspaces"]])
        XCTAssertTrue(full.includes("remote_workspaces"))
        XCTAssertFalse(full.includes("pr_review"))
    }

    func testInvalidDescriptionsCannotBecomeFull() {
        for response: Any in [[:], ["id": "full"], ["id": "unknown", "features": []], ["id": "full", "features": [1]]] {
            XCTAssertThrowsError(try DesktopEdition(response: response))
        }
    }

    func testRemoteCallbacksRequireAvailabilityBeforeConnecting() {
        for operation in ["launch", "browse", "session-request", "terminal-open", "terminal-input"] {
            XCTAssertEqual(DesktopEdition.requiredFeature(target: "remote", operation: operation), "remote_workspaces")
        }
        XCTAssertEqual(DesktopEdition.requiredFeature(target: "local", operation: "project-preview"), "remote_workspaces")
        XCTAssertEqual(DesktopEdition.requiredFeature(target: "remote", operation: "collaboration-connect"), "inbox")
        XCTAssertNil(DesktopEdition.requiredFeature(target: "local", operation: "launch"))
        XCTAssertNil(DesktopEdition.requiredFeature(target: "local", operation: "terminal-open"))
    }

    func testEditionRequestHasFixedLocalRouteAndNoCallerParameters() throws {
        let base = URL(string: "http://127.0.0.1:4300")!
        let request = try LaunchDestination.request(base: base, operation: "edition", params: [:])
        XCTAssertEqual(request.url?.path, "/edition")
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertThrowsError(try LaunchDestination.request(base: base, operation: "edition", params: ["id": "full"]))
    }

    func testNamedAppCannotReuseAnotherEditionServer() throws {
        let full = try DesktopEdition(response: ["id": "full", "features": ["terminal"]])
        XCTAssertNoThrow(try full.validate(expected: "full"))
        XCTAssertNoThrow(try full.validate(expected: nil))
        XCTAssertThrowsError(try full.validate(expected: "lite"))
        XCTAssertThrowsError(try full.validate(expected: "code"))
        XCTAssertThrowsError(try full.validate(expected: "invalid"))
    }
}
