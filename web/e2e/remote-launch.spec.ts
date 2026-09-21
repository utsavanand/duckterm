import { expect, test } from "@playwright/test";

test("remote destination reopens New Session with the carried task", async ({ page }) => {
  await page.addInitScript(() => {
    window.__rubbertermDesktop = {
      currentTarget: "dev",
      targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
      draft: { agent: "codex", command: "codex", name: "Remote task", prompt: "Check the build" },
    };
  });
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "Run on" })).toHaveValue("dev");
  await expect(page.getByPlaceholder("e.g. login refactor")).toHaveValue("Remote task");
  await expect(page.getByPlaceholder("add a healthcheck endpoint")).toHaveValue("Check the build");
  await expect(page.getByText("Browse…", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  await expect(page.getByPlaceholder("e.g. login refactor")).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Run on" })).toHaveValue("dev");
});
