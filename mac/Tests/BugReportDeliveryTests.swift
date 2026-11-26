import Foundation

@main struct BugReportDeliveryTests {
    static func check(_ value: @autoclosure () -> Bool, _ message: String) {
        guard value() else { fatalError(message) }
    }
    static func main() async throws {
        let archive = Data([80, 75, 3, 4, 0, 255, 128])
        let report = BugReportSubmission(id: String(repeating: "a", count: 32), summary: "Frozen report 🦆", body: "Only selected details", archive: archive)
        let multipart = try report.multipart(boundary: "fixture")
        check(multipart.range(of: archive) != nil, "Binary ZIP must survive encoding")
        var requests: [URLRequest] = []
        let client = BugReportDelivery { request in
            requests.append(request)
            if requests.count == 1 { throw URLError(.cannotFindHost) }
            return (Data("{\"ok\":true,\"id\":42}".utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        }
        let result = await client.send(report)
        check(result == .filed(42), "Network fallback must succeed")
        check(requests.count == 2 && requests[0].url != requests[1].url, "Fallback host")
        check(requests[0].httpBody == requests[1].httpBody, "Fallback must use identical bytes")
        check(requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == nil }, "No token in app")
        for status in [400, 409, 413, 429, 502, 503] {
            var calls = 0
            let rejected = BugReportDelivery { request in
                calls += 1
                return (Data("{\"error\":\"secret upstream detail\"}".utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
            }
            let result = await rejected.send(report)
            check(calls == 1, "A service refusal must not trigger fallback")
            check(!result.message.contains("secret"), "Never expose raw upstream errors")
            check(result != .filed(42), "Never claim success on failure")
        }
        for raw in ["[]", "{}", "{\"ok\":1,\"id\":42}", "{\"ok\":true,\"id\":true}", "{\"ok\":true,\"id\":1.5}", "{\"ok\":true,\"id\":-1}"] {
            let malformed = BugReportDelivery { request in
                (Data(raw.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
            let result = await malformed.send(report)
            check(result == .unconfirmed, "Malformed success must remain unconfirmed")
        }
        let oversized = BugReportDelivery { _ in fatalError("Oversized report must never be transmitted") }
        let large = BugReportSubmission(summary: "Large", body: "Report", archive: Data(count: BugReportDelivery.maxUploadBytes))
        let tooLarge = await oversized.send(large)
        check(tooLarge.message.contains("too large"), "Keep export fallback for oversized reports")
        print("Native report delivery: fallback, byte preservation, refusals, receipts and size checks passed")
    }
}
