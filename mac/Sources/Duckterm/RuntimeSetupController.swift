import Darwin
import Foundation
import CoreFoundation

@MainActor
final class RuntimeSetupController {
    enum Failure: LocalizedError {
        case invalid, busy, stale, unavailable, cancelled
        var errorDescription: String? {
            switch self {
            case .invalid: return "Invalid agent setup request"
            case .busy: return "Wait for the current setup operation to finish"
            case .stale: return "This setup operation is no longer active"
            case .unavailable: return "Agent setup is unavailable. Try again or use manual instructions."
            case .cancelled: return "Setup cancelled"
            }
        }
    }
    typealias Factory = @MainActor (RuntimeSetupRuntime, RuntimeSetupAction, URL?) -> RuntimeSetupTransport
    var onEvent: (([String: Any]) -> Void)?
    var onOutput: (([String: Any]) async -> Void)?
    var onPreferences: (([String: Any]) -> Void)?
    private let preferences: RuntimeSetupPreferences
    private let factory: Factory
    private let picker: (RuntimeSetupRuntime) async -> URL?
    private var generation = 0
    private var job: Job?
    private var selections: [String: Selection] = [:]
    private var picking = false
    private var usedRequests = Set<String>()
    private var cleanupWaiters: [CheckedContinuation<Void, Never>] = []

    private struct Selection {
        let runtime: RuntimeSetupRuntime
        let url: URL
        let identity: RuntimeSetupExecutable
    }
    private final class Job {
        let id = UUID().uuidString
        let requestID: String
        let runtime: RuntimeSetupRuntime
        let action: RuntimeSetupAction
        let executable: URL?
        let generation: Int
        let transport: RuntimeSetupTransport
        var result: RuntimeSetupEvent?
        var completed = false
        var finish: ((RuntimeSetupEvent) -> Void)?
        init(requestID: String, runtime: RuntimeSetupRuntime, action: RuntimeSetupAction,
             executable: URL?, generation: Int, transport: RuntimeSetupTransport) {
            self.requestID = requestID; self.runtime = runtime; self.action = action
            self.executable = executable; self.generation = generation; self.transport = transport
        }
        var response: [String: Any] { ["job_id": id, "request_id": requestID] }
    }

    init(preferences: RuntimeSetupPreferences,
         factory: @escaping Factory = { RuntimeSetupProcess(runtime: $0, action: $1, executable: $2) },
         picker: @escaping (RuntimeSetupRuntime) async -> URL?) {
        self.preferences = preferences; self.factory = factory; self.picker = picker
    }

    var state: [String: Any] { preferences.state() }
    func prepare() { _ = preferences.state(prepare: true) }
    var isRunning: Bool { job?.completed == false }

    func shutdown() async {
        reset()
        guard isRunning else { return }
        await withCheckedContinuation { cleanupWaiters.append($0) }
    }

    func reset() {
        generation += 1
        selections.removeAll()
        job?.transport.cancel()
        // Retain the owned transport until it reports complete cleanup.
    }

