import Foundation

/// The server owns feature availability. A missing description grants nothing.
struct DesktopEdition: Equatable {
    let id: String
    let features: Set<String>
    /// The server's desktop profile; nil from servers predating profiles.
    let profile: String?
    let port: Int?

    init(response: Any) throws {
        let invalid = LaunchDestination.Failure.message("The server did not provide a valid edition")
        guard let object = response as? [String: Any],
              let id = object["id"] as? String, ["lite", "code", "full"].contains(id),
              let features = object["features"] as? [String] else { throw invalid }
        self.id = id
        self.features = Set(features)
        if let instance = object["instance"] {
            guard let instance = instance as? [String: Any],
                  let profile = instance["profile_id"] as? String,
                  let port = instance["port"] as? Int else { throw invalid }
            self.profile = profile
            self.port = port
        } else {
            profile = nil
            port = nil
        }
    }

    func includes(_ feature: String) -> Bool { features.contains(feature) }

    /// An app only uses the server of its own profile; it never falls back to
    /// another port or stops a server that belongs to a different profile.
    func validate(edition expected: String?, profile expectedProfile: String?, port expectedPort: Int) throws {
        guard let expected else { return } // Older, unlabelled app bundles.
        let running = profile.map { "DuckTerm \(id.capitalized)\($0.hasPrefix("test") ? " Test" : "")" }
            ?? "an earlier DuckTerm \(id.capitalized)"
        let conflict = LaunchDestination.Failure.message(
            "Port \(expectedPort) is in use by \(running). Quit that app or stop its server, then reopen this app. Your sessions and settings are retained.")
        guard id == expected else { throw conflict }
        guard let expectedProfile else { return }
        if let profile {
            guard profile == expectedProfile, port == expectedPort else { throw conflict }
        } else {
            // A pre-profile server on :4300 can only be the legacy Full profile.
            guard expectedProfile == "full" else { throw conflict }
        }
    }

    static func requiredFeature(target: String, operation: String) -> String? {
        if operation.hasPrefix("collaboration-") { return "inbox" }
        if target != "local" || operation.hasPrefix("project-") { return "remote_workspaces" }
        return nil
    }
}
