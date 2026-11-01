import Foundation

/// The backend is installed inside the private interpreter's site-packages.
/// Its import isolation never changes the Python environment of user projects.
struct BundledRuntime: Sendable {
    let resources: URL

    var python: URL { resources.appendingPathComponent("python/bin/python3.13") }
    var cli: URL { resources.appendingPathComponent("bin/duckterm") }
    var tmux: URL { resources.appendingPathComponent("tmux/bin/tmux") }
    var jq: URL { resources.appendingPathComponent("jq/bin/jq") }

    func validate() throws {
        let files = FileManager.default
        let package = resources.appendingPathComponent("python/lib/python3.13/site-packages/duckterm")
        guard [python, cli, tmux, jq].allSatisfy({ files.isExecutableFile(atPath: $0.path) }),
              files.fileExists(atPath: package.appendingPathComponent("cli.py").path),
              files.fileExists(atPath: package.appendingPathComponent("dashboard/index.html").path) else {
            throw LaunchDestination.Failure.message(
                "Duckterm's bundled runtime is incomplete. Replace this app with a fresh download.")
        }
    }

    func environment(inheriting environment: [String: String]) -> [String: String] {
        var result = environment
        // This directory contains only duckterm, never python or pip.
        result["PATH"] = resources.appendingPathComponent("bin").path + ":" + (environment["PATH"] ?? "")
        result["DUCKTERM_BUNDLED_TMUX"] = tmux.path
        result["DUCKTERM_BUNDLED_CLI"] = cli.path
        result["DUCKTERM_BUNDLED_JQ"] = jq.path
        return result
    }
}
