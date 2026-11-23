import Foundation

/// Update destinations come from the signed bundle, never from the dashboard.
struct AppUpdatePolicy {
    let edition: String
    let test: Bool
    let feed: URL
    let publicKey: String

    init(info: [String: Any]) throws {
        guard let edition = info["DucktermEdition"] as? String,
              ["lite", "code", "full"].contains(edition),
              let key = info["SUPublicEDKey"] as? String,
              Data(base64Encoded: key)?.count == 32,
              let raw = info["SUFeedURL"] as? String, let feed = URL(string: raw) else {
            throw LaunchDestination.Failure.message("This build has no signed update feed. Install the updater-enabled release once.")
        }
        let test = info["DucktermTestBuild"] as? Bool == true
        let expected = "https://github.com/utsavanand/duckterm/releases/latest/download/appcast-\(edition)-arm64.xml"
        guard raw == expected && !test || test && feed.scheme == "http" && feed.host == "127.0.0.1",
              feed.user == nil, feed.password == nil, feed.query == nil, feed.fragment == nil else {
            throw LaunchDestination.Failure.message("The update feed does not match this app edition.")
        }
        self.edition = edition; self.test = test; self.feed = feed; self.publicKey = key
    }

    func accepts(archive: URL?, version: String, informationOnly: Bool) -> Bool {
        guard !informationOnly, Self.validVersion(version), let archive,
              archive.user == nil, archive.password == nil,
              archive.query == nil, archive.fragment == nil else { return false }
        if test { return archive.scheme == "http" && archive.host == "127.0.0.1" && archive.port == feed.port }
        let name = "DuckTerm-\(edition.capitalized)"
        return archive.absoluteString == "https://github.com/utsavanand/duckterm/releases/download/v\(version)/\(name)-\(version)-macos-arm64.zip"
    }

    static func validVersion(_ version: String) -> Bool {
        version.range(of: #"^[0-9]+\.[0-9]+\.[0-9]+$"#, options: .regularExpression) != nil
    }

    static func verified(target: String, app: String, backend: String?) -> Bool {
        validVersion(target) && app == target && backend == target
    }
}
