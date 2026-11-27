import AppKit
import XCTest
@testable import Duckterm

final class RemoteSettingsControlsTests: XCTestCase {
    @MainActor func testEditionRefreshControlsBothNativeEntryPointsAndFailsClosed() async throws {
        _ = NSApplication.shared
        var response: Any = ["id": "full", "features": ["terminal", "remote_workspaces"]]
        var fail = false
        let app = AppDelegate(launchAdmission: WorkspaceLaunchAdmission(), editionRequest: { _ in
            if fail { throw URLError(.timedOut) }
            return response
        }, makePairing: { _, _, _ in fatalError("Pairing is not part of this test") })
        let controls = app.remoteSettings
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 300),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        // An unrelated titlebar accessory must survive every flag transition.
        let other = NSTitlebarAccessoryViewController()
        other.view = NSView(frame: NSRect(x: 0, y: 0, width: 30, height: 30))
        window.addTitlebarAccessoryViewController(other)
        controls.attach(to: window)
        func check(_ allowed: Bool) {
            XCTAssertEqual(controls.menuItem.isHidden, !allowed)
            XCTAssertEqual(controls.menuItem.isEnabled, allowed)
            XCTAssertEqual(app.validateMenuItem(controls.menuItem), allowed)
            XCTAssertEqual(window.titlebarAccessoryViewControllers.count, allowed ? 2 : 1)
            XCTAssertTrue(window.titlebarAccessoryViewControllers.contains(where: { $0 === other }))
        }
        check(false) // before any capability has loaded
        _ = try await app.refreshEdition()
        check(true)
        _ = try await app.refreshEdition()
        check(true) // no duplicate accessory
        response = ["id": "full", "features": ["terminal"]]
        _ = try await app.refreshEdition()
        check(false) // Full alone does not grant BYOC
        response = ["id": "full", "features": ["terminal", "remote_workspaces"]]
        _ = try await app.refreshEdition()
        check(true)
        fail = true
        do { _ = try await app.refreshEdition(); XCTFail("Expected fetch failure") }
        catch { XCTAssertEqual((error as? URLError)?.code, .timedOut) }
        check(false)
        fail = false
        _ = try await app.refreshEdition()
        check(true)
    }

    @MainActor func testEditionMayLoadBeforeTheWindowAndReattachmentRemovesOldControl() {
        _ = NSApplication.shared
        let controls = RemoteSettingsControls()
        controls.update(allowed: true)
        let first = NSWindow(contentRect: .zero, styleMask: [.titled], backing: .buffered, defer: false)
        let second = NSWindow(contentRect: .zero, styleMask: [.titled], backing: .buffered, defer: false)
        first.isReleasedWhenClosed = false; second.isReleasedWhenClosed = false
        defer { first.close(); second.close() }
        controls.attach(to: first)
        XCTAssertEqual(first.titlebarAccessoryViewControllers.count, 1)
        controls.attach(to: second)
        XCTAssertTrue(first.titlebarAccessoryViewControllers.isEmpty)
        XCTAssertEqual(second.titlebarAccessoryViewControllers.count, 1)
        controls.update(allowed: false)
        XCTAssertTrue(second.titlebarAccessoryViewControllers.isEmpty)
    }
}
