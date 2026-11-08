import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPost, base } from "./helpers";

for (const runtime of ["codex", "claude-code"]) {
  test(`Compact submits a slash command to the terminal (${runtime})`, async ({ page }) => {
    const dir = mkdtempSync(join(tmpdir(), "duckterm-compact-test-"));
    const script = join(dir, "agent.py");
    // A real PTY reader distinguishes an Enter key from an embedded newline.
    writeFileSync(script, `import os, tty\ntty.setraw(0)\nprint('READY', flush=True)\nbuf=b''\nwhile True:\n b=os.read(0,1)\n if b == b'\\r':\n  text=buf.replace(b'\\x1b[200~',b'').replace(b'\\x1b[201~',b'')\n  print('\\r\\nCOMMAND_SUBMITTED: '+repr(text)+'\\r\\n', flush=True)\n  buf=b''\n else: buf+=b\n`);
    let key = "";
    try {
      const launch = await apiPost("/sessions/launch", { command: `python3 -u ${script}`, cwd: dir, name: `compact-proof-${runtime}`, runtime, test: true, in_terminal: false });
      expect(launch.status).toBe(200); key = String(launch.body.session_key);
      await page.goto(base());
      await page.locator(".rd-row-name", { hasText: `compact-proof-${runtime}` }).click();
      const terminal = page.locator(".rd-terminal-slot:visible .xterm-rows");
      await expect(terminal).toContainText("READY");
      const showPanel = page.getByRole("button", { name: "Show Context panel", exact: true });
      if (await showPanel.isVisible()) await showPanel.click();
      const context = page.locator("details", { has: page.locator("summary", { hasText: "Active context" }) });
      if (await context.getAttribute("open") === null) await context.locator("summary").click();
      await context.getByRole("button", { name: "Compact", exact: true }).click();
      await expect(terminal).toContainText("COMMAND_SUBMITTED: b'/compact'");
      await expect(terminal).not.toContainText("COMMAND_SUBMITTED: b'/compact\\n'");
    } finally {
      if (key) await apiDelete(`/sessions/${key}`);
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
