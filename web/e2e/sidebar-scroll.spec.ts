import { expect, test } from "@playwright/test";
import { apiDelete, base, expandFolder, seedSession } from "./helpers";

test.use({ viewport: { width: 1280, height: 652 } });

test("opening a scrolled workspace preserves the second click for renaming", async ({ page }) => {
  const fillers = Array.from({ length: 18 }, (_, i) => `A scroll review ${i}`);
  const from = "Z scroll review", to = "Z renamed review", key = "scroll-rename-review";
  try {
    for (const [i, group] of fillers.entries()) await seedSession(`scroll-rename-filler-${i}`, { name: `Filler ${i}`, group });
    await seedSession(key, { name: "Rename review", group: from });
    await page.goto(base());
    await expandFolder(page, from);
    await expect(page.locator(`[data-session-key="${key}"]`)).toBeVisible();
    const header = page.locator(`[data-session-drop-group="${from}"] .rd-group-name`);
    await header.scrollIntoViewIfNeeded();
    expect(await page.locator(".rd-tree").evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    const dialog = page.waitForEvent("dialog", { timeout: 5000 }).then(d => d.accept(to));
    await header.dblclick();
    await dialog;
    await expect(page.getByRole("button", { name: `View interactions in ${to}`, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: `View interactions in ${from}`, exact: true })).toHaveCount(0);
    await expandFolder(page, to);
    await expect(page.locator(`[data-session-drop-group="${to}"]`).locator("..").locator(`[data-session-key="${key}"]`)).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
    for (const [i, group] of fillers.entries()) {
      await apiDelete(`/sessions/scroll-rename-filler-${i}`);
      await apiDelete(`/folders/${encodeURIComponent(group)}`);
    }
    for (const group of [from, to]) await apiDelete(`/folders/${encodeURIComponent(group)}`);
  }
});
