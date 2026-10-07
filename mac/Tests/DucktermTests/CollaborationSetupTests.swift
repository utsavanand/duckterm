import XCTest
@testable import Duckterm

final class CollaborationSetupTests: XCTestCase {
    @MainActor
    func testCancelledAfterQueueReadIsNeverForwarded() async throws {
        let setup = CollaborationSetup()
        var forwarded = false
        try await setup.flush { hub, route, _, _ in
            if route == "/collaboration/owner-queue" {
                return ["commands": [["operation": ["id": "cancelled"]]]]
            }
            if route == "/collaboration/owner-attempt" { return ["attempted": false] }
            if hub { forwarded = true }
            return [:]
        }
        XCTAssertFalse(forwarded)
    }

    @MainActor
    func testLostLocalReceiptRetriesCommittedOperationWithoutRecordingRejection() async throws {
        let setup = CollaborationSetup()
        var applied: [String] = []
        var receipts: [[String: Any]] = []
        var failReceipt = true
        let send: (Bool, String, String, [String: Any]) async throws -> [String: Any] = { _, route, _, body in
            switch route {
            case "/collaboration/owner-queue":
                return ["commands": [["operation": ["id": "same-id", "action": "move"]]]]
            case "/collaboration/owner-attempt": return ["attempted": true]
            case "/collaboration/owner-apply": applied.append(body["id"] as! String)
            case "/collaboration/owner-result":
                receipts.append(body)
                if failReceipt {
                    failReceipt = false
                    throw CollaborationSetup.HTTPFailure(status: 409, message: "Local receipt unavailable")
                }
            default: break
            }
            return [:]
        }
        do {
            try await setup.flush(send: send)
            XCTFail("Expected the lost local receipt to remain retryable")
        } catch let error as CollaborationSetup.HTTPFailure {
            XCTAssertEqual(error.status, 409)
        }
        try await setup.flush(send: send)
        XCTAssertEqual(applied, ["same-id", "same-id"])
        XCTAssertEqual(receipts.count, 2)
        XCTAssertTrue(receipts.allSatisfy { $0["committed"] as? Bool == true && $0["error"] == nil })
    }

    @MainActor
    func testOnlyCoordinatorRejectionMakesActionSafeToCancel() async throws {
        let setup = CollaborationSetup()
        var receipt: [String: Any] = [:]
        try await setup.flush { hub, route, _, body in
            if route == "/collaboration/owner-queue" {
                return ["commands": [["operation": ["id": "conflict"]]]]
            }
            if route == "/collaboration/owner-attempt" { return ["attempted": true] }
            if hub { throw CollaborationSetup.HTTPFailure(status: 409, message: "Parent moved") }
            if route == "/collaboration/owner-result" { receipt = body }
            return [:]
        }
        XCTAssertEqual(receipt["error"] as? String, "Parent moved")
        XCTAssertNil(receipt["committed"])
    }
    @MainActor
    func testDisconnectRetainsLocalCapabilityUntilRevocationIsConfirmed() async throws {
        let setup = CollaborationSetup()
        var revocations = 0
        var forgotten = false
        let send: (Bool, String, String, [String: Any]) async throws -> [String: Any] = { hub, route, _, body in
            switch route {
            case "/collaboration/status": return hub ? ["workspace_id": "workspace"] : ["enabled": true]
            case "/collaboration/disconnect-prepare": return ["id": "same-review", "computer_id": "computer", "workspace_id": "workspace"]
            case "/collaboration/revoke":
                XCTAssertEqual(body["computer_id"] as? String, "computer")
                revocations += 1
                if revocations == 1 { throw URLError(.networkConnectionLost) }
                return ["revoked": true]
            case "/collaboration/disconnect":
                XCTAssertEqual(body["id"] as? String, "same-review")
                forgotten = true
                return ["disconnected": true]
            default: XCTFail("Unexpected route"); return [:]
            }
        }
        do { _ = try await setup.disconnect(send: send); XCTFail("Expected a lost response") }
        catch { XCTAssertFalse(forgotten) }
        _ = try await setup.disconnect(send: send)
        XCTAssertTrue(forgotten)
        XCTAssertEqual(revocations, 2)
    }

    @MainActor
    func testDisconnectRejectsAnotherCoordinatorWorkspace() async throws {
        let setup = CollaborationSetup()
        var mutations: [String] = []
        do {
            _ = try await setup.disconnect { hub, route, _, _ in
                if route == "/collaboration/status" { return hub ? ["workspace_id": "other"] : ["enabled": true] }
                if route == "/collaboration/disconnect-prepare" { return ["id": "review", "computer_id": "computer", "workspace_id": "original"] }
                mutations.append(route)
                return [:]
            }
            XCTFail("Expected mismatched coordinator refusal")
        } catch { XCTAssertTrue(mutations.isEmpty) }
    }
}
