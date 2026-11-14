import AVFoundation
import DuckyCore
import Speech
import SwiftUI

extension DictationLifecycle {
    /// Recognition runs on the iPhone itself: server recognition would send the owner's voice
    /// off the device, so without on-device support there is no button.
    convenience init() {
        let audio = SpeechAudio()
        var available = audio.recognizer?.supportsOnDeviceRecognition == true
        var listening = false
        #if DEBUG
        // Simulators lack on-device recognition; screenshots need the button and its recording state.
        let arguments = ProcessInfo.processInfo.arguments
        available = available || arguments.contains("--ducky-dictation-fixture")
        listening = arguments.contains("--ducky-dictating-fixture")
        #endif
        self.init(audio: audio, available: available, listening: listening)
    }
}

@MainActor
private final class SpeechAudio: DictationAudio {
    let recognizer = SFSpeechRecognizer()
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    func authorize() async -> Bool {
        let speech = await withCheckedContinuation { done in
            SFSpeechRecognizer.requestAuthorization { done.resume(returning: $0 == .authorized) }
        }
        guard speech else { return false }
        return await withCheckedContinuation { done in
            AVAudioSession.sharedInstance().requestRecordPermission { done.resume(returning: $0) }
        }
    }

    func activateSession() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)
    }

    func deactivateSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func installTap() {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        self.request = request
        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in request.append(buffer) }
    }

    func removeTap() { engine.inputNode.removeTap(onBus: 0) }

    func startEngine() throws {
        engine.prepare()
        try engine.start()
    }

    func stopEngine() { engine.stop() }

    func recognize(_ result: @escaping (String?, Bool) -> Void) {
        guard let recognizer, let request else { result(nil, true); return }
        task = recognizer.recognitionTask(with: request) { outcome, error in
            let text = outcome?.bestTranscription.formattedString
            let final = error != nil || outcome?.isFinal == true
            Task { @MainActor in result(text, final) }
        }
    }

    func endRecognition() {
        request?.endAudio(); task?.finish()
        request = nil; task = nil
    }
}

struct DictationButton: View {
    @ObservedObject var dictation: DictationLifecycle
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
