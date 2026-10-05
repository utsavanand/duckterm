import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { waitForOwnedServer } from "./server-readiness";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Boot a real `duckterm serve` for the E2E run, isolated from the dev's state:
//   - DUCKTERM_HOME -> a throwaway temp dir (own db, own token)
//   - DUCKTERM_SUMMARIZER=off -> never shells out to a real agent
//   - DUCKTERM_NO_TERMINAL=1 -> launch never opens a real terminal window,
//     so test runs don't leave orphan terminal tabs behind
// The built dashboard is served by the Python server, which injects the auth
// token into the HTML, so Playwright loading the page is authenticated like a
// real user. Writes the home dir + pid to a state file for teardown/tests.

const PORT = process.env.RD_TEST_PORT || "4399";
const REPO = join(__dirname, "..", "..");

export default async function globalSetup() {
  const runRoot = process.env.RD_TEST_RUN_ROOT;
  if (!runRoot || !process.env.RD_TEST_TMUX_SOCKET || !process.env.RD_TEST_STATE_FILE) {
    throw new Error("Run browser tests with npm run e2e (isolated environment required)");
  }
  // Build the dashboard this run is about to test. Without this, the server
  // serves whatever web/dist happens to hold — we spent an afternoon
  // "testing" a bundle three releases old.
  await new Promise<void>((resolve, reject) => {
    const build = spawn("npm", ["run", "build", "--silent"], {
      cwd: join(__dirname, ".."),
      stdio: "inherit",
    });
    build.on("exit", (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`dashboard build failed (${code})`)),
    );
  });

  const home = mkdtempSync(join(runRoot, "state-"));
  // Backup UI tests must never collect the developer's actual agent transcripts.
  const testClaudeRoot = join(home, "test-claude");
  const testCodexRoot = join(home, "test-codex");
  mkdirSync(join(testClaudeRoot, "projects"), { recursive: true });
  mkdirSync(join(testCodexRoot, "sessions"), { recursive: true });

  // A deterministic stand-in for the LLM backend: the observation-loop spec
  // asserts these exact rules come back as AGENTS.md suggestions. (It also
  // becomes the checkpoint summarizer, which no spec asserts on.)
  const fakeLlm = join(home, "fake-llm.sh");
  writeFileSync(
    fakeLlm,
    "#!/bin/sh\nprintf -- '- [all] [1] Use rg, not grep\\n- [all] [1] No emoji in commit messages\\n'\n",
    { mode: 0o755 },
  );

  // A private tmux namespace for this run's fixture agents, swept wholesale
  // in global-teardown — never the user's real duckterm socket.
  const tmuxSocket = process.env.RD_TEST_TMUX_SOCKET!;

  const proc = spawn(
    "python",
    ["-m", "duckterm.cli", "serve", "--port", PORT],
    {
      cwd: REPO,
      env: {
        ...process.env,
        DUCKTERM_HOME: home,
        DUCKTERM_RELEASE_CHECK: "off",
        CLAUDE_CONFIG_DIR: testClaudeRoot,
        CODEX_HOME: testCodexRoot,
        DUCKTERM_SUMMARIZER_CMD: fakeLlm,
        DUCKTERM_NO_TERMINAL: "1",
        DUCKTERM_NO_BROWSER: "1",
        DUCKTERM_TMUX_SOCKET: tmuxSocket,
        PYTHONPATH: join(REPO, "src"),
      },
      stdio: "inherit",
      detached: false,
    },
  );

  // Setup failures do not run global teardown. Reap only our child and home.
  try {
    await waitForOwnedServer(proc, home, `http://127.0.0.1:${PORT}`);
    writeFileSync(
      process.env.RD_TEST_STATE_FILE || join(tmpdir(), "rd-e2e-state.json"),
      JSON.stringify({ home, pid: proc.pid, port: PORT, tmuxSocket }),
    );
  } catch (error) {
    if (proc.pid && proc.exitCode === null && proc.signalCode === null) {
      await new Promise<void>((resolve) => {
        proc.once("exit", () => resolve());
        proc.kill("SIGKILL");
      });
    }
    rmSync(home, { recursive: true, force: true });
    throw error;
  }
}
