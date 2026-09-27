import XCTest
@testable import Duckterm

final class SessionTransportTests: XCTestCase {
    @MainActor func testRoutesOnlyConfiguredLoopbackSessionOperations() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        let request = try SessionTransport.request(base: base, params: ["path": "/sessions/my.session/input", "method": "POST", "body": "{\"text\":\"hello\"}"])
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:14300/sessions/my.session/input")
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Duckterm-Token"))
        for path in ["https://example.com/sessions", "//example.com/sessions", "/sessions/../file", "/sessions/%2e%2e/file", "/sessions/x/terminal", "/transfers/clone", "/sessions#fragment", "/sessions\r\nInjected: yes"] {
            XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": "GET"]), path)
        }
        XCTAssertThrowsError(try SessionTransport.request(base: URL(string: "https://example.com")!, params: ["path": "/sessions", "method": "GET"]))
        for path in ["/sessions/launch", "/sessions/compare", "/sessions/clear-terminated"] {
            for method in ["GET", "POST", "PATCH", "DELETE"] {
                XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": method]))
            }
        }
    }

    @MainActor func testQueryStaysDataAndImageBodyIsNotStringified() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        let request = try SessionTransport.request(base: base, params: ["path": "/file?path=%2Fhome%2Fproject%2Fa%20b", "method": "GET"])
        XCTAssertEqual(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "/home/project/a b")
        let image = try SessionTransport.request(base: base, params: ["path": "/paste-image", "method": "POST", "contentType": "image/png", "base64": Data([1, 2, 3]).base64EncodedString()])
        XCTAssertEqual(image.httpBody, Data([1, 2, 3]))
        XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": "/file", "method": "POST", "contentType": "image/png", "base64": "AQID"]))
    }
}
