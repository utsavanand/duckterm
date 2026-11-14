import XCTest
import UIKit

/// DEBUG-only synthetic states; never connects to a Mac or accesses real credentials.
@available(iOS 16.4, *)
final class ConnectionStatusUITests: XCTestCase {
    private let app = XCUIApplication()
    override func setUpWithError() throws { continueAfterFailure = false; XCUIDevice.shared.orientation = .portrait }
    override func tearDownWithError() throws { app.terminate(); XCUIDevice.shared.orientation = .portrait }
    @MainActor private func element(_ id: String) -> XCUIElement { app.descendants(matching: .any).matching(identifier: id).firstMatch }
    @MainActor private func capture(_ name: String) {
        let item = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        item.name = name; item.lifetime = .keepAlways; add(item)
    }
    @MainActor private func open(_ mode: String, expectsReceipt: Bool = true) {
        app.launchArguments = ["--ducky-status-ui-fixture", "--ducky-status-mode=" + mode]
        app.launch()
        let folder = app.buttons["folder:Status fixture"]
        XCTAssertTrue(folder.waitForExistence(timeout: 10))
        if mode.hasPrefix("no-cache-offline") {
            XCTAssertTrue(app.staticTexts["Showing last known status"].waitForExistence(timeout: 5))
        }
        let session = app.buttons["session:status-ui-agent"]
        if !session.exists { folder.tap() }
        XCTAssertTrue(session.waitForExistence(timeout: 5)); session.tap()
        XCTAssertTrue(element("conversation-header").waitForExistence(timeout: 5))
        if expectsReceipt {
            XCTAssertTrue(element("conversation-receipt:33333333-3333-4333-8333-333333333333").waitForExistence(timeout: 5))
        }
    }
    @MainActor private func rotate(_ orientation: UIDeviceOrientation) {
        XCUIDevice.shared.orientation = orientation
        let landscape = orientation == .landscapeLeft || orientation == .landscapeRight
        expectation(for: NSPredicate { _, _ in (self.app.frame.width > self.app.frame.height) == landscape }, evaluatedWith: nil)
        waitForExpectations(timeout: 8)
    }
    @MainActor private func openEmbeddedConversation() {
        app.buttons["conversation-back"].tap()
        element("show-map").tap(); rotate(.landscapeLeft)
        app.buttons["Show as list"].tap()
        let folder = app.buttons["constellation-row:folder:Status fixture"]
        XCTAssertTrue(folder.waitForExistence(timeout: 5)); folder.tap()
        let agent = app.buttons["constellation-row:session:status-ui-agent"]
        XCTAssertTrue(agent.waitForExistence(timeout: 5)); agent.tap()
        XCTAssertTrue(app.buttons["conversation-close"].waitForExistence(timeout: 5))
        app.buttons["Show as map"].tap()
    }
    @MainActor func testOfflineWithoutCachedConversationNeverShowsLoading() {
        open("no-cache-offline", expectsReceipt: false)
        XCTAssertTrue(element("conversation-unavailable").waitForExistence(timeout: 5))
        XCTAssertFalse(element("conversation-loading").exists)
        XCTAssertFalse(element("conversation-message:status-reply").exists)
        XCTAssertTrue(element("conversation-connection").label.contains("Can’t reach your Mac"))
        XCTAssertTrue(app.buttons["conversation-retry"].isHittable)
        capture("no-cache-offline-portrait")
        openEmbeddedConversation()
        XCTAssertTrue(element("conversation-unavailable").waitForExistence(timeout: 5))
        XCTAssertFalse(element("conversation-loading").exists)
        XCTAssertTrue(app.buttons["conversation-retry"].isHittable)
        capture("no-cache-offline-landscape-left")
        rotate(.landscapeRight)
        XCTAssertFalse(element("conversation-loading").exists)
        XCTAssertTrue(app.buttons["conversation-retry"].isHittable)
        capture("no-cache-offline-landscape-right")
    }
    @MainActor func testOfflineWithLastKnownUnreadableSessionIsUnavailable() {
        open("no-cache-offline-unreadable", expectsReceipt: false)
        XCTAssertTrue(element("conversation-unavailable").waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["No conversation yet"].exists)
        XCTAssertFalse(element("conversation-loading").exists)
        capture("no-cache-offline-unreadable")
    }
    @MainActor func testFailedFirstReplyReadIsUnavailableWithoutAnIdleSpinner() {
        open("no-cache-read-failure", expectsReceipt: false)
        XCTAssertTrue(element("conversation-refresh-error").waitForExistence(timeout: 5))
        XCTAssertTrue(element("conversation-unavailable").exists)
        XCTAssertFalse(element("conversation-loading").exists)
        XCTAssertTrue(element("conversation-connection").label.contains("Status is current"))
        capture("no-cache-reply-failure")
    }
    @MainActor func testDeliveredReceiptsRequireTheirOriginalMessage() {
        open("orphan-delivered")
        XCTAssertTrue(app.staticTexts["Last seen working"].waitForExistence(timeout: 5))
        let primary = "33333333-3333-4333-8333-333333333333"
        XCTAssertTrue(element("conversation-receipt-message:" + primary).exists)
        XCTAssertTrue(element("conversation-receipt:" + primary).label.contains("Delivered to agent"))
        for id in ["44444444-4444-4444-8444-444444444444", "55555555-5555-4555-8555-555555555555"] {
            XCTAssertFalse(element("conversation-receipt:" + id).exists)
        }
        XCTAssertEqual(app.staticTexts.matching(NSPredicate(format: "identifier BEGINSWITH %@", "conversation-receipt:")).count, 1)
        XCTAssertTrue(element("conversation-message:status-reply").exists)
        capture("delivered-with-body-no-orphan-labels")
    }
    @MainActor private func focusComposer() {
        let field = element("conversation-composer")
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: field)
        waitForExpectations(timeout: 5)
        field.tap(); XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    }
    @MainActor func testWorkingQueueAndCompactKeyboardInPortraitAndEmbeddedLandscape() {
        open("waiting")
        XCTAssertEqual(element("conversation-agent-activity").label, "Working")
        XCTAssertEqual(element("conversation-receipt:33333333-3333-4333-8333-333333333333").label,
                       "On your Mac · waiting for this turn to finish")
        capture("status-working-portrait")
        focusComposer(); element("conversation-composer").typeText("Please keep this draft\nacross rotation")
        XCTAssertTrue(app.buttons["conversation-send"].isHittable)
        XCTAssertLessThanOrEqual(app.buttons["conversation-send"].frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        capture("status-working-portrait-keyboard")
        app.buttons["conversation-back"].tap()
        element("show-map").tap(); rotate(.landscapeLeft)
        // Use the existing dense/list fallback for deterministic node hit testing;
        // return to the map before checking the embedded conversation and keyboard.
        app.buttons["Show as list"].tap()
        let folder = app.buttons["constellation-row:folder:Status fixture"]
        XCTAssertTrue(folder.waitForExistence(timeout: 5)); folder.tap()
        let agent = app.buttons["constellation-row:session:status-ui-agent"]
        XCTAssertTrue(agent.waitForExistence(timeout: 5)); agent.tap()
        XCTAssertTrue(app.buttons["conversation-close"].waitForExistence(timeout: 5))
        app.buttons["Show as map"].tap()
        XCTAssertEqual(element("conversation-composer").value as? String, "Please keep this draft\nacross rotation")
        focusComposer()
        XCTAssertLessThanOrEqual(app.buttons["conversation-send"].frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        capture("status-working-landscape-left-keyboard")
        rotate(.landscapeRight)
        XCTAssertTrue(app.buttons["conversation-close"].isHittable)
        XCTAssertTrue(app.buttons["conversation-send"].isHittable)
        capture("status-working-landscape-right-keyboard")
        app.buttons["conversation-send"].tap()
        XCTAssertTrue(["", "Message agent…"].contains(element("conversation-composer").value as? String ?? "missing"))
        element("conversation-composer").typeText("Next draft")
        XCTAssertEqual(element("conversation-composer").value as? String, "Next draft")
    }
    @MainActor func testConnectionFailureShowsLastConfirmedAndRetryNearHeader() {
        open("failed")
        XCTAssertTrue(app.staticTexts["Last seen working"].waitForExistence(timeout: 5))
        let detail = element("conversation-refresh-error")
        XCTAssertEqual(detail.label, "Can’t reach your Mac. It may be asleep or on a different Wi-Fi."); XCTAssertTrue(detail.isHittable)
        XCTAssertTrue(element("conversation-connection").label.contains("updated"))
        XCTAssertTrue(element("conversation-receipt:33333333-3333-4333-8333-333333333333").label.hasPrefix("Last confirmed:"))
        XCTAssertFalse(element("conversation-composer").isEnabled)
        XCTAssertFalse(app.buttons["conversation-send"].isEnabled)
        XCTAssertEqual(element("conversation-status").label, "Reconnect to send another message.")
        XCTAssertTrue(app.buttons["conversation-retry"].isHittable)
        capture("status-connection-failed")
        app.buttons["conversation-retry"].tap()
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: app.buttons["conversation-retry"])
        waitForExpectations(timeout: 5)
        XCTAssertTrue(detail.isHittable)
    }
    @MainActor func testReplyFailureKeepsWorkingStateAndShowsErrorAboveHistory() {
        open("reading")
        XCTAssertTrue(element("conversation-refresh-error").waitForExistence(timeout: 5))
        XCTAssertEqual(element("conversation-agent-activity").label, "Working")
        XCTAssertTrue(element("conversation-connection").label.contains("Status is current"))
        XCTAssertEqual(element("conversation-refresh-error").label, "Connection timed out.")
        XCTAssertTrue(element("conversation-refresh-error").isHittable)
        XCTAssertTrue(element("conversation-composer").isEnabled)
        capture("status-reply-refresh-failed")
    }
    @MainActor func testServerConfirmedDeliveredAndExpiredReceipts() {
        open("delivered")
        XCTAssertEqual(element("conversation-agent-activity").label, "Idle")
        XCTAssertTrue(element("conversation-receipt:33333333-3333-4333-8333-333333333333").label.hasPrefix("Delivered to agent · "))
        capture("status-delivered")
        app.terminate(); open("expired")
        XCTAssertEqual(element("conversation-receipt:33333333-3333-4333-8333-333333333333").label, "Not sent · expired while waiting")
        capture("status-expired")
    }
    @MainActor func testReconnectingShowsProgressAndLastConfirmedReceipt() {
        open("reconnecting")
        XCTAssertTrue(element("conversation-connection").waitForExistence(timeout: 5))
        XCTAssertTrue(element("conversation-connection").label.contains("Reconnecting to Mac"))
        XCTAssertEqual(element("conversation-agent-activity").label, "Last seen working")
        capture("status-reconnecting")
    }
}

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
        let checkout = app.buttons["folder:Checkout"]
        XCTAssertTrue(checkout.waitForExistence(timeout: 25))
        XCTAssertFalse(app.staticTexts["payments-review"].exists, "Folders begin collapsed")
        capture("collapsed")
        checkout.tap()
        XCTAssertTrue(app.staticTexts["payments-review"].waitForExistence(timeout: 5))
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
        XCTAssertTrue(app.staticTexts["No sessions"].firstMatch.waitForExistence(timeout: 25))
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

