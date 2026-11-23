import { expect, test } from "@playwright/test";

test("Settings reflects server feature flags and preserves core controls", async ({ page }) => {
  const optedIn = process.env.RD_TEST_FEATURE_FLAGS !== "";
  await page.goto("/");
  const edition = await (await page.request.get("/edition")).json();
  const supportsCloud = edition.id === "full";
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  for (const name of ["Collaboration", "Skill kits", "Agent setup", "Back up to remote"]) {
    const control = page.getByRole("button", { name, exact: true });
    if (optedIn) await expect(control).toBeVisible();
    else await expect(control).toHaveCount(0);
  }
  await expect(page.getByRole("button", { name: "Checkpointing", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Feature flags", exact: true }).click();
  const table = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "This installation", exact: true }) });
  await expect(table.getByRole("row")).toHaveCount(9);
  await expect(table.getByRole("cell", { name: "Home", exact: true })).toHaveCount(1);
  await expect(table.getByRole("cell", { name: "Background rule suggestions", exact: true })).toHaveCount(1);
  await expect(table.getByRole("cell", { name: optedIn ? "Local override" : "Release default", exact: true })).toHaveCount(supportsCloud ? 6 : 5);
  await expect(table.getByRole("cell", { name: "Not included in this edition", exact: true })).toHaveCount(supportsCloud ? 0 : 1);
});