    func perform(operation: String, params: [String: Any]) async throws -> [String: Any] {
        switch operation {
        case "state":
            try keys(params, required: [])
            return preferences.state(prepare: true)
        case "choose-executable":
            try keys(params, required: ["runtime"])
            let runtime = try runtime(params)
            guard job?.completed != false, !picking else { throw Failure.busy }
            picking = true; defer { picking = false }
            let current = generation
            guard let chosen = await picker(runtime) else { return ["cancelled": true] }
            guard current == generation else { throw Failure.stale }
            let validated = try RuntimeSetupExecutable.validate(chosen)
            let url = validated.url
            let identity = try RuntimeSetupExecutable.validate(url)
            let handle = UUID().uuidString
            selections[handle] = Selection(runtime: runtime, url: url, identity: identity)
            return ["selection_handle": handle, "display_name": url.path]
        case "status", "start":
            let isStatus = operation == "status"
            try keys(params, required: isStatus ? ["runtime", "request_id"] : ["runtime", "request_id", "action"], optional: ["selection_handle"])
            let runtime = try runtime(params)
            let requestID = try identifier(params, "request_id")
            let action: RuntimeSetupAction
            if isStatus { action = .status }
            else {
                guard let raw = params["action"] as? String, let value = RuntimeSetupAction(rawValue: raw), value != .status else { throw Failure.invalid }
                action = value
            }
            if action == .install && params["selection_handle"] != nil { throw Failure.invalid }
            let executable = action == .install ? nil : try selected(params, runtime: runtime)
            if let existing = job, existing.requestID == requestID {
                guard existing.generation == generation, existing.runtime == runtime,
                      existing.action == action, existing.executable == executable else { throw Failure.stale }
                if !isStatus { return existing.response }
                if existing.completed, case .status(let status) = existing.result { return Self.status(status) }
                throw Failure.busy
            }
            guard job?.completed != false, !picking else { throw Failure.busy }
            guard !usedRequests.contains(requestID.lowercased()), usedRequests.count < 10000 else { throw Failure.stale }
            usedRequests.insert(requestID.lowercased())
            let next = makeJob(runtime: runtime, action: action, executable: executable, requestID: requestID)
            job = next
            if isStatus {
                return try await withCheckedThrowingContinuation { continuation in
                    next.finish = { event in
                        if case .status(let result) = event { continuation.resume(returning: Self.status(result)) }
                        else if case .error(.cancelled) = event { continuation.resume(throwing: Failure.cancelled) }
                        else { continuation.resume(throwing: Failure.unavailable) }
                    }
                    start(next)
                }
            }
            start(next)
            return next.response
        case "input", "resize", "cancel":
            let active: Job
            if operation == "cancel", params["request_id"] != nil {
                try keys(params, required: ["request_id"])
                guard let value = job, value.requestID == (try identifier(params, "request_id")), value.generation == generation else { throw Failure.stale }
                active = value
            } else {
                try keys(params, required: operation == "input" ? ["job_id", "data"] : operation == "resize" ? ["job_id", "cols", "rows"] : ["job_id"])
                guard let value = job, value.id == (try identifier(params, "job_id")), value.generation == generation else { throw Failure.stale }
                active = value
            }
            if operation == "cancel" { if !active.completed { active.transport.cancel() }; return ["cancelling": !active.completed] }
            guard !active.completed, active.action != .status else { throw Failure.stale }
            if operation == "input" {
                guard let encoded = params["data"] as? String, encoded.utf8.count <= 45000,
                      let bytes = Data(base64Encoded: encoded), bytes.base64EncodedString() == encoded else { throw Failure.invalid }
                guard bytes.count <= 32768 else { throw Failure.invalid }
                try active.transport.send(.input(bytes))
            } else {
                guard let cols = Self.integer(params["cols"]), let rows = Self.integer(params["rows"]), rows <= 300 else { throw Failure.invalid }
                try active.transport.send(.resize(columns: cols, rows: rows))
            }
            return ["accepted": true]
        case "complete":
            try keys(params, required: ["skipped"], optional: ["agent", "command", "selection_handle"])
            guard Self.boolean(params["skipped"]) != nil else { throw Failure.invalid }
            guard job?.completed != false, !picking else { throw Failure.busy }
            var agent: String?, command: String?, executable: URL?
            if params["skipped"] as? Bool != true, let value = params["agent"] as? String {
                guard RuntimeSetupPreferences.agents.contains(value) else { throw Failure.invalid }
                agent = value
                if value == "custom" {
                    guard params["selection_handle"] == nil, let text = params["command"] as? String else { throw Failure.invalid }
                    command = text.isEmpty ? "zsh" : text
                } else {
                    guard params["command"] == nil else { throw Failure.invalid }
                    let previous = preferences.state()
                    command = previous["default_agent"] as? String == value
                        ? previous["default_command"] as? String ?? value : value
                    if params["selection_handle"] != nil {
                        guard let runtime = RuntimeSetupRuntime(rawValue: value == "claude" ? "claude-code" : value), let url = try selected(params, runtime: runtime) else { throw Failure.invalid }
                        executable = url
                        command = RuntimeSetupPreferences.quote(url.path)
                    }
                }
            } else if params["skipped"] as? Bool != true,
                      params["agent"] != nil || params["command"] != nil || params["selection_handle"] != nil { throw Failure.invalid }
            let result = try preferences.complete(agent: agent, command: command, executable: executable)
            onPreferences?(result)
            return result
        default: throw Failure.invalid
        }
    }

