import Foundation

enum AppIdentity {
    static let isTest = Bundle.main.object(forInfoDictionaryKey: "DucktermTestBuild") as? Bool == true
    static let name = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "DuckTerm"
    static let edition = Bundle.main.object(forInfoDictionaryKey: "DucktermEdition") as? String
    static let localPort = isTest
        ? (Bundle.main.object(forInfoDictionaryKey: "DucktermTestPort") as? Int ?? 4301)
        : 4300
}
