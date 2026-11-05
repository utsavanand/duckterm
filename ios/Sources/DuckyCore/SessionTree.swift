import Foundation

public extension AgentStatus {
    var isActive: Bool { !["terminated", "stopped", "interrupted", "archived", "merged"].contains(state) }
    var stateLabel: String {
        switch state {
        case "busy": return "Busy"
        case "waiting": return "Needs you"
        case "idle": return "Idle"
        case "terminated": return "Finished"
        case "stopped": return "Stopped"
        case "interrupted": return "Interrupted"
        default: return "Unknown"
        }
    }
    var runtimeLabel: String {
        ["claude-code": "Claude Code", "codex": "Codex", "generic": "Shell", "opencode": "OpenCode", "copilot": "Copilot"][runtime] ?? runtime
    }
}

public enum SessionFilter: String, CaseIterable {
    case all, active, waiting
    public var label: String { self == .all ? "All" : self == .active ? "Active" : "Needs you" }
    public func matches(_ session: AgentStatus) -> Bool {
        self == .all || (self == .active && session.isActive) || (self == .waiting && session.state == "waiting")
    }
}

public struct SessionTreeRow: Identifiable {
    public let path: String
    public let depth: Int
    public let session: AgentStatus?
    public let count: Int
    public let expanded: Bool
    public let hasAttention: Bool
    public var id: String { session.map { "session:" + $0.id } ?? "folder:" + path }
    public var label: String { path.isEmpty ? "Ungrouped" : String(path.split(separator: "/").last ?? "") }
}

/// Presentation-only projection. Filtering never mutates saved expansion choices.
public struct SessionTree {
    public let sessions: [AgentStatus]
    private let paths: Set<String>
    public init(sessions: [AgentStatus], folders: [String]) {
        self.sessions = sessions.filter { !["archived", "merged"].contains($0.state) }
        var paths = Set(folders.filter { !$0.isEmpty })
        for session in self.sessions where !session.folder.isEmpty { paths.insert(session.folder) }
        for path in paths {
            let pieces = path.split(separator: "/", omittingEmptySubsequences: false)
            if pieces.count > 1 {
                for i in 1..<pieces.count { paths.insert(pieces.prefix(i).joined(separator: "/")) }
            }
        }
        self.paths = paths
    }
    public func matching(filter: SessionFilter, query: String) -> [AgentStatus] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return sessions.filter { filter.matches($0) && (query.isEmpty || ($0.name + " " + $0.folder).localizedCaseInsensitiveContains(query)) }
    }
    public func rows(filter: SessionFilter, query: String, expanded: Set<String>) -> [SessionTreeRow] {
        let matches = matching(filter: filter, query: query)
        let searching = !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let autoExpand = searching || filter == .waiting
        var children: [String: [String]] = [:]
        var own: [String: [AgentStatus]] = [:]
        var counts: [String: Int] = [:], attention: Set<String> = []
        for path in paths {
            let parent = path.lastIndex(of: "/").map { String(path[..<$0]) } ?? ""
            children[parent, default: []].append(path)
        }
        for session in matches {
            own[session.folder, default: []].append(session)
            var path = session.folder
            while true {
                counts[path, default: 0] += 1
                if session.state == "waiting" { attention.insert(path) }
                guard let slash = path.lastIndex(of: "/") else { break }
                path = String(path[..<slash])
            }
        }
        func sorted(_ values: [String]) -> [String] {
            values.sorted { a, b in
                let comparison = a.localizedCaseInsensitiveCompare(b)
                return comparison == .orderedSame ? a < b : comparison == .orderedAscending
            }
        }
        // Iterative traversal bounds stack depth even for unusual nested folder names.
        var pending = sorted(children[""] ?? []).reversed().map { ($0, 0) }
        if own[""] != nil { pending.append(("", 0)) }
        var result: [SessionTreeRow] = []
        while let (path, depth) = pending.popLast() {
            let count = counts[path] ?? 0
            if (filter != .all || searching) && count == 0 { continue }
            let open = autoExpand ? count > 0 : expanded.contains(path)
            result.append(.init(path: path, depth: depth, session: nil, count: count, expanded: open, hasAttention: attention.contains(path)))
            guard open else { continue }
            let members = (own[path] ?? []).sorted {
                let comparison = $0.name.localizedCaseInsensitiveCompare($1.name)
                return comparison == .orderedSame ? $0.id < $1.id : comparison == .orderedAscending
            }
            result += members.map { .init(path: path, depth: depth, session: $0, count: 0, expanded: false, hasAttention: false) }
            if !path.isEmpty {
                pending += sorted(children[path] ?? []).reversed().map { ($0, depth + 1) }
            }
        }
        return result
    }
}

public struct FolderExpansionStore {
    private let defaults: UserDefaults
    public init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    private func key(_ hostID: String) -> String { "ducky.folder-expansion.v1." + hostID }
    public func load(hostID: String) -> Set<String> { Set(defaults.stringArray(forKey: key(hostID)) ?? []) }
    public func save(_ paths: Set<String>, hostID: String) { defaults.set(paths.sorted(), forKey: key(hostID)) }
    public func forget(hostID: String) { defaults.removeObject(forKey: key(hostID)) }
}
