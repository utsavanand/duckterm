import { sessionMenu } from "./helpers";
import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("Restart dialog edits model, queues visibly, survives reload, and cancels", async ({ page }) => {
  const key = `e2e-restart-${Date.now()}`;
  await seedSession(key, { name: "Restart review", runtime: "codex", launched: true });
  let state = { can_restart: true, draft_clear: true, after_turn: true, model: "current-model", status: "", requested_model: "" };
  const submitted: string[] = [];
  await page.route(`**/sessions/${key}/restart`, async route => {
    const method = route.request().method();
    if (method === "POST") { const data = route.request().postDataJSON(); submitted.push(data.model); state = { ...state, status: "queued", requested_model: data.model }; }
    if (method === "DELETE") state = { ...state, status: "canceled" };
    await route.fulfill({ json: state });
  });
  await page.route(`**/sessions/${key}/restart-options`, route => route.fulfill({ json: {
    current: { harness: "codex", model: "current-model" }, resume_restart: { available: true }, draft_clear: state.draft_clear, after_turn: true,
    harnesses: [{ name: "codex", available: true, context: "native", model_selection: { available: true }, models: [{ id: "current-model", label: "Current model 1.0" }, { id: "new-model", label: "New model 2.0" }] }],
  } }));
  let catalogUnavailable = true;
  await page.route(`**/sessions/${key}/models`, route => catalogUnavailable
    ? route.fulfill({ status: 503, json: { error: "Could not read model choices. Check CLI sign-in and retry." } })
    : route.fulfill({ json: { models: [{ id: "current-model", label: "Current model 1.0" }, { id: "new-model", label: "New model 2.0" }] } }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByText("Restart review", { exact: true }).first().click();
    const restart = (await sessionMenu(page)).getByRole("menuitem", { name: "Restart…", exact: true });
    await expect(restart).toBeEnabled();
    const change = (await sessionMenu(page)).getByRole("menuitem", { name: "Change model…", exact: true });
    await change.click();
    await expect(page.getByRole("menu", { name: "Choose model" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveText("Could not read model choices. Check CLI sign-in and retry.");
    expect(submitted).toHaveLength(0);
    catalogUnavailable = false;
    await page.getByRole("menuitem", { name: "Retry model list" }).click();
    await expect(page.getByRole("menuitemradio", { name: /New model 2.0/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(change).toBeFocused();
    await change.click();
    await page.getByRole("menuitemradio", { name: /New model 2.0/ }).click();
    await expect(page.getByRole("dialog", { name: "Restart session" })).toBeVisible();
    await page.keyboard.press("Escape");
    expect(submitted).toHaveLength(0);
    await sessionMenu(page);
    await restart.click();
    const dialog = page.getByRole("dialog", { name: "Restart session" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Model")).toHaveValue("current-model");
    await dialog.getByLabel("Model").selectOption("new-model");
    await page.screenshot({ path: "/tmp/duckterm-f15-implemented-dialog.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "/tmp/duckterm-f15-implemented-narrow.png" });
    await dialog.getByRole("button", { name: "Restart after this turn" }).click();
    expect(submitted).toEqual(["new-model"]);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect(page.getByText(/Restart pending/)).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Cancel restart" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel restart" }).click();
    await expect(page.getByRole("button", { name: "Cancel restart" })).toHaveCount(0);
    expect(submitted).toHaveLength(1);
    await sessionMenu(page);
    await expect(restart).toBeEnabled();
    state = { ...state, draft_clear: false };
    await sessionMenu(page);
    await restart.click();
    await expect(dialog.getByRole("button", { name: "Restart after this turn" })).toBeDisabled();
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("older backend exposes harness choices but cannot bypass memory preparation", async ({ page }) => {
  const key = `e2e-switch-${Date.now()}`;
  await seedSession(key, { name: "Harness switch review", runtime: "claude-code", launched: true });
  const submitted: { harness: string; model: string }[] = [];
  let blocked = true;
  let completed = false;
  await page.route(`**/sessions/${key}/restart`, async route => {
    if (route.request().method() === "POST") { submitted.push(route.request().postDataJSON()); completed = true; }
    await route.fulfill({ json: completed
      ? { status: "completed", context: "seeded_new_conversation", source_harness: "claude-code", requested_harness: "codex", configured_model: "gpt-6-astra", cli_version: "codex 0.155.1" }
      : { can_restart: false, reason: "Cannot verify this conversation." } });
  });
  await page.route(`**/sessions/${key}/restart-options`, route => route.fulfill({ json: {
    current: { harness: "claude-code", model: "claude-opus-5" }, resume_restart: { available: false, reason: "Cannot verify this conversation." }, draft_clear: !blocked, after_turn: false,
    harnesses: [
      { name: "claude-code", available: false, reason: "Cannot verify this conversation.", context: "native", model_selection: { available: true }, models: [{ id: "claude-opus-5", label: "Claude Opus 5" }] },
      { name: "codex", available: true, context: "seeded_new_conversation", model_selection: { available: true }, models: [{ id: "gpt-6-astra", label: "GPT-6 Astra" }] },
    ],
  } }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByText("Harness switch review", { exact: true }).first().click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Restart…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Restart session" });
    await expect(dialog.getByRole("combobox", { name: "Harness", exact: true })).toHaveValue("claude-code");
    await expect(dialog.getByRole("button", { name: "Restart now" })).toBeDisabled();
    await dialog.getByRole("combobox", { name: "Harness", exact: true }).selectOption("codex");
    await expect(dialog.getByRole("combobox", { name: "Model", exact: true })).toHaveValue("");
    await expect(dialog.getByRole("button", { name: "Switch to Codex" })).toBeDisabled();
    await expect(dialog.getByText(/Send or clear unsent/)).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    blocked = false;
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Restart…", exact: true }).click();
    await expect(dialog.getByRole("combobox", { name: "Harness", exact: true })).toBeEnabled();
    await dialog.getByRole("combobox", { name: "Harness", exact: true }).selectOption("codex");
    await dialog.getByRole("combobox", { name: "Model", exact: true }).selectOption("gpt-6-astra");
    await expect(dialog.getByText("Memory-backed switching needs a supporting backend and an available target harness.")).toBeVisible();
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await page.screenshot({ path: "/tmp/restart-switch-implemented-dark.png" });
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await page.screenshot({ path: "/tmp/restart-switch-implemented-light.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "/tmp/restart-switch-implemented-mobile.png" });
    await expect(dialog.getByRole("button", { name: "Switch to Codex" })).toBeDisabled();
    expect(submitted).toEqual([]);
    await expect(page.getByText(/Restarted — conversation continued/)).toHaveCount(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("working session offers an explicit immediate switch with truthful pending status", async ({ page }) => {
  const key = `e2e-immediate-${Date.now()}`;
  await seedSession(key, { name: "Working harness review", runtime: "claude-code", launched: true });
  const submitted: Record<string, unknown>[] = [];
  let requested = false;
  const preparationId = "b".repeat(32);
  let binding: Record<string, unknown> = {};
  await page.route(`**/sessions/${key}/restart-preparation**`, async route => {
    if (route.request().method() === "DELETE") return route.fulfill({ json: { released: true } });
    if (route.request().method() === "POST") binding = route.request().postDataJSON().binding;
    await route.fulfill({ json: { version: 1, preparation_id: preparationId, sequence: 1, state: "ready", binding,
      coverage: { state: "complete", available_text: "processed", retrieval: "available", retention: "retained_snapshot", source_count: 1, covered_source_count: 1, gap_count: 0, gaps: [], has_more: false, details_cursor: null },
      proof: { snapshot_id: "snapshot-a", revision_id: "revision-a", prepared_at: Date.now(), expires_at: Date.now() + 60000, overview: "Continue current work", resolved_model: null } } });
  });
  await page.route(`**/sessions/${key}/restart`, async route => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON(); submitted.push(body); requested = true;
      return route.fulfill({ json: { id: "operation-a", request_key: body.request_key, preparation_id: body.memory.preparation_id,
        sequence: 1, binding, status: "queued", can_cancel: true, process_state: "source_running" } });
    }
    await route.fulfill({ json: requested
      ? { status: "queued", interrupt: true, context: "seeded_new_conversation", source_harness: "claude-code", requested_harness: "codex", requested_model: "gpt-6-astra" }
      : { can_restart: false, reason: "Cannot verify this conversation." } });
  });
  await page.route(`**/sessions/${key}/restart-options`, route => route.fulfill({ json: {
    current: { harness: "claude-code", model: "claude-opus-5", conversation_generation: "generation-a" }, memory_switch: { version: 1, available: true }, resume_restart: { available: false }, draft_clear: true, after_turn: true, supports_interrupt_switch: true,
    harnesses: [
      { name: "claude-code", available: false, reason: "Cannot verify this conversation.", context: "native", model_selection: { available: true }, models: [] },
      { name: "codex", available: true, context: "seeded_new_conversation", model_selection: { available: true }, models: [{ id: "gpt-6-astra", label: "GPT-6 Astra" }] },
    ],
  } }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await page.getByText("Working harness review", { exact: true }).first().click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Restart…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Restart session" });
    await expect(dialog.getByRole("combobox", { name: "Harness", exact: true })).toBeEnabled();
    await dialog.getByRole("combobox", { name: "Harness", exact: true }).selectOption("codex");
    const checkbox = dialog.getByRole("checkbox", { name: "Stop the current turn and switch now" });
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();
    expect(submitted).toHaveLength(0);
    await expect(dialog.getByText(/DuckTerm rechecks the handoff before stopping/)).toBeVisible();
    await dialog.getByRole("combobox", { name: "Model", exact: true }).selectOption("gpt-6-astra");
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();
    await page.screenshot({ path: "/tmp/restart-immediate-implemented-dark.png", animations: "disabled" });
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
    await page.screenshot({ path: "/tmp/restart-immediate-implemented-light.png", animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "/tmp/restart-immediate-implemented-mobile.png", animations: "disabled" });
    await dialog.getByRole("button", { name: "Stop and switch now" }).click();
    expect(submitted).toHaveLength(1);
    expect(submitted[0]).toMatchObject({ model: "gpt-6-astra", harness: "codex", interrupt: true,
      request_key: expect.any(String), memory: { version: 1, preparation_id: preparationId, snapshot_id: "snapshot-a", source_generation: "generation-a" } });
    await expect(page.getByText(/Preparing to stop and switch now/)).toBeVisible();
    await expect(page.getByText(/Restart pending — after this turn/)).toHaveCount(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
