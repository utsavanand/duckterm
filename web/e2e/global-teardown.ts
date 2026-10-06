import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { readOwnedState, statePath } from "./run-state";

function alive(pid: number): boolean {
  try { process.kill(pid, 0); return true; }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
}

async function until(check: () => boolean, timeout: number): Promise<boolean> {
  const deadline = Date.now() + timeout;
  while (!check()) {
    if (Date.now() >= deadline) return false;
    await delay(25);
  }
  return true;
}

async function stopServer(pid: number) {
  if (!alive(pid)) return;
  try { process.kill(pid, "SIGTERM"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
  if (await until(() => !alive(pid), 10_000)) return;
  try { process.kill(pid, "SIGKILL"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
  if (!await until(() => !alive(pid), 5_000)) {
    throw new Error("Owned browser server did not exit; cleanup deadline exceeded");
  }
}

function tmuxGone(socket: string): boolean {
  try {
    execFileSync("tmux", ["-L", socket, "list-sessions"], { stdio: "ignore", timeout: 1000 });
    return false;
  } catch (error) {
    const failure = error as NodeJS.ErrnoException & { status?: number };
    if (failure.code === "ENOENT" || failure.status === 1) return true;
    throw error;
  }
}

async function stopTmux(socket: string, home: string) {
  const panes = join(home, "panes");
  const completions = existsSync(panes) ? readdirSync(panes)
    .filter(name => name.endsWith(".writer"))
    .flatMap(name => {
      const generation = readFileSync(join(panes, name), "utf8");
      return /^[a-f0-9]{32}$/.test(generation)
        ? [join(panes, name.slice(0, -7) + "." + generation + ".done")] : [];
    }) : [];
  try {
    execFileSync("tmux", ["-L", socket, "kill-server"], { stdio: "ignore", timeout: 5000 });
  } catch (error) {
    if (!tmuxGone(socket)) throw error;
  }
  if (!await until(() => tmuxGone(socket), 5000)) {
    throw new Error("Owned tmux server did not exit; cleanup deadline exceeded");
  }
  // Socket removal can precede pipe writers' final atomic EOF marker writes.
  if (!await until(() => completions.every(path => existsSync(path)), 5000)) {
    throw new Error("Owned pane writers did not finish; cleanup deadline exceeded");
  }
}

export default async function globalTeardown() {
  const path = statePath();
  let owned;
  try { owned = readOwnedState(); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  const { home, pid, tmuxSocket } = owned;
  if (pid) await stopServer(pid);
  if (tmuxSocket) await stopTmux(tmuxSocket, home);
  if (home) rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  rmSync(path, { force: true });
}
