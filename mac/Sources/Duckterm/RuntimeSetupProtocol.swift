import Foundation

enum RuntimeSetupRuntime: String, Equatable, Sendable { case claudeCode = "claude-code", codex }
enum RuntimeSetupAction: String, Equatable, Sendable { case status, install, login }
enum RuntimeSetupProbe: String, Equatable, Sendable { case missing, ok, unrecognized, timeout, failed }
enum RuntimeSetupAuthentication: String, Equatable, Sendable {
    case notChecked = "not_checked", signedIn = "signed_in", signedOut = "signed_out", unknown
}
enum RuntimeSetupCredentialMethod: String, Equatable, Sendable { case unknown, account, apiKey = "api_key" }
enum RuntimeSetupStatusReason: String, Equatable, Sendable { case authStatusUnavailable = "auth_status_unavailable" }
enum RuntimeSetupFailureReason: String, Equatable, Sendable {
    case invalidRequest = "invalid_request", invalidExecutable = "invalid_executable"
    case configurationUnavailable = "configuration_unavailable", busy, notInstalled = "not_installed"
    case invalidCommand = "invalid_command", cancelled, timeout, outputLimit = "output_limit"
    case cleanupFailed = "cleanup_failed", transportUnavailable = "transport_unavailable"
    case setupUnavailable = "setup_unavailable"
}

struct RuntimeSetupStatus: Equatable, Sendable {
    let runtime: RuntimeSetupRuntime
    let installed: Bool
    let version: String?
    let probe: RuntimeSetupProbe
    let authentication: RuntimeSetupAuthentication
    let credentialMethod: RuntimeSetupCredentialMethod
    let environmentAPIKeyConfigured: Bool
    let reason: RuntimeSetupStatusReason?
}

/// No decoder error retains or displays rejected input, provider output or credentials.
enum RuntimeSetupProtocolError: Error, Equatable { case invalidFrame, tooLarge, unexpectedEvent, incompleteStream }

enum RuntimeSetupEvent: Equatable, Sendable {
    case ready(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction)
    case output(Data)
    case status(RuntimeSetupStatus)
    case exited(code: Int, cancelled: Bool)
    case error(RuntimeSetupFailureReason)

    /// Serialized JSON plus its terminating LF must fit the helper's 64 KiB bound.
    static let maximumFrameBytes = 65_536
    static let maximumTerminalBytes = 32_768

    static func decode(_ data: Data) throws -> Self {
        guard data.count < maximumFrameBytes else { throw RuntimeSetupProtocolError.tooLarge }
        let frame = try SetupJSONObject(data)
        guard try frame.integer("protocol") == 1 else { throw RuntimeSetupProtocolError.invalidFrame }
        switch try frame.string("type") {
        case "ready":
            try frame.keys(["protocol", "type", "runtime", "action"])
            let runtime: RuntimeSetupRuntime = try frame.enumeration("runtime")
            let action: RuntimeSetupAction = try frame.enumeration("action")
            guard action != .status else { throw RuntimeSetupProtocolError.invalidFrame }
            return .ready(runtime: runtime, action: action)
        case "output":
            try frame.keys(["protocol", "type", "data"])
            let encoded = try frame.string("data")
            guard let bytes = Data(base64Encoded: encoded), bytes.base64EncodedString() == encoded else {
                throw RuntimeSetupProtocolError.invalidFrame
            }
            guard bytes.count <= maximumTerminalBytes else { throw RuntimeSetupProtocolError.tooLarge }
            return .output(bytes)
        case "status":
            try frame.keys(["protocol", "type", "runtime", "installed", "version", "probe", "authentication",
                            "credential_method", "environment_api_key_configured"], optional: ["reason"])
            let version: String?
            if frame.values["version"] == .null { version = nil }
            else {
                let text = try frame.string("version")
                guard text.utf8.count <= 32,
                      text.range(of: #"\A[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-(?:alpha|beta|rc)(?:\.[0-9]{1,4})?)?\z"#,
                                 options: .regularExpression) != nil else { throw RuntimeSetupProtocolError.invalidFrame }
                version = text
            }
            return .status(try .init(runtime: frame.enumeration("runtime"), installed: frame.boolean("installed"),
                version: version, probe: frame.enumeration("probe"), authentication: frame.enumeration("authentication"),
                credentialMethod: frame.enumeration("credential_method"),
                environmentAPIKeyConfigured: frame.boolean("environment_api_key_configured"),
                reason: frame.values["reason"] == nil ? nil : frame.enumeration("reason")))
        case "exited":
            try frame.keys(["protocol", "type", "exit_code", "cancelled"])
            let code = try frame.integer("exit_code"), cancelled = try frame.boolean("cancelled")
            // Cancellation has its own error frame, emitted after helper cleanup.
            guard Int32(exactly: code) != nil, !cancelled else { throw RuntimeSetupProtocolError.invalidFrame }
            return .exited(code: code, cancelled: cancelled)
        case "error":
            try frame.keys(["protocol", "type", "reason"])
            return .error(try frame.enumeration("reason"))
        default: throw RuntimeSetupProtocolError.invalidFrame
        }
    }
}

