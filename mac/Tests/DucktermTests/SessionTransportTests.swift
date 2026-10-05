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

extension SessionTransportTests {
    @MainActor func testBugReportsUseNarrowRoutesAndBoundedLargeBodies() throws {
        let base = URL(string: "http://127.0.0.1:14300")!
        let bytes = Data(repeating: 255, count: 5 * 1024 * 1024)
        let attachment: [String: Any] = ["name": "sample.bin", "content_base64": bytes.base64EncodedString()]
        let json = try JSONSerialization.data(withJSONObject: ["summary": "Test", "body": "Exact report", "attachments": [attachment, attachment, attachment]], options: [.withoutEscapingSlashes])
        let params: [String: Any] = ["path": "/bugreport/submit", "method": "POST", "body": String(decoding: json, as: UTF8.self)]
        let request = try SessionTransport.request(base: base, params: params)
        XCTAssertEqual(request.httpBody, json)
        let envelope = try JSONSerialization.data(withJSONObject: params, options: [.withoutEscapingSlashes])
        XCTAssertLessThan(envelope.count, SessionTransport.envelopeLimit(operation: "session-request", params: params))
        XCTAssertEqual(SessionTransport.envelopeLimit(operation: "session-request", params: ["path": "/file", "method": "POST"]), 14 * 1024 * 1024)
        XCTAssertEqual(SessionTransport.envelopeLimit(operation: "launch", params: params), 131072)
        XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": "/file", "method": "POST", "body": params["body"]!]))
        XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": "/bugreport/submit", "method": "POST", "body": String(repeating: "x", count: 21 * 1024 * 1024 + 1)]))
        for (path, method) in [("/bugreport/context", "POST"), ("/bugreport/submit", "GET"), ("/bugreport/submit?extra=true", "POST"), ("/bugreport/bundles/../../secret", "GET"), ("/bugreport/bundles/" + String(repeating: "a", count: 32), "DELETE")] {
            XCTAssertThrowsError(try SessionTransport.request(base: base, params: ["path": path, "method": method]))
        }
        let context = try SessionTransport.request(base: base, params: ["path": "/bugreport/context?session_key=remote-session", "method": "GET"])
        XCTAssertEqual(context.url?.query, "session_key=remote-session")
    }

    @MainActor func testReportBundleKeepsBinaryBytesAndErrorsStayJSON() throws {
        let path = "/bugreport/bundles/" + String(repeating: "b", count: 32)
        let url = URL(string: "http://127.0.0.1:14300" + path)!
        let bytes = Data([0x50, 0x4b, 0, 255, 128, 192, 254, 1])
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!
        let result = SessionTransport.responsePayload(data: bytes, response: response, path: path)
        XCTAssertEqual(Data(base64Encoded: result["base64"] as! String), bytes)
        XCTAssertEqual(result["contentType"] as? String, "application/zip")
        let error = HTTPURLResponse(url: url, statusCode: 404, httpVersion: nil, headerFields: nil)!
        let failed = SessionTransport.responsePayload(data: Data("{\"error\":\"missing\"}".utf8), response: error, path: path)
        XCTAssertEqual(failed["body"] as? String, "{\"error\":\"missing\"}")
    }
}
