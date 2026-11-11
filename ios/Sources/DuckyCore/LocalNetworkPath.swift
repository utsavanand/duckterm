import Foundation
import Network

/// Whether the iPhone is on Wi-Fi or a wired network. The Mac's private address is unreachable over cellular alone.
public final class LocalNetworkPath: @unchecked Sendable {
    public static let shared = LocalNetworkPath()
    private let monitor = NWPathMonitor()
    private let lock = NSLock()
    private var local = true

    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let local = path.status == .satisfied && (path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet))
            self?.lock.withLock { self?.local = local }
        }
        monitor.start(queue: DispatchQueue(label: "app.duckterm.ducky.network-path"))
    }

    public var available: Bool { lock.withLock { local } }
}
