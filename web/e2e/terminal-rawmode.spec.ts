import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// A faithful test of typing into a RAW-MODE TUI (like claude), not `cat`. The
// program reads keystrokes char-by-char in raw mode and prints GOT:<char>, so we
// can confirm a browser keystroke actually reached the agent's stdin. Also logs
// console + WS state to surface browser-side failures.

// The program is written here so the spec is self-contained (it used to point
// at a hand-made /tmp/rawtui.py that didn't survive the machine it was born on).
const RAWTUI = `
import sys, tty

print("READY", flush=True)
tty.setraw(sys.stdin.fileno())
while True:
    c = sys.stdin.read(1)
    if not c:
        break
    sys.stdout.write("GOT:" + c + "\\r\\n")
    sys.stdout.flush()
`;

test("raw-mode TUI: keystrokes reach the agent from the browser", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));

  const script = join(tmpdir(), `rd-e2e-rawtui-${Date.now()}.py`);
  writeFileSync(script, RAWTUI);
  const r = await apiPost("/sessions/launch", {
    command: `python3 ${script}`,
    cwd: "/tmp",
    name: "rawtui",
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);

  await page.goto(base());
  await page.locator(".rd-row-name", { hasText: "rawtui" }).click();
  const term = page.locator(".rd-terminal-slot:visible .xterm");
  await expect(term).toBeVisible({ timeout: 10_000 });

  // The program prints READY once it's in raw mode.
  await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText(
    "READY",
    { timeout: 8_000 },
  );

  // Check the WS is actually OPEN from the browser's view.
  const wsOpen = await page.evaluate(() => {
    // @ts-expect-error test hook
    return window.__rdTermWsState ?? "unknown";
  });
  console.log(
    "WS state from browser:",
    wsOpen,
    "console errors:",
    consoleErrors,
  );

  // Type WITHOUT clicking the terminal first (the real flow).
  await page.keyboard.type("Z");
  await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText(
    "GOT:Z",
    { timeout: 5_000 },
  );
});
