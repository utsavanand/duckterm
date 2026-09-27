import Foundation
import Darwin

/// Connection metadata only. Authentication and host keys belong to OpenSSH.
struct RemoteHost: Codable, Equatable {
    let name: String
    let target: String
    let remotePort: Int

    init(name: String, target: String, remotePort: Int = 4300) throws {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-@")
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !target.isEmpty, !target.hasPrefix("-"),
              target.unicodeScalars.allSatisfy({ allowed.contains($0) }),
              (1...65535).contains(remotePort)
        else { throw HostError.invalidTarget }
        self.name = name
        self.target = target
        self.remotePort = remotePort
    }

    enum HostError: LocalizedError {
        case invalidTarget, noPort
        var errorDescription: String? {
            switch self {
            case .invalidTarget: return "Enter an SSH config alias or user@hostname and a port from 1–65535."
            case .noPort: return "Could not reserve a local port for the SSH connection."
            }
        }
    }

    static func load() -> [RemoteHost] {
        guard let data = UserDefaults.standard.data(forKey: "remoteHosts"),
              let hosts = try? JSONDecoder().decode([RemoteHost].self, from: data)
        else { return [] }
        // Validate persisted values too: synthesized decoding bypasses init.
        return hosts.compactMap { try? RemoteHost(name: $0.name, target: $0.target, remotePort: $0.remotePort) }
    }

    static func save(_ hosts: [RemoteHost]) {
        guard let data = try? JSONEncoder().encode(hosts) else { return }
        UserDefaults.standard.set(data, forKey: "remoteHosts")
    }

    func arguments(port: Int) -> [String] {
        ["-N", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
         "-o", "ServerAliveCountMax=3", "-o", "ConnectTimeout=10",
         "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=no",
         "-L", "127.0.0.1:\(port):127.0.0.1:\(remotePort)", target]
    }
}

/// Owns only a tunnel; never sends a remote stop command. A dead tunnel is
/// replaced with backoff. HTTP failure alone does not spawn duplicate SSH jobs.
@MainActor
final class RemoteConnection {
    let host: RemoteHost
    let url: URL
    private let port: Int
    private var process: Process?
    private var loop: Task<Void, Never>?
    var onStatus: ((String, Bool) -> Void)?

    init(host: RemoteHost) throws {
        self.host = host
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw RemoteHost.HostError.noPort }
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let read = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
        }
        guard bound == 0, read == 0 else { throw RemoteHost.HostError.noPort }
        port = Int(UInt16(bigEndian: address.sin_port))
        url = URL(string: "http://127.0.0.1:\(port)")!
    }

    func start() {
        stop()
        loop = Task { [weak self] in
            var delay: UInt64 = 1
            while !Task.isCancelled {
                guard let self else { return }
                if self.process?.isRunning != true {
                    self.onStatus?("Connecting", false)
                    let process = Process()
                    process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
                    process.arguments = self.host.arguments(port: self.port)
                    process.standardInput = FileHandle.nullDevice
                    process.standardOutput = FileHandle.nullDevice
                    process.standardError = FileHandle.nullDevice
                    do { try process.run(); self.process = process }
                    catch { self.onStatus?("SSH could not start", false) }
                }
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                guard !Task.isCancelled else { return }
                var request = URLRequest(url: self.url)
                request.timeoutInterval = 2
                let response = try? await URLSession.shared.data(for: request)
                guard !Task.isCancelled else { return }
                let http = response?.1 as? HTTPURLResponse
                let connected = self.process?.isRunning == true && http?.value(forHTTPHeaderField: "X-Duckterm") == "1"
                self.onStatus?(connected ? "Connected" : "Disconnected — retrying; remote agents may still be running", connected)
                delay = connected ? 3 : min(delay * 2, 15)
            }
        }
    }

    func stop() {
        loop?.cancel()
        loop = nil
        if process?.isRunning == true { process?.terminate() }
        process = nil
    }
}
