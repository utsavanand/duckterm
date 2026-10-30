import Foundation

/// The server no longer prints its owner token into GET /, because anything
/// reaching the port could read it. The app reads it where the server keeps it:
/// the local workspace's token file, or over SSH for a remote computer, which is
/// the same trust that opens its tunnel.
enum OwnerToken {
    private static let lock = NSLock()
    private static var remotes: [Int: RemoteHost] = [:]
    private static var cache: [Int: String] = [:]

    static func register(tunnelPort: Int, host: RemoteHost) {
        lock.withLock {
            remotes[tunnelPort] = host
            cache[tunnelPort] = nil
        }
    }

    static func local(home: URL = AppIdentity.home) -> String? {
        guard let text = try? String(contentsOf: home.appendingPathComponent("token"), encoding: .utf8) else { return nil }
        let token = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return valid(token) ? token : nil
    }

    static func token(base: URL) async throws -> String {
        guard let port = base.port else { throw LaunchDestination.Failure.message("Invalid destination") }
        if port == AppIdentity.localPort {
            guard let token = local() else { throw LaunchDestination.Failure.message("This Mac's DuckTerm token is missing.") }
            return token
        }
        let (host, cached) = lock.withLock { (remotes[port], cache[port]) }
        if let cached { return cached }
        guard let host else { throw LaunchDestination.Failure.message("Unknown computer") }
        let token = try await remote(host)
        lock.withLock { cache[port] = token }
        return token
    }

    static func valid(_ token: String) -> Bool {
        !token.isEmpty && token.utf8.count <= 128
            && token.range(of: #"^[A-Za-z0-9_-]+$"#, options: .regularExpression) != nil
    }

    /// Finds the workspace serving `remotePort` from the server.json each server
    /// writes under its home, then prints that home's token. Sent on stdin to
    /// `sh -s` so the remote login shell never parses it.
    static func remoteScript(port: Int) -> String {
        """
        for f in "$HOME"/.duckterm*/session-credentials/server.json; do
          if grep -q '"url": "http://127.0.0.1:\(port)"' "$f" 2>/dev/null; then
            cat "${f%/session-credentials/server.json}/token"; exit
          fi
        done
        exit 1

        """
    }

    private static func remote(_ host: RemoteHost) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
            process.arguments = host.tokenArguments()
            let input = Pipe(), output = Pipe()
            process.standardInput = input
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            process.terminationHandler = { finished in
                let data = output.fileHandleForReading.readDataToEndOfFile()
                let token = String(decoding: data.prefix(256), as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                if finished.terminationStatus == 0, valid(token) {
                    continuation.resume(returning: token)
                } else {
                    continuation.resume(throwing: LaunchDestination.Failure.message(
                        "Could not read DuckTerm's token on \(host.name). Check SSH access and that DuckTerm runs there on port \(host.remotePort)."))
                }
            }
            do {
                try process.run()
                input.fileHandleForWriting.write(Data(remoteScript(port: host.remotePort).utf8))
                try? input.fileHandleForWriting.close()
            } catch {
                continuation.resume(throwing: LaunchDestination.Failure.message("SSH could not start"))
            }
        }
    }
}