/// DEBUG-only in-memory fixture. These tests never pair with a Mac or read Keychain.
@available(iOS 16.4, *)
final class ChatPolishTests: XCTestCase {
    private let app = XCUIApplication()

    override func setUpWithError() throws {
        continueAfterFailure = false
        app.launchArguments = ["--ducky-chat-ui-fixture"]
    }

    override func tearDownWithError() throws {
        app.terminate()
    }

    @MainActor private func element(_ identifier: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    @MainActor private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    @MainActor private func launchConversation() {
        app.launch()
        let folder = app.buttons["folder:UI Test"]
        XCTAssertTrue(folder.waitForExistence(timeout: 10))
        XCTAssertTrue(element("global-header").exists, "The list retains its global header")
        XCTAssertTrue(element("global-tabs").exists, "The list retains its navigation")
        let session = app.buttons["session:chat-ui-test"]
        if !session.exists { folder.tap() }
        XCTAssertTrue(session.waitForExistence(timeout: 5))
        session.tap()
        XCTAssertTrue(element("conversation-header").waitForExistence(timeout: 5))
        XCTAssertTrue(element("conversation-composer").waitForExistence(timeout: 5))
    }

    @MainActor private func isVisible(_ item: XCUIElement, in viewport: XCUIElement) -> Bool {
        guard item.exists, viewport.exists else { return false }
        let overlap = item.frame.intersection(viewport.frame)
        return !overlap.isNull && overlap.height >= min(60, item.frame.height)
    }

    @MainActor private func showFormattedReply() {
        let scroll = app.scrollViews["conversation-scroll"]
        XCTAssertTrue(scroll.waitForExistence(timeout: 5))
        let reply = element("conversation-message:formatted-reply")
        for _ in 0..<18 {
            if isVisible(reply, in: scroll) { return }
            scroll.swipeUp(velocity: .fast)
        }
        XCTAssertTrue(isVisible(reply, in: scroll), "The fixture's final reply must be reachable")
    }

    @MainActor
    func testConversationUsesCompactNavigationAndReturnsToTheList() {
        launchConversation()
        XCTAssertFalse(element("global-header").exists)
        XCTAssertFalse(element("global-tabs").exists)
        XCTAssertFalse(app.buttons["Settings"].exists)
        XCTAssertTrue(app.buttons["conversation-back"].isHittable)
        capture("chat-compact-navigation")

        app.buttons["conversation-back"].tap()
        XCTAssertTrue(app.buttons["session:chat-ui-test"].waitForExistence(timeout: 5))
        XCTAssertTrue(element("global-header").exists)
        XCTAssertTrue(element("global-tabs").exists)
    }

    @MainActor
    func testMarkdownHeadingsInlineFormattingAndCodeRenderAsContent() {
        launchConversation()
        showFormattedReply()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@", "message-heading:formatted-reply:")).firstMatch.exists)
        XCTAssertTrue(app.staticTexts["Shipping checklist"].exists)
        let paragraph = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Checkpoint ready")).firstMatch
        XCTAssertTrue(paragraph.exists)
        XCTAssertFalse(paragraph.label.contains("**"))
        XCTAssertFalse(paragraph.label.contains("`duckterm status`"))
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "Verify folder isolation")).firstMatch.exists)
        let code = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "let retryLimit = 3")).firstMatch
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@", "message-code:formatted-reply:")).firstMatch.exists)
        XCTAssertTrue(code.exists)
        XCTAssertTrue(code.label.contains("print(\"ready <agent>\")"), "Code preserves literal punctuation")
        XCTAssertFalse(code.label.contains("```"), "Fence syntax is rendered as a code block")
        capture("chat-markdown-and-code")
    }

    @MainActor
    func testUntrustedReplyURLsHaveNoLinkActionsAndNestingStaysReadable() {
        app.launchArguments.append("--ducky-chat-untrusted-reply-fixture")
        launchConversation()
        let scroll = app.scrollViews["conversation-scroll"]
        // Never activate a destination, even when this regression fails.
        XCTAssertEqual(scroll.links.count, 0)
        for label in ["trusted.example", "https://untrusted.example/autolink",
                      "https://untrusted.example/bare", "www.untrusted.example/plain", "Pair"] {
            XCTAssertTrue(scroll.staticTexts[label].exists, label)
        }
        let nested = scroll.staticTexts["Deeply nested fixture text remains readable."]
        XCTAssertTrue(nested.exists)
        XCTAssertGreaterThan(nested.frame.width, app.frame.width * 0.35)
        XCTAssertGreaterThanOrEqual(nested.frame.minX, scroll.frame.minX)
        XCTAssertLessThanOrEqual(nested.frame.maxX, scroll.frame.maxX + 1)
        capture("chat-untrusted-links-and-nesting")
    }

    @MainActor
    func testOversizedReplyUsesPlainTextAndNavigationRemainsAvailable() {
        app.launchArguments.append("--ducky-chat-large-reply-fixture")
        launchConversation()
        let reply = app.staticTexts.containing(NSPredicate(
            format: "label BEGINSWITH %@", "# Oversized **unformatted**")).firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any).matching(NSPredicate(
            format: "identifier BEGINSWITH %@", "message-heading:large-reply:")).firstMatch.exists)
        XCTAssertEqual(app.scrollViews["conversation-scroll"].links.count, 0)
        XCTAssertTrue(app.buttons["conversation-back"].isHittable)
        app.buttons["conversation-back"].tap()
        XCTAssertTrue(app.buttons["session:chat-ui-test"].waitForExistence(timeout: 5))
    }

    @MainActor
    func testComposerKeepsNewlinesAndLocalCommandsCannotBeSent() {
        launchConversation()
        let composer = element("conversation-composer")
        let send = app.buttons["conversation-send"]
        XCTAssertFalse(send.isEnabled)
        composer.tap()
        composer.typeText("First line\nSecond line")
        XCTAssertEqual(composer.value as? String, "First line\nSecond line")
        XCTAssertTrue(send.isEnabled)
        XCTAssertFalse(app.staticTexts["On your Mac · waiting to send"].exists, "Return must not submit a message")
        capture("chat-multiline-composer")

        composer.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "First line\nSecond line".count))
        composer.typeText("/clear")
        XCTAssertEqual(composer.value as? String, "/clear")
        XCTAssertFalse(send.isEnabled, "Compact UI must preserve the existing local-command guard")
        XCTAssertFalse(app.staticTexts["On your Mac · waiting to send"].exists)
    }

    @MainActor
    func testDelayedQueuedReceiptKeepsComposerAndKeyboardAvailable() {
        launchConversation()
        let composer = element("conversation-composer")
        composer.tap()
        composer.typeText("Queue this fixture message\nwith its second line")
        let keyboardWasVisible = app.keyboards.firstMatch.exists
        app.buttons["conversation-send"].tap()
        let queued = app.staticTexts["On your Mac · waiting to send"]
        XCTAssertTrue(queued.waitForExistence(timeout: 10))
        XCTAssertTrue(isVisible(queued, in: app.scrollViews["conversation-scroll"]),
            "A delayed receipt must remain visible above the composer")
        XCTAssertTrue(composer.isHittable)
        XCTAssertFalse(app.buttons["conversation-send"].isEnabled)
        XCTAssertEqual(app.keyboards.firstMatch.exists, keyboardWasVisible)
        composer.typeText("Next unsent draft")
        XCTAssertEqual(composer.value as? String, "Next unsent draft",
            "Sending must clear the accepted draft and retain input focus")
        capture("chat-queued-with-live-composer")
    }

    /// Run with the dedicated simulator's content_size set to accessibility-large.
    @MainActor
    func testLargerTextKeepsNavigationAndMultilineComposerUsable() {
        launchConversation()
        showFormattedReply()
        let back = app.buttons["conversation-back"]
        let scroll = app.scrollViews["conversation-scroll"]
        let composer = element("conversation-composer")
        let send = app.buttons["conversation-send"]
        XCTAssertTrue(back.isHittable)
        XCTAssertGreaterThan(scroll.frame.height, 100)
        // The header's decorative edge fade intentionally extends into the feed.
        XCTAssertGreaterThanOrEqual(scroll.frame.minY, back.frame.maxY - 1)
        XCTAssertGreaterThanOrEqual(scroll.frame.minX, app.frame.minX)
        XCTAssertLessThanOrEqual(scroll.frame.maxX, app.frame.maxX)
        capture("chat-larger-text-reading")

        composer.tap()
        composer.typeText("First large-text line\nSecond large-text line\nThird line")
        XCTAssertEqual(composer.value as? String, "First large-text line\nSecond large-text line\nThird line")
        XCTAssertTrue(send.isEnabled)
        XCTAssertTrue(send.isHittable)
        XCTAssertTrue(back.isHittable)
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.exists)
        XCTAssertLessThanOrEqual(composer.frame.maxY, keyboard.frame.minY + 1)
        XCTAssertLessThanOrEqual(send.frame.maxY, keyboard.frame.minY + 1)
        XCTAssertGreaterThanOrEqual(composer.frame.minX, app.frame.minX)
        XCTAssertLessThanOrEqual(send.frame.maxX, app.frame.maxX)
        capture("chat-larger-text-multiline-keyboard")

        send.tap()
        let queued = app.staticTexts["On your Mac · waiting to send"]
        XCTAssertTrue(queued.waitForExistence(timeout: 10))
        XCTAssertTrue(isVisible(queued, in: scroll))
        composer.typeText("Still editable")
        XCTAssertEqual(composer.value as? String, "Still editable")
        XCTAssertTrue(back.isHittable)
        capture("chat-larger-text-queued")
        back.tap()
        XCTAssertTrue(app.buttons["session:chat-ui-test"].waitForExistence(timeout: 5))
    }

    @MainActor
    func testBackAndReopenPreserveUnsentDraftAndReadingPosition() {
        launchConversation()
        let composer = element("conversation-composer")
        let draft = "Keep this draft\nand its second line"
        composer.tap()
        composer.typeText(draft)
        app.buttons["conversation-back"].tap()
        app.buttons["session:chat-ui-test"].tap()
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.value as? String, draft)

        showFormattedReply()
        let scroll = app.scrollViews["conversation-scroll"]
        let checkpoint = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "Reading marker 6.")).firstMatch
        for _ in 0..<32 {
            if isVisible(checkpoint, in: scroll) { break }
            scroll.swipeDown(velocity: .slow)
        }
        XCTAssertTrue(isVisible(checkpoint, in: scroll), "Read midway through an older long reply before navigating away")
        let offset = checkpoint.frame.minY - scroll.frame.minY
        XCTAssertFalse(isVisible(element("conversation-message:formatted-reply"), in: scroll))
        capture("chat-reading-before-back")

        app.buttons["conversation-back"].tap()
        XCTAssertTrue(app.buttons["session:chat-ui-test"].waitForExistence(timeout: 5))
        app.buttons["session:chat-ui-test"].tap()
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: checkpoint)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(composer.value as? String, draft)
        XCTAssertTrue(isVisible(checkpoint, in: scroll))
        XCTAssertLessThanOrEqual(abs(checkpoint.frame.minY - scroll.frame.minY - offset), 120,
            "Reopening must retain the position within a long reply, not merely its message ID")
        XCTAssertFalse(isVisible(element("conversation-message:formatted-reply"), in: scroll))
        capture("chat-reading-after-reopen")
    }
}

