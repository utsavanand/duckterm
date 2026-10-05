import { expect, test } from "@playwright/test";
import { apiDelete, apiPost } from "./helpers";

test("Settings preserves the live terminal and shares preferences with native menu actions", async ({ page }) => {
  const launched = await apiPost("/sessions/launch", { command: "cat", cwd: "/tmp", name: "Settings workflow", in_terminal: false, test: true });
  expect(launched.status).toBe(200); const key = String(launched.body.session_key);
  try {
    await page.addInitScript(() => {
      Object.assign(window, { __rubbertermDesktop: { currentTarget: "local", targets: [], canSettingsMenu: true }, __menuStates: [], webkit: { messageHandlers: { remoteSession: { postMessage: (m: unknown) => (window as unknown as {__menuStates: unknown[]}).__menuStates.push(m) } } } });
    });
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto("/");
    await page.locator(".rd-row-name", { hasText: "Settings workflow" }).click();
    const terminal = page.locator(".rd-terminal-slot:visible .xterm"); await expect(terminal).toBeVisible();
    await terminal.click(); await page.keyboard.type("unsubmitted draft");
    await expect(terminal).toContainText("unsubmitted draft");
    const original = await terminal.elementHandle(); const bounds = await terminal.boundingBox();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Appearance", exact: true })).toBeFocused();
    await expect(page.locator(".rd-workspace-panes")).toHaveAttribute("inert", "");
    expect(await terminal.boundingBox()).toEqual(bounds);
    await page.getByRole("group", { name: "Theme", exact: true }).getByRole("button", { name: "Light", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.screenshot({ path: "/tmp/settings-implemented-light.png" });
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("duckterm-menu", { detail: { action: "theme", value: "dark" } })));
    await expect(page.getByRole("button", { name: "Dark", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("combobox", { name: "Sidebar density" }).selectOption("compact");
    await expect.poll(() => page.evaluate(() => (window as unknown as {__menuStates: {density:string}[]}).__menuStates.at(-1)?.density)).toBe("compact");
    await page.screenshot({ path: "/tmp/settings-implemented-dark.png" });
    await page.getByRole("button", { name: "Notifications & voice", exact: true }).click();
    await page.screenshot({ path: "/tmp/settings-implemented-voice.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/settings-implemented-mobile.png", fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "← Sessions", exact: true }).click();
    expect(await original!.evaluate(el => el.isConnected)).toBe(true);
    await expect(terminal).toContainText("unsubmitted draft");
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("duckterm-menu", { detail: { action: "folder" } })));
    await expect(page.getByRole("heading", { name: "New folder", exact: true })).toBeVisible();
  } finally { await apiDelete(`/sessions/${key}`); }
});
