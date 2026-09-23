import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base } from "./helpers";

// Drives the REAL terminal in a REAL browser: launches PTY sessions, opens the
// terminal tab, types into xterm, and switches between agents. Catches the
// browser-only bugs (input focus, WS wiring, pane not re-keying on select) that
// curl-level tests miss.

async function launchCat(name: string): Promise<string> {
  // Print a READY banner, then `cat` (which echoes stdin back through the PTY).
  // The banner lets the test wait until the terminal is connected before typing,
  // so it isn't racing a not-yet-attached WS.
  const r = await apiPost("/sessions/launch", {
    command: "sh -c 'echo READY_CAT; exec cat'",
    cwd: "/tmp",
    name,
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);
  return r.body.session_key as string;
}

// Terminals for every PTY agent stay mounted; only the selected slot is shown.
// Scope assertions to the VISIBLE slot.
function visibleRows(page: import("@playwright/test").Page) {
  return page.locator(".rd-terminal-slot:visible .xterm-rows");
}

async function waitTerminalReady(page: import("@playwright/test").Page) {
  await expect(page.locator(".rd-terminal-slot:visible .xterm")).toBeVisible({
    timeout: 10_000,
  });
  await expect(visibleRows(page)).toContainText("READY_CAT", {
    timeout: 8_000,
  });
}

test("terminal: typing reaches the agent and echoes back", async ({ page }) => {
  await launchCat("cat-A");
  await page.goto(base());

  // The agent row appears; select it.
  const row = page.locator(".rd-row-name", { hasText: "cat-A" });
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.click();

  // Wait until the terminal is connected (READY banner rendered) before typing.
  await waitTerminalReady(page);

  // Type WITHOUT an explicit terminal click first — selecting the agent should
  // leave the terminal focused so you can type immediately (the real flow).
  await page.keyboard.type("HELLO_DUCKTERM");
  await page.keyboard.press("Enter");

  await expect(visibleRows(page)).toContainText(
    "HELLO_DUCKTERM",
    { timeout: 5_000 },
  );
});

test("terminal: switching agents shows the other agent's terminal", async ({
  page,
}) => {
  await launchCat("cat-one");
  await launchCat("cat-two");
  await page.goto(base());

  // Select the first, type a unique marker so its buffer is identifiable.
  await page.locator(".rd-row-name", { hasText: "cat-one" }).click();
  await waitTerminalReady(page);
  await page.keyboard.type("MARKER_ONE");
  await page.keyboard.press("Enter");
  await expect(visibleRows(page)).toContainText(
    "MARKER_ONE",
    { timeout: 5_000 },
  );

  // Switch to the second agent. Its terminal must NOT show the first's marker
  // (i.e. the pane actually re-mounted for the new session).
  await page.locator(".rd-row-name", { hasText: "cat-two" }).click();
  await waitTerminalReady(page);
  await page.keyboard.type("MARKER_TWO");
  await page.keyboard.press("Enter");
  await expect(visibleRows(page)).toContainText(
    "MARKER_TWO",
    { timeout: 5_000 },
  );
  await expect(visibleRows(page)).not.toContainText(
    "MARKER_ONE",
  );
});

test("terminal: Shift+Enter sends a newline, not a submit", async ({ page }) => {
  await launchCat("cat-NL");
  await page.goto(base());
  const row = page.locator(".rd-row-name", { hasText: "cat-NL" });
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.click();
  await waitTerminalReady(page);

  // cat runs in canonical mode: an LF completes the line, so cat echoes AAA a
  // second time. Two AAAs = the newline byte reached the agent; had Shift+Enter
  // sent nothing, AAA would appear exactly once with BBB glued to it.
  await page.keyboard.type("AAA");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("BBB");

  await expect(visibleRows(page)).toContainText("BBB", { timeout: 5_000 });
  const joined = (await visibleRows(page).allTextContents()).join("\n");
  expect(joined.split("AAA").length - 1).toBe(2);
});


test("terminal: attach lands at the bottom and later output preserves scrollback reading", async ({ page }) => {
  const launched = await apiPost("/sessions/launch", {
    command: "sh -c 'i=0; while [ \"$i\" -lt 250 ]; do echo HISTORY_$i; i=$((i+1)); done; echo ATTACH_LAST_LINE; exec cat'",
    cwd: "/tmp", name: "scroll-review", in_terminal: false, test: true,
  });
  expect(launched.status).toBe(200);
  const key = launched.body.session_key as string;
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "scroll-review" }).click();
    await expect(visibleRows(page)).toContainText("ATTACH_LAST_LINE");
    const viewport = page.locator(".rd-terminal-slot:visible .xterm-viewport");
    await expect.poll(() => viewport.evaluate((el) => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(3);
    await expect.poll(() => viewport.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeGreaterThan(500);
    await page.locator(".rd-terminal-slot:visible .xterm-screen").hover();
    await page.mouse.wheel(0, -1500);
    await expect.poll(() => viewport.evaluate((el) => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeGreaterThan(300);
    const before = await viewport.evaluate((el) => el.scrollTop);
    // Server-side input produces real output without a browser keystroke,
    // which xterm intentionally treats as a request to return to the prompt.
    const height = await viewport.evaluate((el) => el.scrollHeight);
    expect((await apiPost(`/sessions/${key}/input`, { text: "LATER_OUTPUT\n" })).status).toBe(200);
    await expect.poll(() => viewport.evaluate((el) => el.scrollHeight)).toBeGreaterThan(height);
    expect(Math.abs(await viewport.evaluate((el) => el.scrollTop) - before)).toBeLessThan(3);
    await page.setViewportSize({ width: 1250, height: 760 });
    await expect.poll(() => viewport.evaluate((el) => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeGreaterThan(300);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
