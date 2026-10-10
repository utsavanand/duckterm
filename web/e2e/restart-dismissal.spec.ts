import { expect, test } from "@playwright/test";
import { apiDelete, seedSession, sessionMenu } from "./helpers";

test("dismissing a session dialog also dismisses its originating context menu", async ({ page }) => {
  const key = await seedSession(`dismiss-restart-${Date.now()}`, { name: "Dismiss review", runtime: "claude-code", launched: true, test: true });
  let submitted = 0, released = 0;
  let binding: unknown;
  const preparation = () => ({
    version: 1, preparation_id: "d".repeat(32), sequence: 1, state: "preparing", binding,
    coverage: { state: "unknown", available_text: "not_processed", retrieval: "unavailable", retention: "unknown", source_count: 0, covered_source_count: 0, gap_count: 0, gaps: [], has_more: false, details_cursor: null },
  });
  await page.route(`**/sessions/${key}/restart**`, async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.endsWith("restart-options")) return route.fulfill({ json: {
      current: { harness: "claude-code", model: "current-model", conversation_generation: "generation-a" },
      resume_restart: { available: true }, draft_clear: true, after_turn: false, memory_switch: { version: 1, available: true },
      harnesses: ["claude-code", "codex"].map(name => ({ name, available: true, models: [], model_selection: { available: true }, context: name === "codex" ? "seeded_new_conversation" : "native" })),
    } });
    if (path.includes("restart-preparation")) {
      if (request.method() === "DELETE") { released++; return route.fulfill({ json: { released: true } }); }
      if (request.method() === "POST") binding = request.postDataJSON().binding;
      return route.fulfill({ json: preparation() });
    }
    if (request.method() === "POST") submitted++;
    return route.fulfill({ json: { can_restart: true, draft_clear: true, after_turn: false, model: "current-model" } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const row = page.locator(".rd-row", { has: page.getByText("Dismiss review", { exact: true }) });
    await row.locator(".rd-row-name").click();
    for (const action of ["Change harness", "Restart"]) {
      for (const dismissal of ["Cancel", "Escape", "Close", "Backdrop"]) {
        const menu = await sessionMenu(page);
        await menu.getByRole("menuitem", { name: action, exact: true }).click();
        const dialog = page.getByRole("dialog", { name: action === "Restart" ? "Restart session" : action, exact: true });
        await expect(dialog).toBeVisible();
        if (action === "Change harness") await expect(dialog.getByText("Preparing the handoff…", { exact: true })).toBeVisible();
        if (dismissal === "Escape") await page.keyboard.press("Escape");
        else if (dismissal === "Backdrop") await page.locator(".rd-restart-backdrop").click({ position: { x: 5, y: 5 } });
        else await dialog.getByRole("button", { name: dismissal === "Cancel" ? "Cancel" : action === "Restart" ? "Close Restart" : "Close Change harness", exact: true }).click();
        await expect(dialog).toHaveCount(0);
        await expect(page.getByRole("menu", { name: "Actions for Dismiss review" })).toHaveCount(0);
        await expect(row).toBeFocused();
        expect(submitted).toBe(0);
      }
    }
    await expect.poll(() => released).toBe(4);
  } finally { await apiDelete(`/sessions/${key}`); }
});
