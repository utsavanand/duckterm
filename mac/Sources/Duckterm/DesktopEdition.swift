import Foundation

/// The server owns feature availability. A missing description grants nothing.
struct DesktopEdition: Equatable {
    let id: String
    let features: Set<String>

    init(response: Any) throws {
        guard let object = response as? [String: Any],
              let id = object["id"] as? String, ["lite", "code", "full"].contains(id),
              let features = object["features"] as? [String] else {
            throw LaunchDestination.Failure.message("The server did not provide a valid edition")
        }
        self.id = id
        self.features = Set(features)
    }

    func includes(_ feature: String) -> Bool { features.contains(feature) }

    func validate(expected: String?) throws {
        guard let expected else { return } // Older, unlabelled app bundles.
        guard id == expected else {
            throw LaunchDestination.Failure.message(
                "Duckterm \(id.capitalized) is already running. Open that edition, or restart the server with duckterm restart --edition \(expected) before opening this app. Your sessions and settings are retained.")
        }
    }

    static func requiredFeature(target: String, operation: String) -> String? {
        if operation.hasPrefix("collaboration-") { return "inbox" }
        if target != "local" || operation.hasPrefix("project-") { return "remote_workspaces" }
        return nil
    }
}
