import Foundation

enum TmuxAvailability: Equatable {
    case available, missing, checkFailed

    static func probe(python: String?, environment: [String: String], timeout: TimeInterval = 15) async -> Self {
        await Task.detached(priority: .utility) {
            guard let python else {
                let found = (environment["PATH"] ?? "").split(separator: ":").contains {
                    FileManager.default.isExecutableFile(atPath: String($0) + "/tmux")
                }
                return found ? .available : .missing
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = ["-s", "-B", "-m", "duckterm.agents.tmux"]
            process.environment = environment
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            do { try process.run() } catch { return .checkFailed }
            let deadline = Date().addingTimeInterval(timeout)
            while process.isRunning && Date() < deadline {
                try? await Task.sleep(nanoseconds: 20_000_000)
            }
            guard !process.isRunning else {
                process.terminate()
                return .checkFailed
            }
            guard process.terminationReason == .exit else { return .checkFailed }
            let result = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            switch (process.terminationStatus, result) {
            case (0, "duckterm-tmux:available"): return .available
            case (1, "duckterm-tmux:missing"): return .missing
            default: return .checkFailed
            }
        }.value
    }
}
