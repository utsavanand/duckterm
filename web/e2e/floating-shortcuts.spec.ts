import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base } from "./helpers";

test("floating shortcuts preserve geometry, drafts and connections while sharing persisted pins", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1512, height: 982 });
  const keys: string[] = [], sizes: unknown[] = [];
  let connections = 0;
  page.on("websocket", socket => {
    if (!keys.some(key => socket.url().includes(`/sessions/${key}/terminal`))) return;
    connections++;
    socket.on("framesent", ({ payload }) => {
      try { const data = JSON.parse(String(payload)); if (data.resize) sizes.push(data.resize); } catch { /* Keystrokes. */ }
    });
  });
  try {
    for (const name of ["shortcut-ui", "shortcut-api", "shortcut-qa", "shortcut-extra"]) {
      const result = await apiPost("/sessions/launch", { command: `sh -c 'echo READY_${name}; exec cat'`, cwd: "/tmp", name, in_terminal: false, test: true });
      expect(result.status).toBe(200); keys.push(String(result.body.session_key));
    }
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "shortcut-ui" }).click();
    const rows = page.locator(".rd-terminal-slot:visible .xterm-rows");
    const slot = page.locator(".rd-terminal-slot:visible");
    const rail = page.getByRole("navigation", { name: "Pinned session shortcuts" });
    const width = async () => (await slot.boundingBox())!.width;
    await expect(rows).toContainText("READY_shortcut-ui");
    await expect.poll(() => sizes.length).toBeGreaterThan(0);
    const originalWidth = await width(), initialResizes = sizes.length, initialConnections = connections;
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.type("UNSENT_SHORTCUT_DRAFT");
    await rail.getByRole("button", { name: "Pin current session shortcut-ui" }).click();
    await expect(rail.getByRole("button", { name: "Open pinned shortcut-ui" })).toBeVisible();
    await expect(page.locator(".rd-agents").getByRole("button", { name: "Unpin shortcut-ui", exact: true })).toBeVisible();
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    const picker = page.getByRole("dialog", { name: "Pin sessions" });
    await picker.getByRole("searchbox").fill("shortcut-");
    await picker.getByRole("button", { name: "Pin shortcut-api in picker" }).click();
    await expect(picker.getByRole("button", { name: "Unpin shortcut-api in picker" })).toBeEnabled();
    await picker.getByRole("button", { name: "Pin shortcut-qa in picker" }).click();
    await expect(picker.getByRole("button", { name: "Pin shortcut-extra in picker" })).toBeDisabled();
    await page.screenshot({ path: "/tmp/floating-shortcuts-picker.png" });
    await page.keyboard.press("Escape");
    await expect(picker).toBeHidden();
    await expect(rail.getByRole("button", { name: "Choose sessions to pin" })).toBeFocused();
    expect(await width()).toBe(originalWidth); expect(sizes.length).toBe(initialResizes);
    const overlap = await slot.evaluate(element => {
      const term = element.querySelector(".xterm")!.getBoundingClientRect();
      const tabs = element.querySelectorAll(".rd-shortcut-tab");
      const a = tabs[0].getBoundingClientRect(), b = tabs[1].getBoundingClientRect();
      const gap = document.elementFromPoint(a.x + 15, (a.bottom + b.top) / 2);
      return { overlap: term.right - a.left, gapPasses: !!gap?.closest(".xterm") };
    });
    expect(overlap.overlap).toBeGreaterThan(100); expect(overlap.gapPasses).toBe(true);
    await rail.getByRole("button", { name: "Open pinned shortcut-api" }).click();
    await expect(rows).toContainText("READY_shortcut-api");
    await rail.getByRole("button", { name: "Open pinned shortcut-ui" }).click();
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT"); expect(connections).toBe(initialConnections + 1);
    await rail.getByRole("button", { name: "Unpin shortcut-ui from shortcuts" }).click();
    await expect(rail.getByRole("button", { name: "Pin current session shortcut-ui" })).toBeEnabled();
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("shortcut-ui");
    await rail.getByRole("button", { name: "Pin current session shortcut-ui" }).click();
    await expect(rail.getByRole("button", { name: "Open pinned shortcut-ui" })).toBeVisible();
    for (const theme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.screenshot({ path: `/tmp/floating-shortcuts-${theme}.png` });
    }
    await page.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
    await expect.poll(width).toBeGreaterThan(originalWidth);
    await expect(rail).toBeVisible();
    await expect(page.getByRole("button", { name: "Show Context panel", exact: true })).toBeVisible();
    await page.screenshot({ path: "/tmp/floating-shortcuts-collapsed.png" });
    await page.getByRole("button", { name: "Show Context panel", exact: true }).click();
    await expect.poll(width).toBe(originalWidth);
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT"); expect(connections).toBe(initialConnections + 1);
    await page.reload();
    await page.locator(".rd-row-name", { hasText: "shortcut-ui" }).click();
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "Focus · 3", exact: true })).toBeVisible();
    await expect(rows).toContainText("UNSENT_SHORTCUT_DRAFT");
    await page.setViewportSize({ width: 600, height: 900 });
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    await expect(picker).toBeVisible();
    const box = (await picker.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(600);
    expect(box.y + box.height).toBeLessThanOrEqual(900);
    await page.screenshot({ path: "/tmp/floating-shortcuts-narrow.png" });
  } finally {
    for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
  }
});
