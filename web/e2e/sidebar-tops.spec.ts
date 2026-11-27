import { expect, test, type Page } from "@playwright/test";

const rows = ["Autopilot", "Oracle main chat", "Support"];

async function rowHeights(page: Page) {
  await page.goto("/");
  const heights = [];
  for (const name of rows) {
    const row = page.locator(".rd-sidebar-tops").getByRole("button", { name, exact: true });
    await expect(row).toBeVisible();
    heights.push((await row.boundingBox())!.height);
  }
  return heights;
}

test.describe("short window", () => {
  test.use({ viewport: { width: 1280, height: 760 } });
  test("Autopilot, Oracle and Support shrink to one line each", async ({ page }) => {
    for (const height of await rowHeights(page)) expect(height).toBeLessThan(40);
    await expect(page.locator(".rd-sidebar-tops small").first()).toBeHidden();
  });
});

test.describe("tall window", () => {
  test.use({ viewport: { width: 1280, height: 900 } });
  test("the rows keep their subtitles", async ({ page }) => {
    for (const height of await rowHeights(page)) expect(height).toBeGreaterThan(50);
    await expect(page.locator(".rd-sidebar-tops").getByText("How DuckTerm works")).toBeVisible();
  });
});
