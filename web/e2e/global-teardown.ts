import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { readOwnedState, statePath } from "./run-state";

export default async function globalTeardown() {
  const path = statePath();
  let owned;
  try {
    owned = readOwnedState();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  const { home, pid, tmuxSocket } = owned;
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
  rmSync(path, { force: true });
}
