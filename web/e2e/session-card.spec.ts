import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, expandFolder, postEvent, seedSession, sessionMenu } from "./helpers";

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
      await expect(menu.getByRole("menuitem", { name: "Delete permanently", exact: true })).toHaveCount(0);
      await expect(card.getByRole("button", { name: /^(Resume|Restart|Notes|Checkpoint|Archive|Delete)/ })).toHaveCount(0);
      await expect(stopped.locator(".rd-row-resume,.rd-row-actions")).toHaveCount(0);
      await page.keyboard.press("Escape");
      await expect(stopped).toBeFocused();
    }
    await page.locator(".rd-row-name", { hasText: "Review busy" }).click();
    const menu = await sessionMenu(page);
    for (const name of ["Restart", "Change model", "Checkpoint", "Notes", "Stop", "Archive"]) {
      await expect(menu.getByRole("menuitem", { name, exact: true })).toBeVisible();
    }
    await expect(menu.getByRole("menuitem", { name: "Delete permanently", exact: true })).toHaveCount(0);
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
    await expect(row).toHaveClass(/context-target/);
    await expect(row).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator(".rd-row.selected")).toContainText("Menu first");
    await page.keyboard.press("Home");
    await expect(menu.getByRole("menuitem").first()).toBeFocused();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem").last()).toBeFocused();
    await page.keyboard.press("Escape"); await expect(row).toBeFocused();
    await expect(row).not.toHaveClass(/context-target/);
    await page.setViewportSize({ width: 390, height: 844 });
    await row.click({ button: "right" });
    await expect(menu).toBeInViewport({ ratio: 1 });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/duckterm-session-actions-narrow.png" });
    await menu.getByRole("menuitem", { name: "Rename" }).click();
    await expect(row).toHaveClass(/context-target/);
    await page.getByRole("textbox", { name: "Session name", exact: true }).fill("Renamed second");
    await page.getByRole("dialog", { name: "Rename session" }).getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".rd-row.selected")).toContainText("Menu first");
    await expect(page.locator(".rd-row-name", { hasText: "Renamed second" })).toBeVisible();
    await expect(row).toHaveCount(0);
    await expect(page.locator(".rd-row.context-target")).toHaveCount(0);
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
    await menu.getByRole("menuitem", { name: "Delete permanently", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirm session removal" });
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(row).toBeVisible();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Delete permanently", exact: true }).click();
    await dialog.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await expect(row).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: /Archived sessions/ })).toHaveCount(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});


test("menu stays open after a long sidebar scrolls a bottom row into view", async ({ page }) => {
  const folders = Array.from({ length: 25 }, (_, i) => `Menu scroll ${i}`);
  const key = await seedSession(`menu-scroll-${Date.now()}`, { name: "Bottom menu target", runtime: "generic", test: true });
  try {
    for (const name of folders) await apiPost("/folders", { name });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base());
    const row = page.locator(".rd-row", { has: page.getByText("Bottom menu target", { exact: true }) });
    await row.click({ button: "right" });
    const menu = page.getByRole("menu", { name: "Actions for Bottom menu target" });
    await expect(menu).toBeInViewport({ ratio: 1 });
    await page.evaluate(() => document.dispatchEvent(new Event("scroll")));
    await expect(menu.getByRole("menuitem", { name: "Notes", exact: true })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(row).toBeFocused();
  } finally {
    await apiDelete(`/sessions/${key}`);
    for (const name of folders) await apiDelete(`/folders/${encodeURIComponent(name)}`);
  }
});


test("Move to folder targets the clicked session, handles failures and persists a nested destination", async ({ page }) => {
  const prefix = `Menu move ${Date.now()}`;
  const destination = `${prefix}/Projects`;
  const active = await seedSession(`move-active-${Date.now()}`, { name: "Working terminal", runtime: "generic", test: true });
  const target = await seedSession(`move-target-${Date.now()}`, { name: "Move this agent", runtime: "generic", test: true });
  try {
    await apiPost("/folders", { name: destination });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Working terminal" }).click();
    const row = page.locator(".rd-row", { has: page.getByText("Move this agent", { exact: true }) });
    await row.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Move to folder", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Move Move this agent to folder" });
    await expect(dialog.getByRole("button", { name: "Move", exact: true })).toBeDisabled();
    await expect(row).toHaveClass(/context-target/);
    await expect(page.locator(".rd-row.selected")).toContainText("Working terminal");
    await dialog.getByRole("searchbox", { name: "Search folders" }).fill("no such folder");
    await expect(dialog.getByText("No matching folders.")).toBeVisible();
    await dialog.getByRole("searchbox", { name: "Search folders" }).fill("Projects");
    await dialog.getByRole("button", { name: destination, exact: true }).click();
    await page.screenshot({ path: "/tmp/duckterm-session-menu-folder-picker.png" });
    const route = `**/sessions/${target}`;
    await page.route(route, async handler => {
      if (handler.request().method() === "PATCH") await handler.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Temporarily offline" }) });
      else await handler.continue();
    });
    await dialog.getByRole("button", { name: "Move", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("Temporarily offline");
    await expect(row).toHaveClass(/context-target/);
    await expect(page.locator(".rd-group-body .rd-row", { hasText: "Move this agent" })).toHaveCount(0);
    await page.unroute(route);
    await dialog.getByRole("button", { name: "Move", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator(".rd-group-body .rd-row", { hasText: "Move this agent" })).toBeVisible();
    await expect(page.locator(".rd-row.selected")).toContainText("Working terminal");
    await expect(page.locator(".rd-row.context-target")).toHaveCount(0);
    await page.reload(); await expandFolder(page, destination);
    await expect(page.locator(".rd-group-body .rd-row", { hasText: "Move this agent" })).toBeVisible();
    await row.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Move to folder", exact: true }).click();
    await expect(dialog.getByRole("button", { name: `${destination} Current`, exact: true })).toHaveAttribute("aria-pressed", "true");
    await dialog.getByRole("button", { name: "Ungrouped", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(row).toBeFocused();
    await expect(row).not.toHaveClass(/context-target/);
    await expect(page.locator(".rd-group-body .rd-row", { hasText: "Move this agent" })).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${active}`); await apiDelete(`/sessions/${target}`);
    await apiDelete(`/folders/${encodeURIComponent(prefix)}`);
  }
});
