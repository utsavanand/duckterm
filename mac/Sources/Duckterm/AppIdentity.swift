import Foundation

/// A desktop profile owns one home, tmux socket and port. build.sh resolves it
/// from helpers/instance.py into Info.plist; the app never derives these itself.
struct AppProfile: Equatable {
    let id: String
    let edition: String
    /// Nil for the legacy Full profile.
    let instance: String?
    let home: URL
    let tmuxSocket: String
    let port: Int

    var url: String { "http://127.0.0.1:\(port)" }

    /// Exactly the workspace servers predating profiles used; nothing else may
    /// accept such a server, including a Full Test or custom Full profile.
    var isCanonicalFull: Bool {
        id == "full" && edition == "full" && instance == nil && port == 4300 && tmuxSocket == "duckterm"
            && home.standardizedFileURL == URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".duckterm")
    }

    /// The complete context the backend validates; the empty instance clears a stale one.
    var environment: [String: String] {
        [
            "DUCKTERM_DESKTOP_PROFILE": id,
            "DUCKTERM_EDITION": edition,
            "DUCKTERM_INSTANCE": instance ?? "",
            "DUCKTERM_HOME": home.path,
            "DUCKTERM_TMUX_SOCKET": tmuxSocket,
            "DUCKTERM_PORT": String(port),
            "DUCKTERM_URL": url,
        ]
    }
}

enum AppIdentity {
    private static func value<T>(_ key: String) -> T? { Bundle.main.object(forInfoDictionaryKey: key) as? T }

    static let isTest = value("DucktermTestBuild") as Bool? == true
    static let name = value("CFBundleDisplayName") ?? "DuckTerm"
    static let edition: String? = value("DucktermEdition")

    /// NSHomeDirectory ignores $HOME; only test builds honor it, so QA can run
    /// a test profile in a private home without overriding profile facets.
    static func userHome(isTest: Bool, environment: [String: String]) -> URL {
        if isTest, let home = environment["HOME"], home.hasPrefix("/") {
            return URL(fileURLWithPath: home)
        }
        return URL(fileURLWithPath: NSHomeDirectory())
    }

    /// Nil only for unlabelled bundles built before desktop profiles.
    static let profile: AppProfile? = {
        guard let id: String = value("DucktermProfile"), let edition,
              let home: String = value("DucktermHome"), let socket: String = value("DucktermTmuxSocket"),
              let port: Int = value("DucktermPort") else { return nil }
        let base = userHome(isTest: isTest, environment: ProcessInfo.processInfo.environment)
        return AppProfile(id: id, edition: edition, instance: value("DucktermInstance"),
                          home: base.appendingPathComponent(home), tmuxSocket: socket, port: port)
    }()

    static let localPort = profile?.port ?? 4300
    static let home = profile?.home ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".duckterm")
}
