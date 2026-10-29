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

    private let lite = AppProfile(id: "lite", edition: "lite", instance: "lite",
                                  home: URL(fileURLWithPath: "/Users/me/.duckterm-lite"), tmuxSocket: "duckterm-lite", port: 4519)
    private let full = AppProfile(id: "full", edition: "full", instance: nil,
                                  home: URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".duckterm"),
                                  tmuxSocket: "duckterm", port: 4300)

    private func server(_ id: String, _ profile: AppProfile, profileID: Any? = nil, instance: Any? = nil,
                        home: String? = nil, socket: String? = nil, port: Int? = nil) throws -> DesktopEdition {
        try DesktopEdition(response: [
            "id": id, "features": ["terminal"],
            "instance": [
                "identity_version": 1, "url": "http://127.0.0.1:\(port ?? profile.port)",
                "profile_id": profileID ?? profile.id, "instance_id": instance ?? (profile.instance ?? NSNull()),
                "home": home ?? profile.home.path, "tmux_socket": socket ?? profile.tmuxSocket, "port": port ?? profile.port,
            ] as [String: Any],
        ])
    }

    func testNamedAppCannotReuseAnotherEditionServer() throws {
        let legacy = try DesktopEdition(response: ["id": "full", "features": ["terminal"]])
        XCTAssertNoThrow(try legacy.validate(edition: "full", profile: full))
        XCTAssertNoThrow(try legacy.validate(edition: nil, profile: nil))
        XCTAssertThrowsError(try legacy.validate(edition: "lite", profile: lite))
    }

    func testAppOnlyUsesTheServerOfItsOwnProfile() throws {
        XCTAssertNoThrow(try server("lite", lite).validate(edition: "lite", profile: lite))
        // Every ownership facet must match, not only edition and port.
        for other in [
            try server("lite", lite, profileID: "test-lite"),
            try server("lite", lite, instance: "test-lite"),
            try server("lite", lite, home: "/Users/me/.duckterm-test-lite"),
            try server("lite", lite, socket: "duckterm"),
            try server("lite", lite, port: 4840),
            try server("code", lite),
        ] {
            XCTAssertThrowsError(try other.validate(edition: "lite", profile: lite))
        }
    }

    func testLegacyFullCompatibilityIsOnlyForTheLegacyWorkspace() throws {
        let legacy = try DesktopEdition(response: ["id": "full", "features": ["terminal"]])
        XCTAssertNil(legacy.instance)
        let test = AppProfile(id: "test", edition: "full", instance: "test",
                              home: URL(fileURLWithPath: "/Users/me/.duckterm-test"), tmuxSocket: "duckterm-test", port: 4655)
        XCTAssertThrowsError(try legacy.validate(edition: "full", profile: test))
        XCTAssertThrowsError(try DesktopEdition(response: ["id": "lite", "features": []]).validate(edition: "lite", profile: lite))
        // The global CLI's Full server: no profile id, every legacy facet equal.
        XCTAssertNoThrow(try server("full", full, profileID: NSNull()).validate(edition: "full", profile: full))
        XCTAssertThrowsError(try server("full", full, profileID: NSNull(), home: "/tmp/other").validate(edition: "full", profile: full))
        XCTAssertThrowsError(try server("full", test, profileID: NSNull()).validate(edition: "full", profile: test))
    }

    func testConflictNamesTheRunningAppWithoutSuggestingTakeover() throws {
        let code = AppProfile(id: "test-code", edition: "code", instance: "test-code",
                              home: URL(fileURLWithPath: "/Users/me/.duckterm-test-code"), tmuxSocket: "duckterm-test-code", port: 4519)
        do {
            try server("code", code).validate(edition: "lite", profile: lite)
            XCTFail("expected a conflict")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("Port 4519 is in use by DuckTerm Code Test"))
            XCTAssertFalse(error.localizedDescription.contains("restart"))
        }
    }

    func testMalformedInstanceIsRejected() {
        let good: [String: Any] = ["profile_id": "lite", "instance_id": "lite", "home": "/h", "tmux_socket": "s", "port": 4519]
        var bad: [Any] = ["lite"]
        for key in good.keys {
            var copy = good
            copy.removeValue(forKey: key)
            bad.append(copy)
        }
        bad.append(good.merging(["port": "4519"]) { _, new in new })
        bad.append(good.merging(["profile_id": 1]) { _, new in new })
        for instance in bad {
            XCTAssertThrowsError(try DesktopEdition(response: ["id": "lite", "features": [], "instance": instance]))
        }
        XCTAssertNoThrow(try DesktopEdition(response: ["id": "lite", "features": [], "instance": good]))
    }

    func testLegacyServerNeedsEveryCanonicalFullFacet() throws {
        let legacy = try DesktopEdition(response: ["id": "full", "features": ["terminal"]])
        XCTAssertTrue(full.isCanonicalFull)
        let variants = [
            AppProfile(id: "full", edition: "full", instance: nil, home: full.home, tmuxSocket: "duckterm", port: 4301),
            AppProfile(id: "full", edition: "full", instance: nil, home: full.home, tmuxSocket: "duckterm-x", port: 4300),
            AppProfile(id: "full", edition: "full", instance: "x", home: full.home, tmuxSocket: "duckterm", port: 4300),
            AppProfile(id: "full", edition: "full", instance: nil, home: URL(fileURLWithPath: "/tmp/.duckterm"), tmuxSocket: "duckterm", port: 4300),
        ]
        for profile in variants {
            XCTAssertFalse(profile.isCanonicalFull)
            XCTAssertThrowsError(try legacy.validate(edition: "full", profile: profile))
            XCTAssertThrowsError(try server("full", profile, profileID: NSNull()).validate(edition: "full", profile: profile))
        }
    }
}
