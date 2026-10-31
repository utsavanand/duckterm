import { expect, test } from "@playwright/test";
import { apiPost } from "./helpers";

test("checkpoint settings save and reopen through the real API", async ({ page }) => {
  const defaults = { lookback_records: "all", writer_model: "sonnet", reviewer_model: "sonnet" };
  await apiPost("/checkpoint-settings", defaults);
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: false }).click();
    await page.getByRole("button", { name: "Checkpointing", exact: false }).click();
    await expect(page.locator("#header-settings-panel")).toHaveCount(0);
    await expect(page.getByRole("radio", { name: "All unsummarized messages (default)" })).toBeChecked();
    const field = page.getByLabel("Latest records per checkpoint");
    await expect(field).toBeDisabled();
    await page.getByRole("radio", { name: /Only the latest/ }).check();
    await field.fill("60");
    await page.getByLabel("Summary writer").selectOption("opus");
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    await expect(page.getByText("Settings saved", { exact: true })).toBeVisible();
    await page.screenshot({ path: "/tmp/checkpoint-settings-implemented-dark.png" });
    await page.getByRole("heading", { name: "Checkpointing", exact: true }).locator("..").getByRole("button", { name: "✕", exact: true }).click();
    await page.getByRole("button", { name: "Settings", exact: false }).click();
    await page.getByRole("button", { name: "Checkpointing", exact: false }).click();
    await expect(field).toHaveValue("60");
    await expect(page.getByLabel("Summary writer")).toHaveValue("opus");
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/checkpoint-settings-implemented-light.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/checkpoint-settings-implemented-mobile.png" });
    expect(await page.locator(".rd-checkpoint-settings").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiPost("/checkpoint-settings", defaults); }
});
