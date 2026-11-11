import XCTest
@testable import DuckyCore

@MainActor
private final class FakeAudio: DictationAudio {
    var calls: [String] = []
    var engineFailure: Error?
    var permission: CheckedContinuation<Bool, Never>?
    var result: ((String?, Bool) -> Void)?
    func authorize() async -> Bool { await withCheckedContinuation { permission = $0 } }
    func grant(_ allowed: Bool = true) { permission?.resume(returning: allowed); permission = nil }
    func activateSession() throws { calls.append("activate") }
    func deactivateSession() { calls.append("deactivate") }
    func installTap() { calls.append("tap") }
    func removeTap() { calls.append("untap") }
    func startEngine() throws {
        if let engineFailure { throw engineFailure }
        calls.append("start")
    }
    func stopEngine() { calls.append("stop") }
    func recognize(_ result: @escaping (String?, Bool) -> Void) { calls.append("recognize"); self.result = result }
    func endRecognition() { calls.append("end") }
}

@MainActor
final class DictationLifecycleTests: XCTestCase {
    private func settle() async { for _ in 0..<20 { await Task.yield() } }

    func testPermissionGrantedAfterStopNeverStartsRecording() async {
        let audio = FakeAudio()
        let dictation = DictationLifecycle(audio: audio, available: true)
        dictation.toggle(draft: "", update: { _ in })
        await settle()
        dictation.stop() // the owner left the chat while the permission sheet was up
        audio.grant()
        await settle()
        XCTAssertFalse(dictation.listening)
        XCTAssertEqual(audio.calls, [], "No session, tap or engine after the start was abandoned")
    }

    func testSecondTapWhilePermissionIsPendingCancelsTheStart() async {
        let audio = FakeAudio()
        let dictation = DictationLifecycle(audio: audio, available: true)
        dictation.toggle(draft: "", update: { _ in })
        await settle()
        dictation.toggle(draft: "", update: { _ in })
        audio.grant()
        await settle()
        XCTAssertFalse(dictation.listening)
        XCTAssertEqual(audio.calls, [])
    }

    func testEngineFailureReleasesTheTapAndSession() async {
        let audio = FakeAudio()
        audio.engineFailure = NSError(domain: "audio", code: 1)
        let dictation = DictationLifecycle(audio: audio, available: true)
        dictation.toggle(draft: "", update: { _ in })
        await settle()
        audio.grant()
        await settle()
        XCTAssertFalse(dictation.listening)
        XCTAssertEqual(audio.calls, ["activate", "tap", "untap", "deactivate"])
    }

    func testTranscriptAppendsToTheDraftAndStaleResultsAreIgnored() async {
        let audio = FakeAudio()
        var draft = "Check"
        let dictation = DictationLifecycle(audio: audio, available: true)
        dictation.toggle(draft: draft, update: { draft = $0 })
        await settle()
        audio.grant()
        await settle()
        XCTAssertTrue(dictation.listening)
        audio.result?("the release notes", false)
        XCTAssertEqual(draft, "Check the release notes")
        let late = audio.result
        dictation.stop()
        XCTAssertEqual(audio.calls, ["activate", "tap", "start", "recognize", "stop", "untap", "end", "deactivate"])
        late?("after stop", true)
        XCTAssertEqual(draft, "Check the release notes", "A result arriving after stop changes nothing")
    }

    func testDeniedPermissionIsReportedAndUnavailableDoesNothing() async {
        let audio = FakeAudio()
        let dictation = DictationLifecycle(audio: audio, available: true)
        dictation.toggle(draft: "", update: { _ in })
        await settle()
        audio.grant(false)
        await settle()
        XCTAssertTrue(dictation.denied)
        XCTAssertFalse(dictation.listening)
        let off = DictationLifecycle(audio: FakeAudio(), available: false)
        off.toggle(draft: "", update: { _ in })
        await settle()
        XCTAssertFalse(off.listening)
    }
}
