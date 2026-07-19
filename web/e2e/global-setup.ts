import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

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
  const home = mkdtempSync(join(tmpdir(), "rd-e2e-"));

  // A deterministic stand-in for the LLM backend: the observation-loop spec
  // asserts these exact rules come back as AGENTS.md suggestions. (It also
  // becomes the checkpoint summarizer, which no spec asserts on.)
  const fakeLlm = join(home, "fake-llm.sh");
  writeFileSync(
    fakeLlm,
    "#!/bin/sh\nprintf -- '- Use rg, not grep\\n- No emoji in commit messages\\n'\n",
    { mode: 0o755 },
  );

  // A private tmux namespace for this run's fixture agents, swept wholesale
  // in global-teardown — never the user's real duckterm socket.
  const tmuxSocket = `rd-e2e-${process.pid}`;

  const proc = spawn(
    "python",
    ["-m", "duckterm.cli", "serve", "--port", PORT],
    {
      cwd: REPO,
      env: {
        ...process.env,
        DUCKTERM_HOME: home,
        DUCKTERM_SUMMARIZER_CMD: fakeLlm,
        DUCKTERM_NO_TERMINAL: "1",
        DUCKTERM_TMUX_SOCKET: tmuxSocket,
        PYTHONPATH: join(REPO, "src"),
      },
      stdio: "inherit",
      detached: false,
    },
  );

  // Wait for the server to answer before tests run.
  const base = `http://127.0.0.1:${PORT}`;
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/sessions`);
      if (res.ok) break;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 200));
  }

  writeFileSync(
    join(tmpdir(), "rd-e2e-state.json"),
    JSON.stringify({ home, pid: proc.pid, port: PORT, tmuxSocket }),
  );
}
