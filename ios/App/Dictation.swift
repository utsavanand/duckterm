import AVFoundation
import Speech
import SwiftUI

/// Speech to draft text, recognised on the iPhone itself: no audio leaves the device,
/// and nothing is sent until the owner taps send.
@MainActor
final class Dictation: ObservableObject {
    @Published private(set) var listening = false
    @Published private(set) var denied = false
    private let recognizer = SFSpeechRecognizer()
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var fixture = false

    init() {
        #if DEBUG
        // Simulators lack on-device recognition; screenshots need the button and its recording state.
        let arguments = ProcessInfo.processInfo.arguments
        fixture = arguments.contains("--ducky-dictation-fixture")
        listening = arguments.contains("--ducky-dictating-fixture")
        #endif
    }

    /// Server recognition would send the owner's voice off the device, so without on-device support there is no button.
    var available: Bool { fixture || recognizer?.supportsOnDeviceRecognition == true }

    func toggle(draft: String, update: @escaping (String) -> Void) {
        if listening { stop(); return }
        Task {
            guard await authorize() else { denied = true; return }
            denied = false
            do { try start(after: draft, update: update) } catch { stop() }
        }
    }

    func stop() {
        guard listening else { return }
        listening = false
        if engine.isRunning { engine.stop(); engine.inputNode.removeTap(onBus: 0) }
        request?.endAudio(); task?.finish()
        request = nil; task = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func authorize() async -> Bool {
        let speech = await withCheckedContinuation { done in
            SFSpeechRecognizer.requestAuthorization { done.resume(returning: $0 == .authorized) }
        }
        guard speech else { return false }
        return await withCheckedContinuation { done in
            AVAudioSession.sharedInstance().requestRecordPermission { done.resume(returning: $0) }
        }
    }

    private func start(after draft: String, update: @escaping (String) -> Void) throws {
        guard let recognizer, available else { return }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in request.append(buffer) }
        engine.prepare()
        try engine.start()
        self.request = request
        listening = true
        // Dictation adds to what is already typed rather than replacing it.
        let prefix = draft.isEmpty || draft.last?.isWhitespace == true ? draft : draft + " "
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                guard let self, self.request === request else { return }
                if let result { update(prefix + result.bestTranscription.formattedString) }
                if error != nil || result?.isFinal == true { self.stop() }
            }
        }
    }
}

struct DictationButton: View {
    @ObservedObject var dictation: Dictation
    let draft: String
    let update: (String) -> Void

    var body: some View {
        if dictation.available || dictation.listening {
            Button { dictation.toggle(draft: draft, update: update) } label: {
                Image(systemName: dictation.listening ? "stop.circle.fill" : "mic")
                    .font(.system(size: 18, weight: .regular))
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(dictation.listening ? ChatColor.accent : ChatColor.muted)
            .accessibilityLabel(dictation.listening ? "Stop dictation" : "Dictate message")
            .accessibilityIdentifier("composer-dictate")
        }
    }
}
