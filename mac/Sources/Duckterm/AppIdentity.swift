import Foundation

enum AppIdentity {
    static let isTest = Bundle.main.object(forInfoDictionaryKey: "DucktermTestBuild") as? Bool == true
    static let name = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "RubberTerm"
    static let localPort = isTest
        ? (Bundle.main.object(forInfoDictionaryKey: "DucktermTestPort") as? Int ?? 4301)
        : 4300
}
