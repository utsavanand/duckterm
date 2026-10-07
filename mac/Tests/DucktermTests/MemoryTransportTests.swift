import XCTest
@testable import Duckterm

final class MemoryTransportTests: XCTestCase {
    @MainActor func testExactMemoryAndRestartRoutes() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        let root = "/sessions/same.id"
        let prepared = root + "/restart-preparation/" + String(repeating: "a", count: 32)
        let routes = [
            (root + "/restart-options", "GET"), (root + "/restart", "GET"),
            (root + "/restart", "POST"), (root + "/restart", "DELETE"),
            (root + "/restart?request_key=owner%3Arequest-1", "GET"),
            (root + "/restart?operation_id=op-1", "DELETE"),
            (root + "/restart-preparation", "POST"), (prepared, "GET"),
            (prepared + "?detail=full", "GET"), (prepared + "?detail=full&cursor=50", "GET"),
            (prepared + "?request_key=dialog-1", "DELETE"),
        ]
        for (path, method) in routes {
            let request = try SessionTransport.request(base: base, params: ["path": path, "method": method])
            XCTAssertEqual(request.url?.absoluteString, base.absoluteString + path)
            XCTAssertNil(request.value(forHTTPHeaderField: "X-Duckterm-Token"))
        }
    }

    @MainActor func testRejectsOtherMethodsQueriesAndPaths() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        let prepared = "/sessions/x/restart-preparation/" + String(repeating: "a", count: 32)
        let bad = [
            ("/sessions/x/restart-options", "POST"), ("/sessions/x/restart-options?x=1", "GET"),
            ("/sessions/x/restart", "PATCH"), ("/sessions/x/restart?request_key=a", "POST"),
            ("/sessions/x/restart?operation_id=a", "GET"), ("/sessions/x/restart?request_key=a", "DELETE"),
            ("/sessions/x/restart?request_key=a&request_key=b", "GET"),
            ("/sessions/x/restart?request_key=%2Fsecret", "GET"),
            ("/sessions/x/restart?request_key=", "GET"),
            ("/sessions/x/restart-preparation", "GET"), ("/sessions/x/restart-preparation?x=1", "POST"),
            (prepared, "POST"), (prepared, "PATCH"), (prepared, "DELETE"),
            (prepared + "?detail=full&cursor=-1", "GET"), (prepared + "?detail=full&cursor=123456789", "GET"),
            (prepared + "?detail=full&cursor=1.2", "GET"), (prepared + "?cursor=0", "GET"),
            (prepared + "?detail=full&path=%2Fetc", "GET"), (prepared + "?detail=full&detail=full", "GET"),
            (prepared + "?detail=brief", "GET"), (prepared + "?operation_id=op1", "DELETE"),
            (prepared + "/extra", "GET"), ("/sessions/x/restart-preparation/%2e%2e", "GET"),
            ("/sessions/../restart", "POST"), ("/sessions/x/memory/read?path=/tmp/test", "GET"),
        ]
        for (path, method) in bad {
            XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": method]), "\(method) \(path)")
        }
    }
}
