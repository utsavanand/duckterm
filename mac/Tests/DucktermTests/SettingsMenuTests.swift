import AppKit
import XCTest
@testable import Duckterm

final class SettingsMenuTests: XCTestCase {
    @MainActor func testNativeShortcutsPreserveClipboardAndRouteToDashboard() throws {
        _ = NSApplication.shared
        let menu = buildMainMenu()
        let file = try XCTUnwrap(menu.items.first { $0.submenu?.title == "File" }?.submenu)
        let session = try XCTUnwrap(file.items.first { $0.keyEquivalent == "n" })
        XCTAssertEqual((session.representedObject as? [String: String])?["action"], "launch")
        let folder = try XCTUnwrap(file.items.first { $0.keyEquivalent == "N" })
        XCTAssertEqual((folder.representedObject as? [String: String])?["action"], "folder")
        let edit = try XCTUnwrap(menu.items.first { $0.submenu?.title == "Edit" }?.submenu)
        XCTAssertEqual(edit.items.first { $0.keyEquivalent == "c" }?.action, #selector(AppDelegate.copyFromDashboard(_:)))
        let view = try XCTUnwrap(menu.items.first { $0.submenu?.title == "View" }?.submenu)
        XCTAssertNotNil(view.items.first { $0.title == "Theme" }?.submenu)
        XCTAssertNotNil(view.items.first { $0.title == "Terminal colors" }?.submenu)
        XCTAssertFalse(try XCTUnwrap(view.items.first { $0.title == "Focus" }).isEnabled)
        let help = try XCTUnwrap(menu.items.first { $0.submenu?.title == "Help" }?.submenu)
        XCTAssertEqual(help.items.first?.action, #selector(AppDelegate.reportBug(_:)))
    }
}
