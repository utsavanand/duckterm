import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, expandFolder, postEvent, seedSession } from "./helpers";

test("filters across folders, persists choices, and restores expansion and selected session", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  const keys = ["filter-active", "filter-waiting", "filter-idle"];
  const folders = ["Filter project", "Filter project/Design"];
  try {
    for (const name of folders) await apiPost("/folders", { name });
    await seedSession(keys[0], { name: "filter-active", group: folders[0], runtime: "codex" });
    await seedSession(keys[1], { name: "filter-waiting", group: folders[1], runtime: "claude-code" });
    await postEvent({ session_key: keys[1], event_type: "PermissionRequest", runtime: "claude-code" });
    await seedSession(keys[2], { name: "filter-idle", group: folders[1], runtime: "codex" });
    await postEvent({ session_key: keys[2], event_type: "Notification", notification_type: "idle_prompt", runtime: "codex" });
    await page.goto(base());
    await expandFolder(page, folders[0]);
    await page.locator(".rd-row-name", { hasText: "filter-active" }).click();
    const filters = page.getByRole("region", { name: "Session filters" });
    const waiting = filters.getByRole("button", { name: /^Waiting / });
    await waiting.focus();
    await page.keyboard.press("Space");
    await expect(waiting).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".rd-row-name", { hasText: "filter-active" })).toHaveCount(0);
    await expect(page.locator(".rd-row-folder", { hasText: folders[1] })).toBeVisible();
    await expect(page.locator(".rd-row-name", { hasText: "filter-waiting" })).toBeVisible();
    // A different harness must produce an explicit empty state, not a blank tree.
    await filters.getByRole("button", { name: /^Codex / }).click();
    await expect(page.getByText("No sessions match these filters.")).toBeVisible();
    await filters.getByRole("button", { name: /^Codex / }).click();
    await waiting.focus();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: `Collapse ${folders[0]}`, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: `Expand ${folders[1]}`, exact: true })).toBeVisible();
    await expect(page.locator(".rd-row.selected")).toContainText("filter-active");
    const phone = page.getByRole("button", { name: `View interactions in ${folders[0]}`, exact: true });
    await phone.focus();
    await expect(phone).toHaveCSS("opacity", "1");
    await waiting.click();
    await page.reload();
    await expect(waiting).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".rd-row-name", { hasText: "filter-waiting" })).toBeVisible();
    await expect(page.locator(".rd-pin-strip")).toHaveCount(0);
    await expect(page.locator(".rd-brand-name")).toHaveText("DuckTerm");
    await expect(page.locator(".rd-brand-term")).toHaveCSS("color", await page.locator(".rd-brand-name").evaluate(el => getComputedStyle(el).color));
    await page.screenshot({ path: "/tmp/sidebar-filters-implemented-dark.png" });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("combobox", { name: "Sidebar density" }).selectOption("compact");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(waiting.locator(".rd-filter-chip-label")).toBeHidden();
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/sidebar-filters-implemented-light-compact.png" });
    const bounds = await page.locator(".rd-sidebar-filters").evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth }));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.width);
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
    for (const name of folders.reverse()) await apiDelete(`/folders/${encodeURIComponent(name)}`);
  }
});
