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
        XCTAssertNoThrow(try full.validate(edition: "full", profile: nil, port: 4300))
        XCTAssertNoThrow(try full.validate(edition: nil, profile: nil, port: 4300))
        for other in ["lite", "code", "invalid"] {
            XCTAssertThrowsError(try full.validate(edition: other, profile: nil, port: 4300))
        }
    }

    private func server(_ id: String, profile: String, port: Int) throws -> DesktopEdition {
        try DesktopEdition(response: [
            "id": id, "features": ["terminal"],
            "instance": ["profile_id": profile, "port": port, "instance_id": profile, "identity_version": 1],
        ])
    }

    func testAppOnlyUsesTheServerOfItsOwnProfile() throws {
        XCTAssertNoThrow(try server("lite", profile: "lite", port: 4519).validate(edition: "lite", profile: "lite", port: 4519))
        XCTAssertNoThrow(try server("lite", profile: "test-lite", port: 4700).validate(edition: "lite", profile: "test-lite", port: 4700))
        // Same edition but another profile or port, e.g. a test build's server.
        XCTAssertThrowsError(try server("lite", profile: "test-lite", port: 4519).validate(edition: "lite", profile: "lite", port: 4519))
        XCTAssertThrowsError(try server("lite", profile: "lite", port: 4800).validate(edition: "lite", profile: "lite", port: 4519))
        XCTAssertThrowsError(try server("code", profile: "code", port: 4800).validate(edition: "lite", profile: "lite", port: 4800))
    }

    func testPreProfileServerIsAcceptedOnlyAsLegacyFull() throws {
        let legacyFull = try DesktopEdition(response: ["id": "full", "features": ["terminal"]])
        XCTAssertNil(legacyFull.profile)
        XCTAssertNoThrow(try legacyFull.validate(edition: "full", profile: "full", port: 4300))
        XCTAssertThrowsError(try legacyFull.validate(edition: "full", profile: "test", port: 4300))
        let legacyLite = try DesktopEdition(response: ["id": "lite", "features": ["terminal"]])
        XCTAssertThrowsError(try legacyLite.validate(edition: "lite", profile: "lite", port: 4519))
        XCTAssertThrowsError(try legacyLite.validate(edition: "full", profile: "full", port: 4300))
    }

    func testConflictNamesTheRunningAppWithoutSuggestingTakeover() throws {
        do {
            try server("code", profile: "test-code", port: 4519).validate(edition: "lite", profile: "lite", port: 4519)
            XCTFail("expected a conflict")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("Port 4519 is in use by DuckTerm Code Test"))
            XCTAssertFalse(error.localizedDescription.contains("restart"))
        }
    }

    func testMalformedInstanceIsRejected() {
        for instance: Any in ["lite", ["profile_id": "lite"], ["port": 4519], ["profile_id": 1, "port": 4519]] {
            XCTAssertThrowsError(try DesktopEdition(response: ["id": "lite", "features": [], "instance": instance]))
        }
    }
}