/// Forty-eight synthetic sessions, in-memory credentials and transport, no Mac connection.
@available(iOS 16.4, *)
final class ConstellationUITests: XCTestCase {
    private let app = XCUIApplication()
    override func setUpWithError() throws {
        continueAfterFailure = false
        app.launchArguments = ["--ducky-map-ui-fixture"]
        XCUIDevice.shared.orientation = .portrait
    }
    override func tearDownWithError() throws {
        app.terminate()
        XCUIDevice.shared.orientation = .portrait
    }
    @MainActor private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: id).firstMatch
    }
    @MainActor private func node(_ name: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", name + ",")).firstMatch
    }
    @MainActor private func capture(_ name: String) {
        // App-only screenshots are portrait-cropped after rotation on iOS 26.
        let item = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        item.name = name; item.lifetime = .keepAlways; add(item)
    }
    @MainActor private func rotate(_ orientation: UIDeviceOrientation) {
        XCUIDevice.shared.orientation = orientation
        let landscape = orientation == .landscapeLeft || orientation == .landscapeRight
        let predicate = NSPredicate { _, _ in (self.app.frame.width > self.app.frame.height) == landscape }
        expectation(for: predicate, evaluatedWith: nil)
        waitForExpectations(timeout: 8)
    }
    @MainActor private func focusComposer() {
        let composer = element("conversation-composer")
        var previous = CGRect.null
        var stableSince = Date()
        // Hit-test the settled panel, not coordinates from its insertion animation.
        expectation(for: NSPredicate { _, _ in
            guard composer.exists, composer.isHittable else { return false }
            let frame = composer.frame
            if frame != previous { previous = frame; stableSince = Date(); return false }
            return Date().timeIntervalSince(stableSince) > 0.4
        }, evaluatedWith: nil)
        waitForExpectations(timeout: 5)
        composer.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    }
    @MainActor private func openMap() {
        app.launch()
        XCTAssertTrue(element("show-map").waitForExistence(timeout: 10))
        XCTAssertGreaterThanOrEqual(element("show-map").frame.width, 44 - 0.01)
        XCTAssertGreaterThanOrEqual(element("show-map").frame.height, 44 - 0.01)
        element("show-map").tap()
        XCTAssertTrue(app.staticTexts["Turn your phone sideways to see the map"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Show list"].isHittable)
        capture("map-portrait-guide")
        rotate(.landscapeLeft)
        XCTAssertTrue(node("DuckTerm").waitForExistence(timeout: 5))
    }
    @MainActor private func tapNode(_ name: String) {
        let item = node(name)
        XCTAssertTrue(item.waitForExistence(timeout: 5), "Missing node: \(name)")
        var previous = CGRect.null
        var stableSince = Date()
        expectation(for: NSPredicate { _, _ in
            guard item.exists, item.isHittable else { return false }
            let frame = item.frame
            if frame != previous { previous = frame; stableSince = Date(); return false }
            return Date().timeIntervalSince(stableSince) > 0.4
        }, evaluatedWith: nil)
        waitForExpectations(timeout: 5)
        item.tap()
    }
    @MainActor private func allFolders() {
        app.buttons["All folders"].tap()
        XCTAssertTrue(node("DuckTerm").waitForExistence(timeout: 5))
    }
    @MainActor private func openPrimaryAgent() {
        tapNode("DuckTerm"); tapNode("Features"); tapNode("Pairing"); tapNode("Pairing engineer")
        XCTAssertTrue(element("conversation-composer").waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["conversation-close"].isHittable)
        XCTAssertFalse(app.buttons["conversation-back"].exists, "Embedded chat must not replace the map with list navigation")
    }

    @MainActor
    func testQAPinchEntersFolderAndReturnsToParent() {
        openMap()
        node("DuckTerm").pinch(withScale: 2.0, velocity: 1.0)
        XCTAssertTrue(node("Features").waitForExistence(timeout: 5))
        capture("qa-pinch-enter-folder")
        node("Features").pinch(withScale: 0.5, velocity: -1.0)
        XCTAssertTrue(node("Research").waitForExistence(timeout: 5))
        capture("qa-pinch-parent")
    }

    @MainActor
    func testFoldersNestedEmptyUngroupedLongNamesAndDenseListStayReachable() {
        openMap()
        for name in ["DuckTerm", "Design", "Research", "Personal", "Dense", "Empty", "Ungrouped"] {
            let item = node(name)
            XCTAssertTrue(item.exists)
            XCTAssertGreaterThanOrEqual(item.frame.minX, app.frame.minX - 1)
            XCTAssertLessThanOrEqual(item.frame.maxX, app.frame.maxX + 1)
        }
        capture("map-all-folders")
        app.buttons["constellation-mode-oracle"].tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "All folders on this Mac")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(element("conversation-composer").exists, "The status overview does not imply question access")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS %@", "Folders", "13")).firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS %@", "Empty folders", "2")).firstMatch.exists)
        capture("map-passive-oracle-overview")
        app.buttons["constellation-mode-map"].tap()
        tapNode("Empty")
        XCTAssertTrue(app.staticTexts["No sessions yet"].waitForExistence(timeout: 5))
        capture("map-empty-folder")
        allFolders()
        tapNode("Ungrouped")
        XCTAssertTrue(node("Agent 21").waitForExistence(timeout: 5))
        allFolders()
        tapNode("DuckTerm"); tapNode("Features"); tapNode("Pairing")
        XCTAssertTrue(node("Pairing engineer").waitForExistence(timeout: 5))
        capture("map-nested-folder")
        allFolders()
        tapNode("Research")
        tapNode("An exceptionally long folder name for viewport verification")
        XCTAssertTrue(node("A deliberately long agent name for layout verification").waitForExistence(timeout: 5))
        capture("map-long-names")
        allFolders()
        tapNode("Dense")
        app.buttons["Show as list"].tap()
        let last = node("Agent 47")
        let list = app.scrollViews.firstMatch
        for _ in 0..<15 {
            if last.isHittable { break }
            list.swipeUp(velocity: .fast)
        }
        XCTAssertTrue(last.isHittable, "Every dense-folder member must remain reachable through the list")
        capture("map-dense-list")
        rotate(.landscapeRight)
        XCTAssertTrue(app.buttons["Show as map"].isHittable)
        app.buttons["Show as map"].tap()
        capture("map-landscape-right")
    }

    @MainActor
    func testEmbeddedDraftSurvivesSwitchAndRotationThenQueuesAboveKeyboard() {
        openMap(); openPrimaryAgent()
        capture("map-agent-conversation")
        let composer = element("conversation-composer")
        focusComposer(); composer.typeText("Keep my first draft\nand its second line")
        XCTAssertTrue(app.buttons["conversation-send"].isHittable)
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.exists)
        XCTAssertLessThanOrEqual(composer.frame.maxY, keyboard.frame.minY + 1)
        XCTAssertLessThanOrEqual(app.buttons["conversation-send"].frame.maxY, keyboard.frame.minY + 1)
        capture("map-typing")
        app.buttons["conversation-close"].tap()
        tapNode("Mobile reviewer")
        XCTAssertNotEqual(composer.value as? String, "Keep my first draft\nand its second line")
        focusComposer(); composer.typeText("A separate second-agent draft")
        app.buttons["conversation-close"].tap()
        tapNode("Pairing engineer")
        XCTAssertEqual(composer.value as? String, "Keep my first draft\nand its second line")
        rotate(.portrait)
        XCTAssertTrue(app.buttons["Show list"].waitForExistence(timeout: 5))
        rotate(.landscapeRight)
        XCTAssertTrue(composer.waitForExistence(timeout: 5))
        XCTAssertEqual(composer.value as? String, "Keep my first draft\nand its second line")
        focusComposer()
        XCTAssertTrue(app.buttons["conversation-send"].isHittable)
        app.buttons["conversation-send"].tap()
        XCTAssertTrue(app.staticTexts["On your Mac · waiting to send"].waitForExistence(timeout: 10))
        composer.typeText("Next draft")
        XCTAssertEqual(composer.value as? String, "Next draft")
        capture("map-queued-keyboard")
        app.buttons["conversation-close"].tap()
        XCTAssertTrue(node("Pairing engineer").waitForExistence(timeout: 5))
        XCTAssertFalse(element("global-tabs").exists)
        rotate(.portrait)
        app.buttons["Show list"].tap()
        XCTAssertTrue(element("global-header").waitForExistence(timeout: 5))
        XCTAssertTrue(element("global-tabs").exists)
    }

    @MainActor
    func testStaleMapKeepsLastKnownDataButDisablesSending() {
        app.launchArguments.append("--ducky-map-ui-stale")
        openMap(); openPrimaryAgent()
        let composer = element("conversation-composer")
        focusComposer(); composer.typeText("An unsent stale draft")
        XCTAssertTrue(app.staticTexts["Last known"].waitForExistence(timeout: 30))
        XCTAssertFalse(app.buttons["conversation-send"].isEnabled)
        XCTAssertEqual(composer.value as? String, "An unsent stale draft")
        capture("map-stale-access")
    }

    @MainActor
    func testRevocationRemovesConversationAndMapData() {
        app.launchArguments.append("--ducky-map-ui-revoked")
        openMap(); openPrimaryAgent()
        let composer = element("conversation-composer")
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: composer)
        waitForExpectations(timeout: 30)
        XCTAssertFalse(node("Pairing engineer").exists)
        XCTAssertFalse(app.staticTexts["Scope checked"].exists)
        XCTAssertFalse(app.buttons["conversation-send"].exists)
        XCTAssertTrue(app.staticTexts["Check access on your Mac"].waitForExistence(timeout: 5))
        XCTAssertFalse(element("show-map").exists)
        capture("map-revoked-access")
    }

    /// The runner configures the dedicated device; the fixture verifies the real UIKit motion setting.
    @MainActor
    func testLargerTextAndSystemReducedMotionKeepComposerUsable() throws {
        try XCTSkipUnless(UIAccessibility.isReduceMotionEnabled,
            "Run on the dedicated device with system Reduce Motion enabled")
        app.launchArguments.append("--ducky-map-ui-reduced-motion")
        openMap(); openPrimaryAgent()
        let composer = element("conversation-composer")
        focusComposer(); composer.typeText("Larger text\nSecond line")
        let send = app.buttons["conversation-send"]
        XCTAssertTrue(send.isHittable)
        XCTAssertTrue(app.buttons["conversation-close"].isHittable)
        XCTAssertLessThanOrEqual(composer.frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        XCTAssertLessThanOrEqual(send.frame.maxY, app.keyboards.firstMatch.frame.minY + 1)
        capture("map-larger-text-reduced-motion-typing")
        send.tap()
        XCTAssertTrue(app.staticTexts["On your Mac · waiting to send"].waitForExistence(timeout: 10))
        capture("map-larger-text-reduced-motion-queued")
        app.buttons["constellation-back"].tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.keyboards.firstMatch)
        waitForExpectations(timeout: 5)
        XCTAssertTrue(app.buttons["conversation-close"].exists, "Dismissing the keyboard keeps the conversation open")
        expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: app.staticTexts["On your Mac · waiting to send"])
        waitForExpectations(timeout: 5)
        capture("map-larger-text-reduced-motion-receipt")
    }
}

