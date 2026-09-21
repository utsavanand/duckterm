import Foundation

/// Copies verified chunks between two saved destinations. Provider tokens and
/// archive contents never cross the JavaScript bridge.
@MainActor
final class ProjectTransfer {
    private var paused = Set<String>()
    private var running = Set<String>()

    func pause(_ id: String) { paused.insert(id) }

    func copy(api: LaunchDestination, local: URL, remote: URL, params: [String: Any]) async throws -> Any {
        guard let id = params["id"] as? String, !running.contains(id),
              let destination = params["destination"] as? String else {
            throw LaunchDestination.Failure.message("Transfer already running or missing destination")
        }
        running.insert(id)
        paused.remove(id)
        defer { running.remove(id) }
        // Check recovery state first: a completed copy does not require rereading
        // the local project or overwriting an existing remote destination.
        if let state = try await api.perform(base: remote, operation: "transfer-status", params: ["id": id]) as? [String: Any],
           let stage = state["stage"] as? String, ["ready", "launching", "launched"].contains(stage) {
            return state
        }
        guard let prepared = try await api.perform(base: local, operation: "transfer-prepare", params: params) as? [String: Any],
              let size = prepared["bytes"] as? Int, let sha = prepared["sha256"] as? String else {
            throw LaunchDestination.Failure.message("Could not prepare the project snapshot")
        }
        guard var state = try await api.perform(base: remote, operation: "transfer-begin", params: ["id": id, "destination": destination, "bytes": size, "sha256": sha]) as? [String: Any] else {
            throw LaunchDestination.Failure.message("Invalid transfer response")
        }
        var offset = state["offset"] as? Int ?? 0
        while offset < size {
            guard !paused.contains(id), !Task.isCancelled else {
                throw LaunchDestination.Failure.message("Transfer paused. Retry resumes the saved snapshot; your local files are unchanged.")
            }
            guard let chunk = try await api.perform(base: local, operation: "transfer-chunk", params: ["id": id, "offset": offset]) as? [String: Any],
                  let data = chunk["data"] as? String, !data.isEmpty else {
                throw LaunchDestination.Failure.message("Snapshot ended before the transfer completed")
            }
            guard let received = try await api.perform(base: remote, operation: "transfer-receive", params: ["id": id, "offset": offset, "data": data]) as? [String: Any] else {
                throw LaunchDestination.Failure.message("Invalid chunk response")
            }
            state = received
            guard let next = state["offset"] as? Int, next > offset else {
                throw LaunchDestination.Failure.message("Transfer made no progress; check its status before retrying")
            }
            offset = next
        }
        guard !paused.contains(id) else { throw LaunchDestination.Failure.message("Transfer paused before verification; retry to continue") }
        return try await api.perform(base: remote, operation: "transfer-finish", params: ["id": id])
    }
}
