import { sessionMenu } from "./helpers";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, archiveRequests, base, findSession, seedSession } from "./helpers";

// Playwright records afterEach failures separately; a finally cleanup error must
// never replace the original assertion. Only keys created by this file are owned.
const cleanupKeys = new Set<string>();
test.afterEach(async () => {
  for (const key of cleanupKeys) {
    try {
      await expect.poll(async () => (await archiveRequests()).find(r => r.session_key === key),
        { timeout: 12000, message: "Owned test archive must settle before deletion" }).toBeUndefined();
      await apiDelete(`/sessions/${key}`);
    } finally { cleanupKeys.delete(key); }
  }
});

test("Archive Undo preserves the session and pending archive survives a viewer reload", async ({ page }) => {
  const key = await seedSession(`undo-${Date.now()}`, { name: "Undo reviewer", launched: true, runtime: "generic", test: true });
  cleanupKeys.add(key);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(base());
  const row = page.locator(".rd-row", { has: page.getByText("Undo reviewer", { exact: true }) });
  await row.locator(".rd-row-name").click();
  const archive = (await sessionMenu(page)).getByRole("menuitem", { name: "Archive", exact: true });
  await sessionMenu(page);
  await archive.click();
  await expect(row).toHaveCount(0);
  const undo = page.getByRole("button", { name: "Undo", exact: true });
  await expect(undo).toBeFocused();
  expect((await findSession(s => s.session_key === key))?.state).not.toBe("archived");
  await page.screenshot({ path: "/tmp/archive-undo-implemented.png" });
  await page.keyboard.press("Enter");
  await expect(row).toBeVisible();
  await expect(page.locator(".rd-session-controls")).toContainText("Undo reviewer");
  // Reopen the menu after Undo restores the row.
  await sessionMenu(page);
  await archive.click();
  await expect(undo).toBeVisible();
  await page.reload();
  await expect(undo).toBeVisible();
  await undo.focus(); await page.keyboard.press("Escape");
  await expect(undo).toHaveCount(0);
  // Dismissing the notification is not cancellation; expiry still commits.
  await expect.poll(async () => (await findSession(s => s.session_key === key))?.state, { timeout: 12000 }).toBe("archived");
  await expect(row).toHaveCount(0);
  expect((await apiPost(`/sessions/${key}/resume`)).status).toBe(400);
});


test("Undo keeps the live terminal mounted and preserves an unsent draft", async ({ page }) => {
  const launched = await apiPost("/sessions/launch", { command: "cat", cwd: "/tmp", name: "Undo terminal", in_terminal: false, test: true });
  expect(launched.status).toBe(200);
  const key = String(launched.body.session_key);
  cleanupKeys.add(key);
  await page.goto(base());
  await page.getByText("Undo terminal", { exact: true }).first().click();
  const input = page.locator(".rd-terminal-slot:visible .xterm-helper-textarea");
  await input.focus();
  await page.keyboard.insertText("UNSENT_ARCHIVE_DRAFT");
  await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("UNSENT_ARCHIVE_DRAFT");
  const original = await input.elementHandle();
  await (await sessionMenu(page)).getByRole("menuitem", { name: "Archive", exact: true }).click();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  // The terminal intentionally stays mounted while Undo is in flight; its
  // visibility alone cannot prove that the server accepted cancellation.
  await expect.poll(async () => (await archiveRequests()).some(r => r.session_key === key)).toBe(false);
  await expect(input).toBeVisible();
  expect(await input.evaluate((node, saved) => node === saved, original)).toBe(true);
  await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("UNSENT_ARCHIVE_DRAFT");
});
