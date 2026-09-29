import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiPost, seedSession } from "./helpers";

test("AGENTS.md preserves a rule typed while the initial read is delayed", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "rd-e2e-rules-load-"));
  const key = `e2e-rules-load-${Date.now()}`;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await seedSession(key, { name: key, cwd: dir });
  try {
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: key }).click();
    await page.route("**/agents-md?*", async (route) => {
      await held;
      await route.continue();
    });
    await page.getByRole("button", { name: "AGENTS.md", exact: true }).click();
    const input = page.getByPlaceholder(/New rule/);
    await input.fill("Keep the new rule.");
    await input.press("Enter");
    await expect(page.locator(".rd-rule-edit")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Add", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    release();
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.locator(".rd-rule-edit")).toHaveValue("Keep the new rule.");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Rules saved")).toBeVisible();
    expect(readFileSync(join(dir, "AGENTS.md"), "utf8")).toContain("Keep the new rule.");
    const saved = JSON.parse(readFileSync(join(dir, ".duckterm-rules.json"), "utf8"));
    expect(saved.rules).toEqual([expect.objectContaining({ text: "Keep the new rule.", status: "active" })]);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
    await apiPost(`/sessions/${key}/delete`);
    rmSync(dir, { recursive: true, force: true });
  }
});
