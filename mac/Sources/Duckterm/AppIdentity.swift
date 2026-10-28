import Foundation

/// The desktop profile is resolved at build time from helpers/instance.py and
/// written to Info.plist; the app never derives ports, homes or ids itself.
enum AppIdentity {
    private static func value<T>(_ key: String) -> T? { Bundle.main.object(forInfoDictionaryKey: key) as? T }

    static let isTest = value("DucktermTestBuild") as Bool? == true
    static let name = value("CFBundleDisplayName") ?? "DuckTerm"
    static let edition: String? = value("DucktermEdition")
    /// Nil only for unlabelled bundles built before desktop profiles.
    static let profile: String? = value("DucktermProfile")
    static let localPort = value("DucktermPort") ?? 4300
    static let home = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent(value("DucktermHome") ?? ".duckterm")
}
