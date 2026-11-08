import Foundation

/// Installation is a native-local capability, never a remote dashboard API.
enum RuntimeSetupAccess {
    static func allows(expected: URL, localPort: Int, target: String, enabled: Bool,
                       currentWebView: Bool, mainFrame: Bool,
                       scheme: String, host: String, port: Int) -> Bool {
        enabled && currentWebView && mainFrame && target == "local"
            && expected.scheme == "http" && expected.host == "127.0.0.1"
            && expected.port == localPort && scheme == expected.scheme
            && host == expected.host && port == localPort
    }
}
