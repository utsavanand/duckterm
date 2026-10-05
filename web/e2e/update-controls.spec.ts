import { expect, test } from "@playwright/test";

test("Settings exposes honest disabled update controls at desktop and narrow widths", async ({ page }) => {
  let checks = 0, installs = 0;
  page.on("request", request => { if (request.url().includes("/update/status")) checks++; if (request.url().includes("/update/install")) installs++; });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.emulateMedia({ colorScheme: "dark" }); await page.goto("/");
  expect(checks).toBe(0);
  await page.getByRole("button", { name: "Settings", exact: true }).click(); await page.getByRole("button", { name: "Updates", exact: true }).click();
  const update = page.getByRole("region", { name: "Update DuckTerm" });
  await update.scrollIntoViewIfNeeded();
  await expect(update).toContainText("Installed"); await expect(update).toContainText("Couldn’t check for updates.");
  await expect(update.getByRole("button", { name: "Install update" })).toBeDisabled();
  await page.screenshot({ path: "/tmp/duckterm-update-controls-dark.png" });
  expect(checks).toBe(1); expect(installs).toBe(0);
  await page.keyboard.press("Escape"); await page.getByRole("button", { name: "Settings", exact: true }).click(); await page.getByRole("button", { name: "Updates", exact: true }).click(); await update.scrollIntoViewIfNeeded();
  await expect(update).toContainText("Installed"); expect(checks).toBe(2);
  await page.setViewportSize({ width: 390, height: 844 }); await update.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/duckterm-update-controls-mobile.png" });
  const box = await update.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(390);
});
