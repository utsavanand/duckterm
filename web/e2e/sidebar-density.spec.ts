import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

test("sidebar density changes information and actions, remembers choice, and keeps names regular", async ({ page }) => {
  const key = await seedSession("density-test", { name: "density-agent", runtime: "codex" });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    const row = page.locator(".rd-row", { has: page.locator(".rd-row-name", { hasText: "density-agent" }) });
    const settings = page.getByRole("button", { name: "Settings", exact: true });
    await row.locator(".rd-row-click").click();
    await page.mouse.move(900, 80);
    await expect(page.locator(".rd-app")).toHaveAttribute("data-density", "standard");
    const standard = (await row.boundingBox())!.height;
    await expect(row.locator(".rd-row-meta")).toBeVisible();
    await expect(row.locator(".rd-row-density-detail")).toBeHidden();
    await settings.click();
    const density = page.getByRole("combobox", { name: "Sidebar density" });
    await density.selectOption("compact");
    await page.keyboard.press("Escape");
    await expect(row.locator(".rd-row-name")).toHaveCSS("font-weight", "400");
    await expect(row.locator(".rd-row-meta")).toBeHidden();
    expect((await row.boundingBox())!.height).toBeLessThan(standard);
    await expect(row).toHaveAttribute("title", /codex/);
    await row.getByRole("button", { name: "Actions for density-agent" }).click();
    await expect(row.getByRole("button", { name: "Notes", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(row.getByRole("button", { name: "Notes", exact: true })).toBeHidden();
    await page.reload();
    await expect(page.locator(".rd-app")).toHaveAttribute("data-density", "compact");
    await row.locator(".rd-row-click").click();
    await settings.click();
    await density.selectOption("relaxed");
    await page.keyboard.press("Escape");
    await page.mouse.move(900, 80);
    await expect(row.locator(".rd-row-density-detail")).toContainText("codex");
    await expect(row.getByRole("button", { name: "Notes", exact: true })).toBeVisible();
    expect((await row.boundingBox())!.height).toBeGreaterThan(standard);
    await expect(row.locator(".rd-row-name")).toHaveCSS("font-weight", "400");
    await page.reload();
    await expect(page.locator(".rd-app")).toHaveAttribute("data-density", "relaxed");
  } finally { await apiDelete(`/sessions/${key}`); }
});
