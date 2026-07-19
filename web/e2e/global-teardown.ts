import { execFileSync } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default async function globalTeardown() {
  const statePath = join(tmpdir(), "rd-e2e-state.json");
  try {
    const { home, pid, tmuxSocket } = JSON.parse(
      readFileSync(statePath, "utf8"),
    );
    if (pid) {
      try {
        process.kill(pid);
      } catch {
        // already gone
      }
    }
    // Kill the run's ENTIRE tmux namespace — every fixture agent the specs
    // launched. The socket is private to this run (global-setup), so this
    // can't touch the user's real sessions. Leaked panes accumulate across
    // runs and make tmux slow enough to flake the terminal specs.
    if (tmuxSocket) {
      try {
        execFileSync("tmux", ["-L", tmuxSocket, "kill-server"]);
      } catch {
        // no server on the socket — nothing was launched
      }
    }
    if (home) rmSync(home, { recursive: true, force: true });
    rmSync(statePath, { force: true });
  } catch {
    // nothing to clean
  }
}
