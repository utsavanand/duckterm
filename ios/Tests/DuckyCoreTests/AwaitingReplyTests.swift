import XCTest
@testable import DuckyCore

private func decoded<T: Decodable>(_ value: Any) throws -> T {
    try JSONDecoder().decode(T.self, from: JSONSerialization.data(withJSONObject: value))
}
private func reply(at: Int64) -> ConversationMessage {
    try! decoded(["id": "r\(at)", "role": "assistant", "text": "Done", "at": at])
}
private func delivered(at: Int64) -> MessageReceipt {
    try! decoded(["request_id": "q\(at)", "session_id": "a", "status": "delivered",
                  "created_at": at - 500, "updated_at": at, "text": "Go"])
}

final class AwaitingReplyTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_000)

    func testBusyAgentIsWorkingWhateverTheFeedHolds() {
        XCTAssertTrue(AwaitingReply.pending(state: "busy", messages: [reply(at: 999_000)], receipts: [], now: now))
    }

    func testFreshDeliveryCountsBeforeTheStatusCatchesUp() {
        XCTAssertTrue(AwaitingReply.pending(state: "idle", messages: [reply(at: 900_000)],
                                            receipts: [delivered(at: 995_000)], now: now))
    }

    func testReplyAfterTheDeliveryEndsTheWait() {
        XCTAssertFalse(AwaitingReply.pending(state: "idle", messages: [reply(at: 998_000)],
                                             receipts: [delivered(at: 995_000)], now: now))
    }

    func testOldDeliveryWithoutBusyStatusIsNotWork() {
        XCTAssertFalse(AwaitingReply.pending(state: "idle", messages: [],
                                             receipts: [delivered(at: 970_000)], now: now))
    }

    func testAgentThatNeedsYouIsNotWorking() {
        XCTAssertFalse(AwaitingReply.pending(state: "waiting", messages: [],
                                             receipts: [delivered(at: 999_000)], now: now))
    }
}
