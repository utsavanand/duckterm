import { expect, test } from "@playwright/test";
import { apiDelete, base, postEvent, seedSession, sessionMenu } from "./helpers";

test("right-click actions work in every density without filling session rows or the information panel", async ({ page }) => {
  const keys: string[] = [];
  try {
    for (const state of ["stopped", "interrupted", "terminated", "busy"]) {
      const key = await seedSession(`card-${state}`, { name: `Review ${state}`, runtime: "codex", launched: true, test: true });
      keys.push(key);
      if (state !== "busy") await postEvent({ event_type: "Notification", session_key: key, lifecycle: state });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    const stopped = page.locator(".rd-row", { has: page.getByText("Review stopped", { exact: true }) });
    await stopped.locator(".rd-row-name").click();
    const card = page.getByRole("region", { name: "Session controls" });
    for (const density of ["compact", "standard", "relaxed"]) {
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await page.getByRole("combobox", { name: "Sidebar density" }).selectOption(density);
      await page.keyboard.press("Escape");
      const menu = await sessionMenu(page);
      await expect(menu.getByRole("menuitem", { name: "Resume", exact: true })).toBeDisabled();
      await expect(menu.getByRole("menuitem", { name: "Notes", exact: true })).toBeVisible();
      await expect(menu.getByRole("menuitem", { name: "Archive", exact: true })).toBeVisible();
      await expect(menu.getByRole("menuitem", { name: "Delete permanently…", exact: true })).toHaveCount(0);
      await expect(card.getByRole("button", { name: /^(Resume|Restart|Notes|Checkpoint|Archive|Delete)/ })).toHaveCount(0);
      await expect(stopped.locator(".rd-row-resume,.rd-row-actions")).toHaveCount(0);
      await page.keyboard.press("Escape");
      await expect(stopped).toBeFocused();
    }
    await page.locator(".rd-row-name", { hasText: "Review busy" }).click();
    const menu = await sessionMenu(page);
    for (const name of ["Restart…", "Change model…", "Checkpoint", "Notes", "Stop", "Archive"]) {
      await expect(menu.getByRole("menuitem", { name, exact: true })).toBeVisible();
    }
    await expect(menu.getByRole("menuitem", { name: "Delete permanently…", exact: true })).toHaveCount(0);
    await expect(menu.getByRole("menuitem", { name: "Resume", exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-session-actions-implemented.png" });
  } finally { for (const key of keys) await apiDelete(`/sessions/${key}`); }
});

test("keyboard context menu targets an unselected row, fits narrow screens, and restores focus", async ({ page }) => {
  const first = await seedSession(`menu-a-${Date.now()}`, { name: "Menu first", runtime: "generic", test: true });
  const second = await seedSession(`menu-b-${Date.now()}`, { name: "Menu second", runtime: "generic", test: true });
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Menu first" }).click();
    const row = page.locator(".rd-row", { has: page.getByText("Menu second", { exact: true }) });
    await row.focus(); await page.keyboard.press("Shift+F10");
    const menu = page.getByRole("menu", { name: "Actions for Menu second" });
    await expect(menu).toBeVisible();
    await expect(page.locator(".rd-row.selected")).toContainText("Menu first");
    await page.keyboard.press("Home");
    await expect(menu.getByRole("menuitem").first()).toBeFocused();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem").last()).toBeFocused();
    await page.keyboard.press("Escape"); await expect(row).toBeFocused();
    await page.setViewportSize({ width: 390, height: 844 });
    await row.click({ button: "right" });
    await expect(menu).toBeInViewport({ ratio: 1 });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/duckterm-session-actions-narrow.png" });
    await menu.getByRole("menuitem", { name: "Rename…" }).click();
    await page.getByRole("textbox", { name: "Session name", exact: true }).fill("Renamed second");
    await page.getByRole("dialog", { name: "Rename session" }).getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".rd-row.selected")).toContainText("Menu first");
    await expect(page.locator(".rd-row-name", { hasText: "Renamed second" })).toBeVisible();
  } finally { await apiDelete(`/sessions/${first}`); await apiDelete(`/sessions/${second}`); }
});

test("archived history stays accessible and permanent deletion requires confirmation", async ({ page }) => {
  const key = await seedSession(`archived-menu-${Date.now()}`, { name: "Archived menu review", runtime: "generic", launched: true, test: true });
  try {
    await postEvent({ event_type: "Notification", session_key: key, lifecycle: "archived" });
    await page.goto(base());
    const row = page.locator(".rd-row", { has: page.getByText("Archived menu review", { exact: true }) });
    await expect(row).toHaveCount(0);
    await page.getByRole("button", { name: /Archived sessions/ }).click();
    await row.locator(".rd-row-name").click();
    const menu = await sessionMenu(page);
    await expect(menu.getByRole("menuitem", { name: /Restart|Resume|Stop|Archive/ })).toHaveCount(0);
    await menu.getByRole("menuitem", { name: "Delete permanently…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirm session removal" });
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(row).toBeVisible();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Delete permanently…", exact: true }).click();
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(row).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: /Archived sessions/ })).toHaveCount(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
