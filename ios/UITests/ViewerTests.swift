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
    func testComposerKeepsNewlinesAndLocalCommandsCannotBeSent() {
        launchConversation()
        let composer = element("conversation-composer")
        let send = app.buttons["conversation-send"]
        XCTAssertFalse(send.isEnabled)
        composer.tap()
        composer.typeText("First line\nSecond line")
        XCTAssertEqual(composer.value as? String, "First line\nSecond line")
        XCTAssertTrue(send.isEnabled)
        XCTAssertFalse(app.staticTexts["Queued on your Mac"].exists, "Return must not submit a message")
        capture("chat-multiline-composer")

        composer.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: "First line\nSecond line".count))
        composer.typeText("/clear")
        XCTAssertEqual(composer.value as? String, "/clear")
        XCTAssertFalse(send.isEnabled, "Compact UI must preserve the existing local-command guard")
        XCTAssertFalse(app.staticTexts["Queued on your Mac"].exists)
    }

    @MainActor
    func testDelayedQueuedReceiptKeepsComposerAndKeyboardAvailable() {
        launchConversation()
        let composer = element("conversation-composer")
        composer.tap()
        composer.typeText("Queue this fixture message\nwith its second line")
        let keyboardWasVisible = app.keyboards.firstMatch.exists
        app.buttons["conversation-send"].tap()
        let queued = app.staticTexts["Queued on your Mac"]
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
        let queued = app.staticTexts["Queued on your Mac"]
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
