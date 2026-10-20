import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession, sessionMenu } from "./helpers";

test("session sections preserve meaningful state, notes and checkpoint review without background writes", async ({ page }) => {
  const key = await seedSession(`panel-${Date.now()}`, { name: "Panel review", runtime: "generic", test: true });
  const savedAt = Date.now() - 2 * 86400000;
  let writes = 0;
  page.on("request", r => { if (r.method() === "POST" && /\/(checkpoint|input)$/.test(new URL(r.url()).pathname)) writes++; });
  try {
    await page.route(`**/sessions/${key}/checkpoints`, route => route.fulfill({ json: {
      checkpoint_clock: { state: "known", succeeded_at: savedAt, checkpoint_id: "saved" },
      checkpoints: [{ id: "failed", label: "manual", created_at: Date.now(), summary_update: { state: "failed", reason: "summary_unverified" }, record: { prompts: [], files: [], tools: [], event_count: 9000 } }],
    } }));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Panel review" }).click();
    const panel = page.locator("#context-view-session");
    const saved = panel.locator(".rd-session-section").filter({ has: page.locator("summary > span", { hasText: /^Saved memory$/ }) });
    const context = panel.locator(".rd-session-section").filter({ has: page.locator("summary > span", { hasText: /^Active context$/ }) });
    await expect(saved).toHaveAttribute("open", "");
    await expect(saved.getByText("2d 0h ago").last()).toBeVisible();
    await expect(saved.locator(".rd-value-unavailable")).toHaveText("Unavailable");
    await expect(context.getByText("Last compaction").locator("+ dd")).toHaveText("Unknown");
    await expect(context.getByRole("button", { name: "Compact", exact: true })).toBeDisabled();
    await saved.locator(":scope > summary").focus(); await page.keyboard.press("Enter");
    await expect(saved).not.toHaveAttribute("open", "");
    await expect(saved.locator(".rd-section-status")).toHaveText("2d 0h ago");
    await page.keyboard.press("Enter");
    await saved.locator(".rd-section-disclosure > summary").first().click();
    await expect(saved.getByText("The summary did not pass validation", { exact: true })).toBeVisible();
    await saved.locator(".rd-section-disclosure > summary").first().click();
    await page.screenshot({ path: "/tmp/duckterm-session-panel-e2e-dark.png" });
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Notes", exact: true }).click();
    await expect(page.getByLabel("Session notes")).toBeVisible();
    await page.getByLabel("Session notes").fill("A reviewed note");
    await panel.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByLabel("Session notes")).toHaveCount(0);
    await page.route(`**/sessions/${key}/checkpoint-plan`, route => route.fulfill({ status: 503, json: { error: "Count unavailable" } }));
    await saved.getByRole("button", { name: "Checkpoint", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Checkpoint", exact: true });
    await expect(dialog.getByText("Couldn’t count the available history.")).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(writes).toBe(0);
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/duckterm-session-panel-e2e-light.png" });
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); }
});
