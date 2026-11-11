import Foundation

/// The microphone and speech steps dictation needs. The app supplies the AVFoundation/Speech
/// version; tests supply a fake, so the start and stop rules below run without hardware.
@MainActor
public protocol DictationAudio: AnyObject {
    func authorize() async -> Bool
    func activateSession() throws
    func deactivateSession()
    /// Starts feeding microphone buffers into a new recognition request.
    func installTap()
    func removeTap()
    func startEngine() throws
    func stopEngine()
    /// Reports the transcript so far; `final` means recognition ended.
    func recognize(_ result: @escaping (_ text: String?, _ final: Bool) -> Void)
    func endRecognition()
}

/// Speech to draft text. Nothing is sent until the owner taps send.
@MainActor
public final class DictationLifecycle: ObservableObject {
    @Published public private(set) var listening: Bool
    @Published public private(set) var denied = false
    public let available: Bool
    private let audio: DictationAudio
    // Stop, leaving the chat or another tap while permission is pending makes that start inert.
    private var attempt = 0
    private var starting = false
    // Cleanup follows what was acquired, not `listening`: a start can fail halfway.
    private var sessionActive = false
    private var tapped = false
    private var engineRunning = false
    private var recognizing = false

    public init(audio: DictationAudio, available: Bool, listening: Bool = false) {
        self.audio = audio
        self.available = available
        self.listening = listening
    }

    public func toggle(draft: String, update: @escaping (String) -> Void) {
        if listening || starting { stop(); return }
        guard available else { return }
        attempt += 1
        let mine = attempt
        starting = true
        Task {
            let allowed = await audio.authorize()
            guard mine == attempt else { return }
            starting = false
            guard allowed else { denied = true; return }
            denied = false
            do { try start(attempt: mine, after: draft, update: update) } catch { stop() }
        }
    }

    public func stop() {
        attempt += 1
        starting = false
        listening = false
        if engineRunning { audio.stopEngine(); engineRunning = false }
        if tapped { audio.removeTap(); tapped = false }
        if recognizing { audio.endRecognition(); recognizing = false }
        if sessionActive { audio.deactivateSession(); sessionActive = false }
    }

    private func start(attempt mine: Int, after draft: String, update: @escaping (String) -> Void) throws {
        try audio.activateSession()
        sessionActive = true
        audio.installTap()
        tapped = true
        try audio.startEngine()
        engineRunning = true
        listening = true
        // Dictation adds to what is already typed rather than replacing it.
        let prefix = draft.isEmpty || draft.last?.isWhitespace == true ? draft : draft + " "
        recognizing = true
        audio.recognize { [weak self] text, final in
            guard let self, mine == self.attempt else { return }
            if let text { update(prefix + text) }
            if final { self.stop() }
        }
    }
}
