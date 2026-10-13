import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("ten pins persist across sidebar, floating shortcuts and Focus without displacing the terminal", async ({ page }) => {
  test.setTimeout(90_000);
  const keys: string[] = [];
  const names = Array.from({ length: 11 }, (_, i) => `Ten pin ${String(i + 1).padStart(2, "0")}`);
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'echo TEN_PINS_READY; exec cat'", cwd: "/tmp", name: names[0], in_terminal: false, test: true });
    expect(launched.status).toBe(200); keys.push(String(launched.body.session_key));
    for (let i = 1; i < names.length; i++) {
      const key = `ten-pins-${Date.now()}-${i}`; keys.push(key);
      await seedSession(key, { name: names[i] });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: names[0] }).click();
    const slot = page.locator(".rd-terminal-slot:visible");
    const rail = page.getByRole("navigation", { name: "Pinned session shortcuts" });
    const width = (await slot.boundingBox())!.width;
    await expect(slot.locator(".xterm-rows")).toContainText("TEN_PINS_READY");
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    const picker = page.getByRole("dialog", { name: "Pin sessions" });
    await picker.getByRole("searchbox").fill("Ten pin");
    for (const name of names.slice(0, 10)) {
      await picker.getByRole("button", { name: `Pin ${name} in picker`, exact: true }).click();
      await expect(picker.getByRole("button", { name: `Unpin ${name} in picker`, exact: true })).toBeEnabled();
    }
    await expect(picker.getByRole("button", { name: `Pin ${names[10]} in picker`, exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(10);
    expect((await slot.boundingBox())!.width).toBe(width);
    await page.locator(".rd-agents").getByRole("button", { name: `Pin ${names[10]}`, exact: true }).click();
    await expect(page.getByText("Unpin one first", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Focus · 10", exact: true })).toBeEnabled();
    await page.reload();
    await page.locator(".rd-row-name", { hasText: names[0] }).click();
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(10);
    await page.getByRole("button", { name: "Focus · 10", exact: true }).click();
    await expect(page.locator(".rd-grid-tile")).toHaveCount(3);
    await expect(page.locator(".rd-grid-dock-chip")).toHaveCount(7);
    await expect(page.locator(".rd-grid-title")).toHaveText("Focus · 10 pinned");
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 1440, height: 400 });
    expect(await rail.locator(".rd-shortcut-list").evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
    const last = rail.getByRole("button", { name: `Open pinned ${names[0]}`, exact: true });
    await last.scrollIntoViewIfNeeded();
    await expect(last).toBeInViewport();
    await last.hover();
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `/tmp/ten-pins-${colorScheme}.png` });
    }
    await rail.getByRole("button", { name: `Unpin ${names[0]} from shortcuts`, exact: true }).click();
    await rail.getByRole("button", { name: "Choose sessions to pin" }).click();
    await picker.getByRole("searchbox").fill(names[10]);
    await picker.getByRole("button", { name: `Pin ${names[10]} in picker`, exact: true }).click();
    await expect(picker.getByRole("button", { name: `Unpin ${names[10]} in picker`, exact: true })).toBeEnabled();
    await page.keyboard.press("Escape");
    await expect(rail.locator(".rd-shortcut-tab")).toHaveCount(10);
    await expect(rail.getByRole("button", { name: `Open pinned ${names[0]}`, exact: true })).toHaveCount(0);
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText(names[0]);
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
  }
});
