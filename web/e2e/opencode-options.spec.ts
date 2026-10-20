import { expect, test } from "@playwright/test";

test("OpenCode form reflects actual destination support and refuses unverified launch", async ({ page, request }) => {
  const options = await request.get("/runtimes/opencode/launch-options");
  expect(options.ok()).toBe(true);
  const support = await options.json();
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  await page.getByRole("button", { name: "OpenCode", exact: true }).click();
  await expect(page.getByLabel("OpenCode model")).toHaveValue("");
  await page.getByLabel("OpenCode model").fill("local/org/model:quantized");
  if (!support.launch.available) {
    await expect(page.getByRole("dialog").getByRole("status")).toContainText(support.launch.reason);
    await expect(page.getByRole("button", { name: "Launch", exact: true })).toBeDisabled();
  }
  await page.getByText("OpenCode support", { exact: true }).click();
  await expect(page.getByText("Fork conversation: Unavailable.", { exact: true })).toBeVisible();
  await page.getByText("OpenCode support", { exact: true }).click();
  await page.getByRole("button", { name: "Codex", exact: true }).click();
  await expect(page.getByLabel("OpenCode model")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Launch", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
});
