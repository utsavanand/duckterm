import XCTest
import AppKit
import Darwin
@testable import Duckterm

final class LegacyWorkspaceNoticeTests: XCTestCase {
    private func fixture(_ body: (URL) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("duckterm-notice-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try body(root)
    }

    private func profile(_ edition: String, root: URL, id: String? = nil) -> AppProfile {
        AppProfile(id: id ?? edition, edition: edition, instance: edition,
                   home: root.appendingPathComponent(".duckterm-\(edition)"), tmuxSocket: "duckterm-\(edition)", port: 12345)
    }

    private func legacy(_ contents: String, root: URL) throws {
        let home = root.appendingPathComponent(".duckterm")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        try Data(contents.utf8).write(to: home.appendingPathComponent("edition.json"))
    }

    func testOnlyMatchingLegacyLiteOrCodeNeedsTheNotice() throws {
        try fixture { root in
            for edition in ["lite", "code"] {
                let selected = profile(edition, root: root)
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
                try legacy("{\"id\":\"\(edition)\"}", root: root)
                XCTAssertEqual(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root)?.edition, edition)
                for other in ["full", edition == "lite" ? "code" : "lite"] {
                    XCTAssertNil(LegacyWorkspaceNotice.detect(profile: profile(other, root: root), isTest: false, userHome: root))
                }
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: true, userHome: root))
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: profile(edition, root: root, id: "test-\(edition)"), isTest: false, userHome: root))
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: nil, isTest: false, userHome: root))
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root.appendingPathComponent("other")))
                try FileManager.default.removeItem(at: root.appendingPathComponent(".duckterm"))
            }
        }
    }

    func testExistingHomesIncludingDanglingSymlinksNeverGetFirstWorkspaceAdvice() throws {
        try fixture { root in
            try legacy("{\"id\":\"lite\"}", root: root)
            let selected = profile("lite", root: root)
            try FileManager.default.createDirectory(at: selected.home, withIntermediateDirectories: true)
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
            try FileManager.default.removeItem(at: selected.home)
            try Data("existing file".utf8).write(to: selected.home)
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
            try FileManager.default.removeItem(at: selected.home)
            try FileManager.default.createSymbolicLink(at: selected.home, withDestinationURL: root.appendingPathComponent("missing"))
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
        }
    }

    func testMalformedLegacyMetadataDoesNotInventAnEarlierWorkspace() throws {
        try fixture { root in
            for contents in ["", "{}", "[]", "{\"id\":null}", "{\"id\":1}", "{\"id\":\"full\"}", "invalid"] {
                try legacy(contents, root: root)
                XCTAssertNil(LegacyWorkspaceNotice.detect(profile: profile("lite", root: root), isTest: false, userHome: root))
            }
        }
    }

    func testOversizedOrNonregularLegacyMetadataDoesNotBlockStartup() throws {
        try fixture { root in
            let selected = profile("lite", root: root)
            try legacy("{\"id\":\"lite\",\"padding\":\"\(String(repeating: "x", count: 4096))\"}", root: root)
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
            let file = root.appendingPathComponent(".duckterm/edition.json")
            try FileManager.default.removeItem(at: file)
            try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root))
            try FileManager.default.removeItem(at: file)
            XCTAssertEqual(mkfifo(file.path, 0o600), 0)
            XCTAssertNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root), "A FIFO with no writer must return without blocking")
        }
    }

    func testTrustedHomeAliasStillFindsTheMatchingLegacyWorkspace() throws {
        try fixture { root in
            let physical = root.appendingPathComponent("physical")
            let alias = root.appendingPathComponent("alias")
            try FileManager.default.createDirectory(at: physical, withIntermediateDirectories: false)
            try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: physical)
            try legacy("{\"id\":\"lite\"}", root: physical)
            XCTAssertNotNil(LegacyWorkspaceNotice.detect(profile: profile("lite", root: alias), isTest: false, userHome: alias))
        }
    }

    func testDetectionAndCancelDoNotWriteAWorkspaceOrTouchLegacyData() async throws {
        try await MainActor.run {
            try fixture { root in
                try legacy("{\"id\":\"code\"}", root: root)
                let old = root.appendingPathComponent(".duckterm")
                let files = ["edition.json", "state.db", "token", "server.pid"]
                for file in files.dropFirst() { try Data("preserve \(file)".utf8).write(to: old.appendingPathComponent(file)) }
                let before = try files.map { try Data(contentsOf: old.appendingPathComponent($0)) }
                let selected = profile("code", root: root)
                let admission = WorkspaceLaunchAdmission()
                let notice = LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root)
                XCTAssertNotNil(notice)
                XCTAssertFalse(admission.decide(notice: notice) { _ in false })
                XCTAssertFalse(admission.canStart)
                XCTAssertEqual(try files.map { try Data(contentsOf: old.appendingPathComponent($0)) }, before)
                XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root.path), [".duckterm"])
                XCTAssertNotNil(LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root), "Cancel asks again next launch")
            }
        }
    }

    func testReopenCannotBypassAPendingOrCancelledDecision() async {
        await MainActor.run {
            let admission = WorkspaceLaunchAdmission()
            XCTAssertFalse(admission.canStart)
            var presentations = 0
            XCTAssertFalse(admission.decide(notice: LegacyWorkspaceNotice(edition: "lite")) { _ in
                presentations += 1
                XCTAssertFalse(admission.canStart)
                XCTAssertFalse(admission.decide(notice: nil) { _ in XCTFail("reentrant prompt"); return true })
                return false
            })
            XCTAssertFalse(admission.decide(notice: nil) { _ in XCTFail("cancel is final"); return true })
            XCTAssertEqual(presentations, 1)
            let app = AppDelegate()
            XCTAssertFalse(app.applicationShouldHandleReopen(NSApplication.shared, hasVisibleWindows: false))
            XCTAssertFalse(app.applicationShouldTerminateAfterLastWindowClosed(NSApplication.shared))
        }
    }

    func testAcceptanceAllowsNormalStartupExactlyOnceWithoutCreatingTheHome() async throws {
        try await MainActor.run {
            try fixture { root in
                try legacy("{\"id\":\"lite\"}", root: root)
                let selected = profile("lite", root: root)
                let admission = WorkspaceLaunchAdmission()
                let notice = LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root)
                XCTAssertTrue(admission.decide(notice: notice) { _ in
                    XCTAssertFalse(admission.canStart)
                    return true
                })
                XCTAssertTrue(admission.canStart)
                XCTAssertTrue(admission.decide(notice: notice) { _ in XCTFail("already accepted"); return false })
                XCTAssertFalse(FileManager.default.fileExists(atPath: selected.home.path), "Only the backend creates the new home")
                // Model the backend's successful creation, then the next launch.
                try FileManager.default.createDirectory(at: selected.home, withIntermediateDirectories: false)
                let again = LegacyWorkspaceNotice.detect(profile: selected, isTest: false, userHome: root)
                XCTAssertNil(again)
                XCTAssertTrue(WorkspaceLaunchAdmission().decide(notice: again) { _ in XCTFail("already created"); return false })
            }
        }
    }

    func testOnlyLiteOffersTheApprovedPublicDownload() {
        let lite = LegacyWorkspaceNotice(edition: "lite")
        XCTAssertEqual(lite.downloadURL?.lastPathComponent, "DuckTerm-Lite-macos-arm64.zip")
        XCTAssertTrue(lite.downloadURL?.path.contains("/v0.4.178/") == true)
        XCTAssertTrue(lite.paragraphs[2].contains("keep it under a different name"))
        let code = LegacyWorkspaceNotice(edition: "code")
        XCTAssertNil(code.downloadURL)
        XCTAssertEqual(code.paragraphs[2], "To go back to that work, open your earlier DuckTerm Code app.")
    }
}
