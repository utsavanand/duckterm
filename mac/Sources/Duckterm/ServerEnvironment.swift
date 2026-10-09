import Foundation

/// Finder and Dock launches normally inherit only the macOS system PATH.
/// The backend needs the same installed tools as the CLI, including tmux.
enum ServerEnvironment {
    static func make(
        inheriting environment: [String: String],
        homeDirectory: String = NSHomeDirectory()
    ) -> [String: String] {
        var result = environment
        let inherited = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
        let fallbacks = [
            "\(homeDirectory)/.local/bin",
            "/opt/homebrew/bin", "/usr/local/bin",
            "/opt/homebrew/sbin", "/usr/local/sbin",
            "/usr/bin", "/bin", "/usr/sbin", "/sbin",
        ]
        var seen = Set<String>()
        result["PATH"] = (inherited + fallbacks).filter { seen.insert($0).inserted }.joined(separator: ":")
        return result
    }

    static func executable(named name: String, environment: [String: String]) -> String? {
        for directory in (environment["PATH"] ?? "").split(separator: ":") {
            let path = URL(fileURLWithPath: String(directory)).appendingPathComponent(name).path
            var isDirectory: ObjCBool = false
            if FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
               !isDirectory.boolValue, FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        return nil
    }
}
