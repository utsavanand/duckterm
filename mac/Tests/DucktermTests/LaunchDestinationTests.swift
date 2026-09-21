import XCTest
@testable import Duckterm

final class LaunchDestinationTests: XCTestCase {
    let base = URL(string: "http://127.0.0.1:14300")!

    func testDoesNotAcceptArbitraryURLsOrOperations() {
        XCTAssertThrowsError(try LaunchDestination.request(base: URL(string: "https://example.com")!, operation: "browse", params: [:]))
        XCTAssertThrowsError(try LaunchDestination.request(base: base, operation: "../connectors/github/enable", params: [:]))
        XCTAssertThrowsError(try LaunchDestination.request(base: base, operation: "launch", params: ["in_terminal": true]))
        XCTAssertThrowsError(try LaunchDestination.request(base: base, operation: "launch", params: ["session_key": "other-agent"]))
    }

    func testFolderIsQueryDataAndLaunchAlwaysUsesOwnedTerminal() throws {
        let folder = "/home/test/a & b?x=#y"
        let request = try LaunchDestination.request(base: base, operation: "browse", params: ["path": folder])
        let url = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
        XCTAssertEqual(url.path, "/browse")
        XCTAssertEqual(url.queryItems, [URLQueryItem(name: "path", value: folder)])
        let launch = try LaunchDestination.request(base: base, operation: "launch", params: ["command": "codex", "cwd": folder])
        let body = try JSONSerialization.jsonObject(with: launch.httpBody!) as! [String: Any]
        XCTAssertEqual(body["in_terminal"] as? Bool, false)
        XCTAssertNil(launch.value(forHTTPHeaderField: "X-Duckterm-Token"))
        XCTAssertEqual(launch.httpMethod, "POST")
    }
}
