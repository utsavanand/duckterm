import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Clicking a session highlights its left-panel row, so it's obvious which row
// the middle terminal belongs to. Exactly ONE row is highlighted at a time
// (the app auto-selects the first agent so the center pane is never empty).
test("selecting a session highlights its row and moves with the selection", async ({
  page,
}) => {
  const mk = async (name: string) => {
    const r = await apiPost("/sessions/launch", {
      command: "sh -c 'echo hi; exec cat'",
      cwd: "/tmp",
      name,
      in_terminal: false,
      test: true,
    });
    return r.body.session_key as string;
  };
  await mk("alpha");
  await mk("beta");

  await page.goto(base());
  const alpha = page.locator(".rd-row", { hasText: "alpha" });
  const beta = page.locator(".rd-row", { hasText: "beta" });
  await expect(alpha).toBeVisible();

  // Exactly one row is highlighted from the start (auto-selected).
  await expect(page.locator(".rd-row.selected")).toHaveCount(1);

  // Clicking a specific session highlights it, and only it.
  await beta.locator(".rd-row-click").click();
  await expect(beta).toHaveClass(/selected/);
  await expect(alpha).not.toHaveClass(/selected/);
  await expect(page.locator(".rd-row.selected")).toHaveCount(1);

  // The highlight follows the selection — never two at once.
  await alpha.locator(".rd-row-click").click();
  await expect(alpha).toHaveClass(/selected/);
  await expect(beta).not.toHaveClass(/selected/);
  await expect(page.locator(".rd-row.selected")).toHaveCount(1);
});