    private func makeJob(runtime: RuntimeSetupRuntime, action: RuntimeSetupAction,
                         executable: URL?, requestID: String) -> Job {
        let next = Job(requestID: requestID, runtime: runtime, action: action, executable: executable,
                       generation: generation, transport: factory(runtime, action, executable))
        next.transport.onEvent = { [weak self, weak next] event in
            guard let self, let next else { return }
            switch event {
            case .status, .exited, .error: next.result = event
            case .ready: self.emit(event, job: next)
            case .output: break
            }
        }
        next.transport.onOutput = { [weak self, weak next] bytes in
            guard let self, let next, self.job === next, next.generation == self.generation, !next.completed else { return }
            await self.onOutput?(next.response.merging(["protocol": 1, "type": "output", "data": bytes.base64EncodedString()]) { _, new in new })
        }
        next.transport.onEnd = { [weak self, weak next] in
            guard let self, let next, !next.completed else { return }
            next.completed = true
            let waiters = self.cleanupWaiters; self.cleanupWaiters.removeAll()
            waiters.forEach { $0.resume() }
            let event = next.result ?? .error(.transportUnavailable)
            let finish = next.finish; next.finish = nil
            if next.generation == self.generation { finish?(event); self.emit(event, job: next) }
            else { finish?(.error(.cancelled)) }
        }
        return next
    }

    private func start(_ next: Job) {
        Task {
            do { try await next.transport.start() }
            catch { next.transport.onEvent?(.error(.setupUnavailable)); next.transport.onEnd?() }
        }
    }

    private func emit(_ event: RuntimeSetupEvent, job next: Job) {
        guard job === next, next.generation == generation, next.action != .status else { return }
        var value = next.response
        value["protocol"] = 1
        switch event {
        case .ready(let runtime, let action): value.merge(["type": "ready", "runtime": runtime.rawValue, "action": action.rawValue]) { _, new in new }
        case .exited(let code, let cancelled): value.merge(["type": "exited", "exit_code": code, "cancelled": cancelled]) { _, new in new }
        case .error(let reason): value.merge(["type": "error", "reason": reason.rawValue]) { _, new in new }
        default: return
        }
        onEvent?(value)
    }

    static func status(_ status: RuntimeSetupStatus) -> [String: Any] {
        var result: [String: Any] = ["protocol": 1, "type": "status", "runtime": status.runtime.rawValue, "installed": status.installed,
            "version": status.version as Any? ?? NSNull(), "probe": status.probe.rawValue,
            "authentication": status.authentication.rawValue, "credential_method": status.credentialMethod.rawValue,
            "environment_api_key_configured": status.environmentAPIKeyConfigured]
        if let reason = status.reason { result["reason"] = reason.rawValue }
        return result
    }

    private func selected(_ params: [String: Any], runtime: RuntimeSetupRuntime) throws -> URL? {
        guard params["selection_handle"] != nil else {
            let agent = runtime == .claudeCode ? "claude" : "codex"
            guard let url = preferences.selectedExecutable(agent: agent) else {
                guard !preferences.hasSelectedExecutable(agent: agent) else { throw Failure.invalid }
                return nil
            }
            _ = try RuntimeSetupExecutable.validate(url)
            return url
        }
        let handle = try identifier(params, "selection_handle")
        guard let selection = selections[handle], selection.runtime == runtime,
              try RuntimeSetupExecutable.validate(selection.url) == selection.identity else { throw Failure.stale }
        return selection.url
    }
    private func runtime(_ params: [String: Any]) throws -> RuntimeSetupRuntime {
        guard let text = params["runtime"] as? String, let runtime = RuntimeSetupRuntime(rawValue: text) else { throw Failure.invalid }
        return runtime
    }
    private func identifier(_ params: [String: Any], _ key: String) throws -> String {
        guard let value = params[key] as? String, UUID(uuidString: value) != nil else { throw Failure.invalid }
        return value
    }
    private func keys(_ params: [String: Any], required: Set<String>, optional: Set<String> = []) throws {
        let actual = Set(params.keys)
        guard required.isSubset(of: actual), actual.isSubset(of: required.union(optional)) else { throw Failure.invalid }
    }
    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.rounded() == number.doubleValue, number.doubleValue >= 1,
              number.doubleValue <= 500 else { return nil }
        return number.intValue
    }
    private static func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }
}
