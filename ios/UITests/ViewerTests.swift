import XCTest

/// Uses a disposable host from tests/fixtures/ducky_ui.py, never the user's Mac state.
@available(iOS 16.4, *)
final class ViewerTests: XCTestCase {
    private var control: URL!
    private let app = XCUIApplication()

    override func setUpWithError() throws {
        continueAfterFailure = false
        guard let value = ProcessInfo.processInfo.environment["DUCKY_UI_CONTROL"],
              let url = URL(string: value), url.host == "127.0.0.1" else {
            throw XCTSkip("Start the disposable ducky_ui.py fixture and set DUCKY_UI_CONTROL.")
        }
        control = url
    }

    private func command(_ name: String) async throws -> String {
        let (data, response) = try await URLSession.shared.data(from: control.appendingPathComponent(name))
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        return String(decoding: data, as: UTF8.self)
    }

    @MainActor private func capture(_ name: String) {
        let image = XCTAttachment(screenshot: app.screenshot())
        image.name = name; image.lifetime = .keepAlways
        add(image)
    }

    @MainActor
    func testPairingStatusOfflineRevocationAndForgetting() async throws {
        app.launch()
        app.buttons["Settings"].tap()
        if app.buttons["Forget this Mac"].exists {
            app.buttons["Forget this Mac"].tap()
            app.buttons["Forget now"].tap()
        }
        app.buttons["Agents"].tap()
        XCTAssertTrue(app.staticTexts["Your agents, at a glance"].exists)
        capture("invite")
        let invitation = try await command("invitation")
        app.open(URL(string: invitation)!)
        XCTAssertTrue(app.staticTexts["Approve this iPhone on your Mac"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.staticTexts["discounts"].exists)
        capture("pairing")

        _ = try await command("approve")
        XCTAssertTrue(app.staticTexts["payments-review"].waitForExistence(timeout: 25))
        XCTAssertTrue(app.staticTexts["discounts"].exists)
        XCTAssertTrue(app.staticTexts["Needs you"].exists)
        capture("agents")

        app.buttons["Settings"].tap()
        XCTAssertTrue(app.staticTexts["This pilot cannot alert you in the background yet."].exists ||
            app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "cannot alert you in the background")).firstMatch.exists)
        capture("settings")
        app.buttons["Agents"].tap()

        _ = try await command("notice")
        XCTAssertTrue(app.buttons["Dismiss notification"].waitForExistence(timeout: 25))
        capture("notice")
        app.buttons["Dismiss notification"].tap()

        _ = try await command("offline")
        XCTAssertTrue(app.staticTexts["Showing last known status"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.staticTexts["payments-review"].exists)
        capture("stale")

        _ = try await command("empty")
        _ = try await command("online")
        XCTAssertTrue(app.staticTexts["No running agents"].firstMatch.waitForExistence(timeout: 25))
        capture("empty")

        _ = try await command("restore")
        XCTAssertTrue(app.staticTexts["payments-review"].waitForExistence(timeout: 25))
        app.terminate(); app.launch()
        XCTAssertTrue(app.staticTexts["payments-review"].waitForExistence(timeout: 15), "Keychain pairing should survive a relaunch")

        _ = try await command("revoke")
        XCTAssertTrue(app.staticTexts["Check access on your Mac"].waitForExistence(timeout: 25))
        XCTAssertFalse(app.staticTexts["payments-review"].exists)
        capture("revoked")

        app.buttons["Settings"].tap()
        app.buttons["Forget this Mac"].tap()
        app.buttons["Forget now"].tap()
        app.terminate(); app.launch()
        XCTAssertTrue(app.staticTexts["Your agents, at a glance"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["payments-review"].exists)
        capture("forgotten")
    }
}
