import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base } from "./helpers";

test("Oracle widgets save order and visibility across reloads", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-widgets-")));
  const folder = `Widget test ${Date.now()}`, keys: string[] = [];
  try {
    for (const name of ["ui-dev", "api-dev", "qa", "research", "release-dev", "mobile-dev"]) {
      const result = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name, in_terminal: false, test: true });
      expect(result.status).toBe(200); keys.push(String(result.body.session_key));
      await apiPatch(`/sessions/${keys.at(-1)}`, { group: folder + (name === "mobile-dev" ? "/Mobile" : "/Website") });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(base());
    await page.getByRole("button", { name: /^Oracle/ }).click();
    const widgets = page.getByRole("region", { name: "Fleet insights" });
    await expect(widgets.getByRole("button", { name: "Add widget" })).toBeEnabled();
    await expect(widgets.getByRole("article")).toHaveCount(6);
    await expect(widgets.getByRole("article", { name: "Remote sessions", exact: true })).toContainText("Unavailable");
    const chat = await page.getByRole("complementary", { name: "Ask Oracle", exact: true }).boundingBox();
    const grid = await widgets.boundingBox();
    expect(chat!.x).toBeLessThan(grid!.x);
    expect(chat!.width).toBeGreaterThan(grid!.width);
    await page.screenshot({ path: "/tmp/duckterm-oracle-widgets-implemented.png" });
    await widgets.getByRole("button", { name: "Reorder Last backup", exact: true }).focus();
    await page.keyboard.press("ArrowUp");
    await expect(widgets.getByRole("article").nth(3)).toHaveAttribute("aria-label", "Last backup");
    await widgets.getByRole("button", { name: "Options for Remote sessions" }).click();
    await widgets.getByRole("button", { name: "Remove widget" }).click();
    await expect(widgets.getByRole("article")).toHaveCount(5);
    await page.reload();
    await page.getByRole("button", { name: /^Oracle/ }).click();
    await expect(widgets.getByRole("button", { name: "Add widget" })).toBeEnabled();
    await expect(widgets.getByRole("article")).toHaveCount(5);
    await expect(widgets.getByRole("article").nth(3)).toHaveAttribute("aria-label", "Last backup");
    await widgets.getByRole("button", { name: "Add widget" }).click();
    await widgets.getByRole("checkbox", { name: "Remote sessions" }).click();
    await expect(widgets.getByRole("article")).toHaveCount(6);
    await page.keyboard.press("Escape");
    await expect(widgets).toBeVisible();
    await widgets.getByRole("button", { name: "Reset", exact: true }).click();
    await expect(widgets.getByRole("article").nth(3)).toHaveAttribute("aria-label", "Agent mail · 24h");
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/duckterm-oracle-widgets-light.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/duckterm-oracle-widgets-mobile.png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(cwd, { recursive: true, force: true });
  }
});
