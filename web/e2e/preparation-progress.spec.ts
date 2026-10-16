import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

test("harness preparation shows measured progress and recovers status without restarting work", async ({ page }) => {
  const key = await seedSession("preparation-progress-test", { name: "Progress review", runtime: "claude-code", launched: true, test: true });
  let binding: unknown, posts = 0, switches = 0, sequence = 0;
  let disconnected = false, ready = false;
  let progress: Record<string, unknown> = { stage: "capturing", started_at: Date.now(), updated_at: Date.now() };
  const packet = () => ({ version: 1, preparation_id: "prep-progress", sequence: ++sequence, binding,
    state: ready ? "ready" : "preparing", progress, coverage: { state: "complete", available_text: "processed", retrieval: "available",
      retention: "retained_snapshot", source_count: 1, covered_source_count: 1, gap_count: 0, gaps: [], has_more: false, details_cursor: null },
    ...(ready ? { proof: { snapshot_id: "snapshot1", revision_id: "rev1", prepared_at: Date.now(), expires_at: Date.now() + 60000,
      overview: "Continue the keyboard navigation work.", resolved_model: "gpt-6-astra" } } : {}) });
  try {
    await page.route(`**/sessions/${key}/checkpoint-plan`, route => route.fulfill({ json: {
      available_records: 143, processed_records: 0, pending_records: 143, default_records: 20,
      selected_records: 20, remaining_records: 123, batch_records: 20,
      order: "oldest_unprocessed_first", selection_plan: { version: 1 },
    } }));
    await page.route(`**/sessions/${key}/restart**`, async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = (value: unknown) => route.fulfill({ json: value });
      if (path.endsWith("restart-options")) return json({ current: { harness: "claude-code", model: "claude-opus-5", conversation_generation: "source-a" },
        memory_switch: { version: 1, available: true }, supports_interrupt_switch: true, draft_clear: true, after_turn: true,
        resume_restart: { available: true }, harnesses: [
          { name: "claude-code", available: true, models: [{ id: "claude-opus-5", label: "claude-opus-5" }], model_selection: { available: true }, context: "native" },
          { name: "codex", available: true, models: [{ id: "gpt-6-astra", label: "gpt-6-astra" }], model_selection: { available: true }, context: "seeded_new_conversation" },
        ] });
      if (path.endsWith("restart-preparation") && request.method() === "POST") {
        posts++; binding = request.postDataJSON().binding; return json(packet());
      }
      if (path.includes("restart-preparation/")) {
        if (request.method() === "DELETE") return json({ released: true });
        if (disconnected) return route.abort("failed");
        return json(packet());
      }
      if (request.method() === "POST") switches++; return json({});
    });
    await page.setViewportSize({ width: 1320, height: 1000 });
    await page.addInitScript(() => { if (!localStorage.getItem("rd-theme")) localStorage.setItem("rd-theme", "dark"); });
    await page.goto(base()); await page.locator(".rd-row-name", { hasText: "Progress review" }).click();
    await page.getByRole("region", { name: "Session controls" }).getByRole("button", { name: "Change harness", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Change harness", exact: true });
    await dialog.getByRole("button", { name: "Prepare handoff" }).click();
    await expect(dialog.getByText("Reading the conversation", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    progress = { ...progress, stage: "checking", completed: 12, total: 20, updated_at: Date.now() };
    await expect(dialog.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "12");
    await page.screenshot({ path: "/tmp/harness-progress-implemented-dark.png" });
    progress = { ...progress, stage: "reviewing", started_at: Date.now() - 130000, updated_at: Date.now() - 125000 };
    await expect(dialog.getByText(/No progress update for/)).toContainText("server is responding");
    disconnected = true;
    await expect(dialog.getByText("Connection interrupted", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "12");
    disconnected = false; progress = { ...progress, stage: "assembling", completed: 20, updated_at: Date.now() };
    await dialog.getByRole("button", { name: "Check connection", exact: true }).click();
    await expect(dialog.getByText("Assembling the handoff", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Switch to Codex", exact: true })).toBeDisabled();
    expect(posts).toBe(1); expect(switches).toBe(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/harness-progress-implemented-narrow.png" });
    const bounds = await dialog.boundingBox(); expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    ready = true; progress = { ...progress, stage: "ready", updated_at: Date.now() };
    await expect(dialog.getByRole("button", { name: "Switch to Codex", exact: true })).toBeEnabled();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click(); await expect(dialog).not.toBeVisible();
    expect(switches).toBe(0);
    await page.evaluate(() => localStorage.setItem("rd-theme", "light"));
    ready = false; progress = { ...progress, stage: "checking", completed: 12, updated_at: Date.now() };
    await page.setViewportSize({ width: 1320, height: 1000 }); await page.reload();
    await page.locator(".rd-row-name", { hasText: "Progress review" }).click();
    await page.getByRole("region", { name: "Session controls" }).getByRole("button", { name: "Change harness", exact: true }).click();
    await dialog.getByRole("button", { name: "Prepare handoff" }).click();
    await expect(dialog.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "12");
    await page.screenshot({ path: "/tmp/harness-progress-implemented-light.png" });
  } finally { await apiDelete(`/sessions/${key}`); }
});
