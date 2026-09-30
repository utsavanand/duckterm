import type { ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** A successful public /sessions response alone does not identify our child. */
export async function waitForOwnedServer(
  proc: ChildProcess, home: string, base: string, timeout = 15_000,
): Promise<void> {
  let failure: Error | undefined;
  const onError = (error: Error) => { failure = error; };
  proc.on("error", onError);
  const checkChild = () => {
    if (failure) throw failure;
    if (proc.exitCode !== null || proc.signalCode !== null) {
      throw new Error(`Test server exited before readiness (${proc.exitCode ?? proc.signalCode})`);
    }
  };
  const deadline = Date.now() + timeout;
  try {
    while (Date.now() < deadline) {
      checkChild();
      let token: string | undefined;
      try { token = readFileSync(join(home, "token"), "utf8").trim(); } catch { /* starting */ }
      if (token) {
        try {
          const response = await fetch(base, { signal: AbortSignal.timeout(Math.min(1000, Math.max(1, deadline - Date.now()))) });
          const html = await response.text();
          checkChild();
          if (response.ok && html.includes(`<meta name="duckterm-token" content="${token}">`)) return;
        } catch (error) {
          checkChild();
          // Connection refused or a bounded request timeout while starting.
          if (!(error instanceof Error)) throw error;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    checkChild();
    throw new Error("Test server readiness timed out: private token identity was not verified");
  } finally {
    proc.off("error", onError);
  }
}
