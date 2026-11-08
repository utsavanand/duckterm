import Foundation

/// Completion/defaults belong to the app profile. Harness credentials never do.
struct RuntimeSetupPreferences {
    let defaults: UserDefaults
    let key: String
    let newWorkspace: Bool

    init(home: URL, defaults: UserDefaults = .standard, newWorkspace: Bool) {
        self.defaults = defaults
        key = "agentSetup.v1." + home.standardizedFileURL.path
        self.newWorkspace = newWorkspace
    }

    func state(prepare: Bool = false) -> [String: Any] {
        if let value = defaults.object(forKey: key) {
            guard let record = value as? [String: Any], record["version"] as? Int == 1,
                  let completed = record["completed"] as? Bool else {
                // An unreadable record must not trap existing users in setup.
                return ["show_on_launch": false, "completed": true]
            }
            var result: [String: Any] = ["show_on_launch": !completed, "completed": completed]
            if let agent = record["default_agent"] as? String,
               let command = record["default_command"] as? String,
               Self.agents.contains(agent), Self.validCommand(command),
               (agent == "custom" || (record["default_executable"] == nil && command == agent)
                || selectedExecutable(agent: agent) != nil) {
                result["default_agent"] = agent; result["default_command"] = command
            }
            return result
        }
        if prepare && newWorkspace { defaults.set(["version": 1, "completed": false], forKey: key) }
        return ["show_on_launch": newWorkspace, "completed": !newWorkspace]
    }

    static let agents: Set<String> = ["claude", "codex", "copilot", "opencode", "custom"]
    static func validCommand(_ command: String) -> Bool {
        !command.trimmingCharacters(in: .whitespaces).isEmpty && command.utf8.count <= 16384
            && !command.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
    }

    func hasSelectedExecutable(agent: String) -> Bool {
        guard let record = defaults.dictionary(forKey: key) else { return false }
        return record["default_agent"] as? String == agent && record["default_executable"] != nil
    }

    func selectedExecutable(agent: String) -> URL? {
        guard let record = defaults.dictionary(forKey: key), record["version"] as? Int == 1,
              record["default_agent"] as? String == agent,
              ["claude", "codex"].contains(agent),
              let path = record["default_executable"] as? String, path.hasPrefix("/"),
              Self.validCommand(path), record["default_command"] as? String == Self.quote(path) else { return nil }
        return try? RuntimeSetupExecutable.validate(URL(fileURLWithPath: path)).url
    }

    static func quote(_ path: String) -> String { "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    func complete(agent: String? = nil, command: String? = nil, executable: URL? = nil) throws -> [String: Any] {
        var result = state()
        let oldExecutable = (result["default_agent"] as? String).flatMap { selectedExecutable(agent: $0) }
        if let agent, let command {
            guard Self.agents.contains(agent), Self.validCommand(command) else { throw RuntimeSetupProtocolError.invalidFrame }
            result["default_agent"] = agent; result["default_command"] = command
        } else if agent != nil || command != nil { throw RuntimeSetupProtocolError.invalidFrame }
        if let executable {
            guard let agent, ["claude", "codex"].contains(agent), executable.isFileURL,
                  command == Self.quote(executable.path) else { throw RuntimeSetupProtocolError.invalidFrame }
            let validated = try RuntimeSetupExecutable.validate(executable)
            result["default_command"] = Self.quote(validated.url.path)
            result["default_executable"] = validated.url.path
        } else if let oldExecutable, result["default_command"] as? String == Self.quote(oldExecutable.path) {
            result["default_executable"] = oldExecutable.path
        }
        if let agent = result["default_agent"] as? String,
           agent != "custom", result["default_executable"] == nil,
           result["default_command"] as? String != agent {
            throw RuntimeSetupProtocolError.invalidFrame
        }
        result.removeValue(forKey: "show_on_launch")
        result["completed"] = true
        result["version"] = 1
        defaults.set(result, forKey: key)
        return state()
    }
}
