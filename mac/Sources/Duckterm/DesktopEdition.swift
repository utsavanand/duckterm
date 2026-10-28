import Foundation

/// The server owns feature availability. A missing description grants nothing.
struct DesktopEdition: Equatable {
    /// The server's ownership identity; absent from servers predating profiles.
    struct Instance: Equatable {
        let profile: String?
        let instance: String?
        let home: String
        let tmuxSocket: String
        let port: Int
    }

    let id: String
    let features: Set<String>
    let instance: Instance?

    init(response: Any) throws {
        let invalid = LaunchDestination.Failure.message("The server did not provide a valid edition")
        guard let object = response as? [String: Any],
              let id = object["id"] as? String, ["lite", "code", "full"].contains(id),
              let features = object["features"] as? [String] else { throw invalid }
        self.id = id
        self.features = Set(features)
        guard let raw = object["instance"] else {
            instance = nil
            return
        }
        guard let raw = raw as? [String: Any],
              raw["profile_id"] is String || raw["profile_id"] is NSNull,
              raw["instance_id"] is String || raw["instance_id"] is NSNull,
              let home = raw["home"] as? String, let socket = raw["tmux_socket"] as? String,
              let port = raw["port"] as? Int else { throw invalid }
        instance = Instance(profile: raw["profile_id"] as? String, instance: raw["instance_id"] as? String,
                            home: home, tmuxSocket: socket, port: port)
    }

    func includes(_ feature: String) -> Bool { features.contains(feature) }

    /// An app only uses the server of its own profile; it never falls back to
    /// another port or stops a server that belongs to a different profile.
    func validate(edition expected: String?, profile: AppProfile?) throws {
        guard let expected else { return } // Older, unlabelled app bundles.
        let running = instance?.profile.map { "DuckTerm \(id.capitalized)\($0.hasPrefix("test") ? " Test" : "")" }
            ?? "an earlier or command-line DuckTerm \(id.capitalized)"
        let port = profile?.port ?? instance?.port ?? 4300
        let conflict = LaunchDestination.Failure.message(
            "Port \(port) is in use by \(running). Quit that app or stop its server, then reopen this app. Your sessions and settings are retained.")
        guard id == expected else { throw conflict }
        guard let profile else { return }
        guard let instance else {
            // A server predating profiles is only ever the legacy Full workspace.
            guard profile.id == "full" else { throw conflict }
            return
        }
        // The global CLI's Full server reports no profile but the same legacy facets.
        let sameProfile = instance.profile == profile.id || (instance.profile == nil && profile.id == "full")
        guard sameProfile, instance.instance == profile.instance,
              URL(fileURLWithPath: instance.home).resolvingSymlinksInPath() == profile.home.resolvingSymlinksInPath(),
              instance.tmuxSocket == profile.tmuxSocket, instance.port == profile.port else { throw conflict }
    }

    static func requiredFeature(target: String, operation: String) -> String? {
        if operation.hasPrefix("collaboration-") { return "inbox" }
        if target != "local" || operation.hasPrefix("project-") { return "remote_workspaces" }
        return nil
    }
}