/// One decoder belongs to one native-owned process/generation. The future process
/// controller must drop callbacks from replaced jobs; pipe input cannot start a job.
/// Only a partial line is retained. Output may stream indefinitely within bounded reads.
struct RuntimeSetupFrames {
    private enum Phase { case waiting, streaming, finished, failed }
    private var phase = Phase.waiting
    private var buffer = Data()
    let runtime: RuntimeSetupRuntime
    let action: RuntimeSetupAction
    var complete: Bool { phase == .finished && buffer.isEmpty }

    init(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction) { self.runtime = runtime; self.action = action }

    mutating func append(_ bytes: Data) throws -> [RuntimeSetupEvent] {
        do {
            guard bytes.count <= RuntimeSetupEvent.maximumFrameBytes else { throw RuntimeSetupProtocolError.tooLarge }
            var events: [RuntimeSetupEvent] = []
            for byte in bytes {
                guard phase != .finished && phase != .failed else { throw RuntimeSetupProtocolError.unexpectedEvent }
                if byte == 10 {
                    let event = try RuntimeSetupEvent.decode(buffer)
                    try accept(event)
                    buffer.removeAll(keepingCapacity: true)
                    events.append(event)
                } else {
                    guard buffer.count < RuntimeSetupEvent.maximumFrameBytes - 1 else { throw RuntimeSetupProtocolError.tooLarge }
                    buffer.append(byte)
                }
            }
            return events
        } catch {
            phase = .failed
            buffer.removeAll()
            throw error
        }
    }

    mutating func finish() throws {
        guard complete else {
            phase = .failed; buffer.removeAll()
            throw RuntimeSetupProtocolError.incompleteStream
        }
    }

    private mutating func accept(_ event: RuntimeSetupEvent) throws {
        switch event {
        case let .ready(receivedRuntime, receivedAction):
            guard phase == .waiting, action != .status, receivedRuntime == runtime, receivedAction == action else {
                throw RuntimeSetupProtocolError.unexpectedEvent
            }
            phase = .streaming
        case .output:
            guard phase == .streaming else { throw RuntimeSetupProtocolError.unexpectedEvent }
        case let .status(status):
            guard phase == .waiting, action == .status, status.runtime == runtime else { throw RuntimeSetupProtocolError.unexpectedEvent }
            phase = .finished
        case .exited:
            guard phase == .streaming else { throw RuntimeSetupProtocolError.unexpectedEvent }
            phase = .finished
        case .error: phase = .finished
        }
    }
}

enum RuntimeSetupCommand: Equatable, Sendable {
    case input(Data), resize(columns: Int, rows: Int), cancel

    func encode() throws -> Data {
        let value: [String: Any]
        switch self {
        case let .input(bytes):
            guard bytes.count <= RuntimeSetupEvent.maximumTerminalBytes else { throw RuntimeSetupProtocolError.tooLarge }
            value = ["type": "input", "data": bytes.base64EncodedString()]
        case let .resize(columns, rows):
            guard (1...500).contains(columns), (1...300).contains(rows) else { throw RuntimeSetupProtocolError.invalidFrame }
            value = ["type": "resize", "cols": columns, "rows": rows]
        case .cancel: value = ["type": "cancel"]
        }
        var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
        data.append(10)
        guard data.count <= RuntimeSetupEvent.maximumFrameBytes else { throw RuntimeSetupProtocolError.tooLarge }
        return data
    }
}

