import XCTest
import Sparkle
@testable import Duckterm

final class AppUpdateTests: XCTestCase {
    private let key = Data(repeating: 1, count: 32).base64EncodedString()
    private func info(_ edition: String = "full") -> [String: Any] {
        ["DucktermEdition": edition, "SUPublicEDKey": key,
         "SUFeedURL": "https://github.com/utsavanand/duckterm/releases/latest/download/appcast-\(edition)-arm64.xml"]
    }
    func testEditionCannotSwitchThroughFeedOrArchive() throws {
        let policy = try AppUpdatePolicy(info: info("code"))
        let base = "https://github.com/utsavanand/duckterm/releases/download/v0.4.210/"
        XCTAssertTrue(policy.accepts(archive: URL(string: base + "DuckTerm-Code-0.4.210-macos-arm64.zip"), version: "0.4.210", informationOnly: false))
        for name in ["DuckTerm-0.4.210-macos-arm64.zip", "DuckTerm-Lite-0.4.210-macos-arm64.zip", "DuckTerm-Code-0.4.210-macos-arm64.zip?key=x"] {
            XCTAssertFalse(policy.accepts(archive: URL(string: base + name), version: "0.4.210", informationOnly: false))
        }
        var changed = info("code"); changed["SUFeedURL"] = info()["SUFeedURL"]
        XCTAssertThrowsError(try AppUpdatePolicy(info: changed))
    }
    func testAllPublishedEditionAssetNames() throws {
        for edition in ["lite", "code", "full"] {
            let policy = try AppUpdatePolicy(info: info(edition))
            let url = URL(string: "https://github.com/utsavanand/duckterm/releases/download/v0.4.210/DuckTerm-\(edition.capitalized)-0.4.210-macos-arm64.zip")!
            XCTAssertTrue(policy.accepts(archive: url, version: "0.4.210", informationOnly: false))
        }
    }
    func testMissingKeyWrongHostAndInformationalUpdatesAreRejected() throws {
        var missing = info(); missing.removeValue(forKey: "SUPublicEDKey")
        XCTAssertThrowsError(try AppUpdatePolicy(info: missing))
        var wrong = info(); wrong["SUFeedURL"] = "https://evil.test/feed.xml"
        XCTAssertThrowsError(try AppUpdatePolicy(info: wrong))
        let policy = try AppUpdatePolicy(info: info())
        XCTAssertFalse(policy.accepts(archive: URL(string: "https://evil.test/update.zip"), version: "0.4.210", informationOnly: false))
        XCTAssertFalse(policy.accepts(archive: nil, version: "0.4.210", informationOnly: true))
    }
    func testTestAppCannotReadProductionFeed() throws {
        var test = info(); test["DucktermTestBuild"] = true
        XCTAssertThrowsError(try AppUpdatePolicy(info: test))
        test["SUFeedURL"] = "http://127.0.0.1:59240/test.xml"
        let policy = try AppUpdatePolicy(info: test)
        XCTAssertTrue(policy.accepts(archive: URL(string: "http://127.0.0.1:59240/test.zip"), version: "0.4.210", informationOnly: false))
        XCTAssertFalse(policy.accepts(archive: URL(string: "http://127.0.0.1:4300/test.zip"), version: "0.4.210", informationOnly: false))
        test["DucktermTestBuild"] = false
        XCTAssertThrowsError(try AppUpdatePolicy(info: test))
    }
    func testBothAppAndBackendMustMatchTarget() {
        XCTAssertTrue(AppUpdatePolicy.verified(target: "0.4.210", app: "0.4.210", backend: "0.4.210"))
        XCTAssertFalse(AppUpdatePolicy.verified(target: "0.4.210", app: "0.4.209", backend: "0.4.210"))
        XCTAssertFalse(AppUpdatePolicy.verified(target: "0.4.210", app: "0.4.210", backend: "0.4.209"))
        XCTAssertFalse(AppUpdatePolicy.verified(target: "0.4.210", app: "0.4.210", backend: nil))
    }
    @MainActor func testCancelInvokesSparkleCancellationAndClearsProgress() async throws {
        let suite = "DuckTermUpdaterTests-\(UUID())"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let updater = AppUpdater(bundle: Bundle(for: Self.self), defaults: defaults)
        var cancelled = false
        updater.showDownloadInitiated { cancelled = true }
        updater.showDownloadDidReceiveExpectedContentLength(100)
        updater.showDownloadDidReceiveData(ofLength: 42)
        XCTAssertEqual(updater.state["progress"] as? Double, 0.42)
        _ = try await updater.perform("cancel")
        XCTAssertTrue(cancelled)
        XCTAssertEqual(updater.state["phase"] as? String, "idle")
        XCTAssertNil(updater.state["progress"])
    }
    @MainActor func testExtractionDisablesCancellationAndDoesNotClaimSuccess() async throws {
        let updater = AppUpdater(bundle: Bundle(for: Self.self))
        updater.showDownloadDidStartExtractingUpdate()
        XCTAssertEqual(updater.state["can_cancel"] as? Bool, false)
        var acknowledged = false
        updater.showUpdateInstalledAndRelaunched(true) { acknowledged = true }
        XCTAssertTrue(acknowledged)
        XCTAssertEqual(updater.state["phase"] as? String, "restarting")
    }
}
