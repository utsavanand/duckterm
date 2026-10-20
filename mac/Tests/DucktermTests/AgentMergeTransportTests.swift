import XCTest
@testable import Duckterm

final class AgentMergeTransportTests: XCTestCase {
    @MainActor func testMergeRoutesAreNarrowAndHostScoped() async throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        for (suffix, method) in [("agent-merge", "GET"), ("agent-merge", "POST"), ("agent-merge/preview", "POST"), ("agent-merges", "GET")] {
            let path = "/sessions/my.session/" + suffix
            let request = try SessionTransport.request(base: base, params: ["path": path, "method": method])
            XCTAssertEqual(request.url?.path, path)
            XCTAssertEqual(request.httpMethod, method)
        }
        for (suffix, method) in [("agent-merge", "DELETE"), ("agent-merge", "PATCH"), ("agent-merge/preview", "GET"), ("agent-merges", "POST"), ("agent-merges/extra", "GET"), ("agent-merge?target=other", "GET")] {
            XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": "/sessions/x/" + suffix, "method": method]))
        }
        XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": "/sessions/../agent-merge", "method": "POST"]))
    }
}
