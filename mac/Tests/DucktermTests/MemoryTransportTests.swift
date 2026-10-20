import XCTest
@testable import Duckterm

final class MemoryTransportTests: XCTestCase {
    @MainActor func testCheckpointSettingsUseExactHostBoundRoutes() throws {
        for port in [4300, 14300] {
            let base = URL(string: "http://127.0.0.1:\(port)")!
            for method in ["GET", "POST"] {
                let request = try SessionTransport.request(base: base, params: [
                    "path": "/checkpoint-settings", "method": method,
                ])
                XCTAssertEqual(request.url?.absoluteString, base.absoluteString + "/checkpoint-settings")
                XCTAssertEqual(request.httpMethod, method)
            }
            for (path, method) in [
                ("/checkpoint-settings", "DELETE"), ("/checkpoint-settings", "PATCH"),
                ("/checkpoint-settings?x=1", "GET"), ("/checkpoint-settings?x=1", "POST"),
                ("/checkpoint-settings/extra", "GET"),
            ] {
                XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": method]))
            }
        }
    }

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


extension MemoryTransportTests {
    @MainActor func testOwnerMemoryReadsPreserveSelectedHostAndEncodedQueries() throws {
        let handle = String(repeating: "a", count: 32) + "%3A" + String(repeating: "b", count: 64)
        let root = "/sessions/same.id/memory"
        let paths = [
            root,
            root + "?revision=" + String(repeating: "c", count: 32),
            root + "/read?source=" + handle + "&record=constraints%3A0&offset=16&limit=8000",
            root + "/related?source=" + handle + "&record=constraints%3A0&limit=20&cursor=opaque%2B%2F%3D",
        ]
        // Same session key on local and SSH-tunnel bases must stay host-bound.
        for port in [4300, 14300] {
            let base = URL(string: "http://127.0.0.1:\(port)")!
            for path in paths {
                let request = try SessionTransport.request(base: base, params: [
                    "path": path, "method": "GET",
                    "headers": ["X-Duckterm-Token": "untrusted"],
                    "url": "https://untrusted.example",
                ])
                XCTAssertEqual(request.url?.absoluteString, base.absoluteString + path)
                XCTAssertEqual(request.httpMethod, "GET")
                XCTAssertNil(request.value(forHTTPHeaderField: "X-Duckterm-Token"))
                XCTAssertNil(request.httpBody)
            }
        }
    }

    @MainActor func testMemoryReadsRejectMutationsAndUnrelatedQueries() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        for path in ["/sessions/x/memory", "/sessions/x/memory/read", "/sessions/x/memory/related"] {
            for method in ["POST", "PATCH", "DELETE"] {
                XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": method]))
            }
        }
        for path in [
            "/sessions/x/memory/claim", "/sessions/x/memory/search", "/sessions/x/memory/read/extra",
            "/sessions/x/memory?path=/tmp/secret", "/sessions/x/memory/read?url=https://example.com",
            "/sessions/x/memory/read?source=a&source=b", "/sessions/x/memory?revision=a&revision=b",
            "/sessions/x/memory/related?cursor=a&cursor=b", "/sessions/x/memory/related?offset=1",
            "/sessions/x/memory/read?cursor=a", "/sessions/x/memory?source=a",
            "/sessions/x/memory?claims_cursor=a&claims_cursor=b",
            "/sessions/x/memory?revisions_cursor=a&revisions_cursor=b",
            "/sessions/x/memory?checkpoint=a&checkpoint=b",
            "/sessions/x/memory?checkpoint=a&revision=b",
            "/sessions/x/memory?checkpoint=a&claims_cursor=b",
            "/sessions/x/memory?checkpoint=a&claims_limit=25",
            "/sessions/%2e%2e/memory", "/sessions/x/memory/%2e%2e/read",
            "//example.com/sessions/x/memory", "/sessions/x/memory#fragment",
        ] {
            XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": "GET"]), path)
        }
    }

    @MainActor func testCheckpointAndPagedMemoryReadsPreserveSelectedConnection() throws {
        for base in [URL(string: "http://127.0.0.1:14300")!, URL(string: "http://127.0.0.1:15400")!] {
            for path in [
                "/sessions/x/memory?claims_cursor=stamp%3A25&claims_limit=25",
                "/sessions/x/memory?revisions_cursor=stamp%3A20&revisions_limit=20",
                "/sessions/x/memory?revision=exact&revisions_cursor=stamp%3A20&revisions_limit=20",
                "/sessions/x/memory?checkpoint=exact&revisions_cursor=stamp%3A20&revisions_limit=20",
            ] {
                let request = try SessionTransport.request(base: base, params: ["path": path, "method": "GET"])
                XCTAssertEqual(request.url?.absoluteString, base.absoluteString + path)
                XCTAssertEqual(request.httpMethod, "GET")
                XCTAssertNil(request.httpBody)
            }
        }
    }
}
