import Foundation

/// Owner setup broker. Pairing tickets and service capabilities never enter the web view.
@MainActor
final class CollaborationSetup {
    struct Plan {
        let id: String
        let created: Date
        let coordinator: RemoteHost
        let source: String
        let sourceName: String
        let connection: String
        var snapshots: [String: [[String: Any]]]
        let groups: [String: [String: String]]
        var invitation: [String: Any]?
    }
    private var plan: Plan?
    private let http = BoundedSessionHTTP()

    func call(base: URL, api: LaunchDestination, route: String, method: String = "POST", body: [String: Any] = [:]) async throws -> [String: Any] {
        guard base.scheme == "http", base.host == "127.0.0.1",
              route.hasPrefix("/collaboration/") else { throw LaunchDestination.Failure.message("Invalid collaboration destination") }
        var request = URLRequest(url: base.appendingPathComponent(String(route.dropFirst())))
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue(try await api.token(base: base, attempts: 2), forHTTPHeaderField: "X-Duckterm-Token")
        if method != "GET" {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await http.perform(request)
        guard let response = response as? HTTPURLResponse,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw LaunchDestination.Failure.message("Invalid collaboration response")
        }
        guard (200..<300).contains(response.statusCode) else {
            throw LaunchDestination.Failure.message(result["error"] as? String ?? "Update DuckTerm on both computers before connecting")
        }
        return result
    }

    func preview(coordinator: RemoteHost, source: String, sourceName: String, connection: String,
                 bases: [String: URL], groups: [String: [String: String]], api: LaunchDestination) async throws -> [String: Any] {
        guard source != coordinator.target, bases[source] != nil, bases[coordinator.target] != nil else {
            throw LaunchDestination.Failure.message("Choose two different computers")
        }
        _ = try RemoteHost(name: "Coordinator", target: connection, remotePort: coordinator.remotePort)
        var snapshots: [String: [[String: Any]]] = [:]
        var visible: [[String: Any]] = []
        var migrationGroups = groups
        for host in [coordinator.target, source] {
            let response = try await call(base: bases[host]!, api: api, route: "/collaboration/preview", method: "GET")
            guard response["protocol"] as? Int == 1, let cards = response["sessions"] as? [[String: Any]] else {
                throw LaunchDestination.Failure.message("Update DuckTerm on both computers before connecting")
            }
            snapshots[host] = cards
            if response["enabled"] as? Bool == true { migrationGroups[host] = nil }
            for card in cards {
                guard let key = card["session_key"] as? String else { continue }
                visible.append(["host": host, "key": key, "name": card["name"] ?? key,
                                "folder": migrationGroups[host]?[key] ?? card["folder"] ?? ""])
            }
        }
        let id = UUID().uuidString
        plan = Plan(id: id, created: Date(), coordinator: coordinator, source: source,
                    sourceName: sourceName, connection: connection, snapshots: snapshots, groups: migrationGroups)
        return ["plan_id": id, "sessions": visible, "coordinator": coordinator.name,
                "source": sourceName, "background_available": false]
    }

    func confirm(id: String, bases: [String: URL], api: LaunchDestination,
                 move: (String, String, String) async throws -> Void) async throws -> [String: Any] {
        guard var current = plan, current.id == id, Date().timeIntervalSince(current.created) < 300,
              let hubBase = bases[current.coordinator.target], let sourceBase = bases[current.source] else {
            throw LaunchDestination.Failure.message("Review the current folder arrangement again before connecting")
        }
        // Recheck the owner-visible scope, not mutable activity text.
        for host in [current.coordinator.target, current.source] {
            let response = try await call(base: bases[host]!, api: api, route: "/collaboration/preview", method: "GET")
            func membership(_ cards: [[String: Any]]) -> [String: String] {
                Dictionary(uniqueKeysWithValues: cards.compactMap { c in
                    guard let key = c["session_key"] as? String else { return nil }
                    return (key, "\(c["folder"] ?? "")|\(c["root"] ?? "")")
                })
            }
            guard membership(response["sessions"] as? [[String: Any]] ?? []) == membership(current.snapshots[host] ?? []) else {
                throw LaunchDestination.Failure.message("Sessions or folders changed. Review the setup again.")
            }
        }
        _ = try await call(base: hubBase, api: api, route: "/collaboration/initialize", body: ["name": current.coordinator.name])
        let status = try await call(base: hubBase, api: api, route: "/collaboration/status", method: "GET")
        guard let hubID = status["computer_id"] as? String else { throw LaunchDestination.Failure.message("Coordinator identity unavailable") }
        var bindings: [[String: String]] = []
        for host in [current.coordinator.target, current.source] {
            var roots = Set<String>()
            for card in current.snapshots[host] ?? [] {
                guard let key = card["session_key"] as? String else { continue }
                let folder = current.groups[host]?[key] ?? (card["folder"] as? String ?? "")
                if !folder.isEmpty { roots.insert(String(folder.split(separator: "/")[0])) }
                if folder != (card["folder"] as? String ?? "") { try await move(host, key, folder) }
            }
            for root in roots.sorted() {
                let folder = try await call(base: hubBase, api: api, route: "/collaboration/folder", body: ["path": root])
                guard let folderID = folder["folder_id"] as? String else { continue }
                if host == current.coordinator.target {
                    _ = try await call(base: hubBase, api: api, route: "/collaboration/bind", body: ["computer_id": hubID, "local_path": root, "canonical_path": root])
                } else { bindings.append(["local_path": root, "folder_id": folderID]) }
            }
            let refreshed = try await call(base: bases[host]!, api: api, route: "/collaboration/preview", method: "GET")
            current.snapshots[host] = refreshed["sessions"] as? [[String: Any]] ?? []
            plan = current
        }
        if current.invitation == nil {
            current.invitation = try await call(base: hubBase, api: api, route: "/collaboration/invite", body: ["name": current.sourceName, "bindings": bindings])
            plan = current
        }
        let result = try await call(base: sourceBase, api: api, route: "/collaboration/connect", body: [
            "invitation": current.invitation!, "connection": ["ssh_target": current.connection, "remote_port": current.coordinator.remotePort]
        ])
        plan = nil
        return result
    }
}