/// The wire grammar is deliberately flat. Reading each JSON string with Foundation
/// preserves escaping/Unicode validation; the small outer parser rejects duplicate
/// keys, nested objects, coercible booleans and non-integer numeric spellings.
private struct SetupJSONObject {
    enum Value: Equatable { case string(String), integer(Int), boolean(Bool), null }
    let values: [String: Value]

    init(_ data: Data) throws {
        var reader = Reader(bytes: Array(data))
        values = try reader.object()
    }
    func keys(_ required: Set<String>, optional: Set<String> = []) throws {
        let actual = Set(values.keys)
        guard required.isSubset(of: actual), actual.isSubset(of: required.union(optional)) else { throw RuntimeSetupProtocolError.invalidFrame }
    }
    func string(_ key: String) throws -> String {
        guard case let .string(value) = values[key] else { throw RuntimeSetupProtocolError.invalidFrame }; return value
    }
    func integer(_ key: String) throws -> Int {
        guard case let .integer(value) = values[key] else { throw RuntimeSetupProtocolError.invalidFrame }; return value
    }
    func boolean(_ key: String) throws -> Bool {
        guard case let .boolean(value) = values[key] else { throw RuntimeSetupProtocolError.invalidFrame }; return value
    }
    func enumeration<T: RawRepresentable>(_ key: String) throws -> T where T.RawValue == String {
        guard let value = T(rawValue: try string(key)) else { throw RuntimeSetupProtocolError.invalidFrame }; return value
    }

    private struct Reader {
        let bytes: [UInt8]
        var index = 0
        mutating func whitespace() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
        mutating func take(_ byte: UInt8) throws {
            whitespace()
            guard index < bytes.count, bytes[index] == byte else { throw RuntimeSetupProtocolError.invalidFrame }
            index += 1
        }
        mutating func string() throws -> String {
            whitespace(); let start = index
            try take(34)
            while index < bytes.count {
                let byte = bytes[index]; index += 1
                if byte == 92 { index += 1 }
                else if byte == 34 {
                    do { return try JSONDecoder().decode(String.self, from: Data(bytes[start..<index])) }
                    catch { throw RuntimeSetupProtocolError.invalidFrame }
                }
            }
            throw RuntimeSetupProtocolError.invalidFrame
        }
        mutating func value() throws -> Value {
            whitespace()
            guard index < bytes.count else { throw RuntimeSetupProtocolError.invalidFrame }
            if bytes[index] == 34 { return .string(try string()) }
            let start = index
            while index < bytes.count && bytes[index] != 44 && bytes[index] != 125 { index += 1 }
            let token = String(decoding: bytes[start..<index], as: UTF8.self).trimmingCharacters(in: .init(charactersIn: " \t\r\n"))
            switch token {
            case "true": return .boolean(true)
            case "false": return .boolean(false)
            case "null": return .null
            default:
                guard token.range(of: #"\A-?(?:0|[1-9][0-9]*)\z"#, options: .regularExpression) != nil,
                      let number = Int(token) else { throw RuntimeSetupProtocolError.invalidFrame }
                return .integer(number)
            }
        }
        mutating func object() throws -> [String: Value] {
            try take(123)
            var result: [String: Value] = [:]
            while true {
                let key = try string()
                guard result[key] == nil else { throw RuntimeSetupProtocolError.invalidFrame }
                try take(58)
                result[key] = try value()
                whitespace()
                guard index < bytes.count else { throw RuntimeSetupProtocolError.invalidFrame }
                if bytes[index] == 125 { index += 1; break }
                try take(44)
            }
            whitespace()
            guard index == bytes.count else { throw RuntimeSetupProtocolError.invalidFrame }
            return result
        }
    }
}