final class AnalysisUITests: XCTestCase {
    private var app: XCUIApplication!
    @MainActor override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--ducky-analysis-ui-fixture"]
        XCUIDevice.shared.orientation = .portrait
    }
    @MainActor override func tearDown() { app.terminate(); XCUIDevice.shared.orientation = .portrait }
    @MainActor private func element(_ id: String) -> XCUIElement { app.descendants(matching: .any).matching(identifier: id).firstMatch }
    @MainActor private func node(_ name: String) -> XCUIElement { app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", name + ",")).firstMatch }
    @MainActor private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
    @MainActor private func rotate(_ direction: UIDeviceOrientation) {
        XCUIDevice.shared.orientation = direction
        expectation(for: NSPredicate { _, _ in self.app.frame.width > self.app.frame.height }, evaluatedWith: nil)
        waitForExpectations(timeout: 8)
    }
    @MainActor private func openMap() {
        app.launch(); XCTAssertTrue(element("show-map").waitForExistence(timeout: 10)); element("show-map").tap()
        XCTAssertTrue(app.staticTexts["Turn your phone sideways to see the map"].waitForExistence(timeout: 5))
        rotate(.landscapeLeft)
        XCTAssertTrue(node("DuckTerm").waitForExistence(timeout: 5))
    }
    @MainActor private func focus() {
        let composer = element("analysis-composer")
        var prior = CGRect.null; var since = Date()
        expectation(for: NSPredicate { _, _ in
            guard composer.exists, composer.isHittable else { return false }
            if composer.frame != prior { prior = composer.frame; since = Date(); return false }
            return Date().timeIntervalSince(since) > 0.4
        }, evaluatedWith: nil)
        waitForExpectations(timeout: 6)
        composer.tap(); XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
    }
    @MainActor func testFolderQuestionKeepsDraftAcrossRotationAndAnswersAboveKeyboard() {
        openMap(); node("DuckTerm").tap()
        XCTAssertTrue(element("constellation-ask").waitForExistence(timeout: 5)); element("constellation-ask").tap()
        XCTAssertTrue(element("analysis-header").waitForExistence(timeout: 5))
        capture("analysis-folder-ready")
        focus(); element("analysis-composer").typeText("What changed?\nInclude blockers.")
        rotate(.landscapeRight)
        XCTAssertEqual(element("analysis-composer").value as? String, "What changed?\nInclude blockers.")
        XCTAssertTrue(element("analysis-send").isHittable)
        XCTAssertLessThanOrEqual(element("analysis-send").frame.maxY, app.keyboards.firstMatch.frame.minY + 2)
        capture("analysis-folder-keyboard")
        element("analysis-send").tap()
        XCTAssertTrue(element("analysis-pending").waitForExistence(timeout: 5))
        XCTAssertTrue(element("analysis-answer").waitForExistence(timeout: 10))
        XCTAssertTrue(element("analysis-facts").exists)
        XCTAssertGreaterThanOrEqual(element("analysis-scroll").frame.minY, element("analysis-header").frame.maxY - 1)
        capture("analysis-folder-answer-keyboard")
        element("analysis-scroll").swipeDown()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.keyboards.firstMatch)
        waitForExpectations(timeout: 5)
        capture("analysis-folder-answer")
        focus(); element("analysis-composer").typeText("Next scoped question")
        element("analysis-close").tap()
        XCTAssertTrue(element("constellation-ask").waitForExistence(timeout: 5)); element("constellation-ask").tap()
        XCTAssertEqual(element("analysis-composer").value as? String, "Next scoped question")
    }
    @MainActor func testOracleAndUngroupedScopesStayDistinct() {
        openMap(); element("constellation-mode-oracle").tap()
        XCTAssertTrue(element("analysis-header").waitForExistence(timeout: 5))
        focus(); element("analysis-composer").typeText("Where am I needed?"); element("analysis-send").tap()
        XCTAssertTrue(element("analysis-answer").waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "partial context")).firstMatch.exists)
        capture("analysis-oracle-answer")
        element("analysis-close").tap(); node("Ungrouped").tap()
        element("constellation-ask").tap()
        XCTAssertTrue(app.staticTexts["Ask Ungrouped"].waitForExistence(timeout: 5))
        XCTAssertFalse(element("analysis-answer").exists)
        capture("analysis-ungrouped-ready")
    }
    @MainActor func testCodeKeepsOracleFactualAndAllowsEmptyFolderQuestion() {
        app.launchArguments.append("--ducky-analysis-code")
        openMap(); element("constellation-mode-oracle").tap()
        XCTAssertFalse(element("analysis-composer").exists)
        element("constellation-mode-map").tap(); node("Empty").tap()
        XCTAssertTrue(element("constellation-ask").waitForExistence(timeout: 5)); element("constellation-ask").tap()
        XCTAssertTrue(app.staticTexts["Ask Empty"].waitForExistence(timeout: 5))
        capture("analysis-code-empty-folder")
    }
    @MainActor func testLiteKeepsOnlyFactualMap() {
        app.launchArguments.append("--ducky-analysis-lite")
        openMap(); node("DuckTerm").tap(); XCTAssertFalse(element("constellation-ask").exists)
        element("constellation-mode-oracle").tap(); XCTAssertFalse(element("analysis-composer").exists)
        capture("analysis-lite-no-questions")
    }
}
