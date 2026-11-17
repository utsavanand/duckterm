import { expect, test } from "@playwright/test";

test("Settings reflects server feature flags and preserves core controls", async ({ page }) => {
  const optedIn = process.env.RD_TEST_FEATURE_FLAGS !== "";
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  for (const name of ["Collaboration", "Harnesses", "Agent setup", "Back up to remote"]) {
    const control = page.getByRole("button", { name, exact: true });
    if (optedIn) await expect(control).toBeVisible();
    else await expect(control).toHaveCount(0);
  }
  await expect(page.getByRole("button", { name: "Checkpointing", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Feature flags", exact: true }).click();
  const table = page.getByRole("table");
  await expect(table.getByRole("row")).toHaveCount(7);
  await expect(table.getByText(optedIn ? "Local override" : "Release default", { exact: true })).toHaveCount(6);
});
