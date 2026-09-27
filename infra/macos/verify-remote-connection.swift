// Compile with mac/Sources/Duckterm/RemoteHost.swift, then pass a verified SSH alias.
// Uses the production tunnel/reconnect implementation against a real workspace.
import Foundation
import Darwin

@main
struct VerifyRemoteConnection {
    @MainActor
    static func main() async throws {
        guard CommandLine.arguments.count == 2 else { exit(2) }
        let host = try RemoteHost(name: "QA", target: CommandLine.arguments[1])
        let connection = try RemoteConnection(host: host)
        var dropped = false
        var sawDisconnected = false
        var recovered = false
        connection.onStatus = { status, connected in
            print(status)
            if connected && !dropped {
                let children = Process()
                let output = Pipe()
                children.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
                children.arguments = ["-P", String(getpid()), "-x", "ssh"]
                children.standardOutput = output
                do {
                    try children.run()
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    children.waitUntilExit()
                    let pids = String(decoding: data, as: UTF8.self).split(separator: "\n")
                    guard pids.count == 1, let pid = Int32(pids[0]) else { return }
                    dropped = kill(pid, SIGTERM) == 0
                } catch { print("Could not interrupt the QA tunnel") }
            } else if dropped && !connected {
                sawDisconnected = true
            } else if dropped && connected && sawDisconnected {
                recovered = true
            }
        }
        connection.start()
        for _ in 0..<90 {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            if recovered { break }
        }
        connection.stop()
        guard recovered else { print("FAIL: automatic reconnect was not observed"); exit(1) }
        print("PASS: native tunnel recovered after its SSH process was terminated")
    }
}
