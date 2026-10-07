import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, postEvent, seedSession, sessionMenu } from "./helpers";

test("approved preparation dialog and milestone Timeline preserve existing controls", async ({ page }) => {
  const key = await seedSession("memory-ui-test", { name: "Workspace developer", runtime: "claude-code", launched: true, test: true });
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  let switchCount = 0, releaseCount = 0;
  let current: Record<string, unknown> = { can_restart: true, draft_clear: true, after_turn: true, model: "claude-opus-5" };
  const preparations = new Map<string, Record<string, unknown>>();
  try {
    await postEvent({ event_type: "UserPromptSubmit", session_key: key, prompt: "Ordinary prompt belongs only in Messages" });
    await postEvent({ event_type: "PreToolUse", session_key: key, tool_name: "Bash" });
    await apiPost(`/sessions/${key}/checkpoint`, { label: "Before keyboard review" });
    await page.route(`**/sessions/${key}/restart**`, async route => {
      const request = route.request(), url = new URL(request.url());
      const json = (value: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
      if (url.pathname.endsWith("restart-options")) return json({ current: { harness: "claude-code", model: "claude-opus-5", conversation_generation: "source-a" },
        memory_switch: { version: 1, available: true }, supports_interrupt_switch: true,
        draft_clear: true, after_turn: true, resume_restart: { available: true }, harnesses: [
          { name: "claude-code", available: true, models: [{ id: "claude-opus-5", label: "claude-opus-5" }], model_selection: { available: true }, context: "native" },
          { name: "codex", available: true, models: [{ id: "gpt-6-astra", label: "gpt-6-astra" }], model_selection: { available: true }, context: "seeded_new_conversation" },
        ] });
      if (url.pathname.endsWith("restart-preparation") && request.method() === "POST") {
        const body = request.postDataJSON(), id = String(preparations.size + 1).padStart(32, "a");
        const value = { version: 1, preparation_id: id, request_key: body.request_key, binding: body.binding, sequence: 1, state: "ready",
          coverage: { state: "partial", available_text: "processed", retrieval: "available", retention: "retained_snapshot", source_count: 3, covered_source_count: 3,
            gaps: [{ kind: "unread_attachment", reason: "One image remains available as an attachment; its contents were not interpreted.", blocking: false }], gap_count: 1, has_more: false, details_cursor: null },
          proof: { snapshot_id: "snapshot-a", revision_id: "revision-a", prepared_at: Date.now(), expires_at: Date.now() + 60000,
            overview: "Continue keyboard accessibility work.", resolved_model: body.binding.target.model.id ?? null } };
        preparations.set(id, value); return json(value);
      }
      if (url.pathname.includes("restart-preparation/")) {
        if (request.method() === "DELETE") { releaseCount++; return json({ released: true }); }
        const id = url.pathname.split("/").at(-1)!;
        if (url.searchParams.get("detail") === "full") {
          const text = "Continue the keyboard accessibility work.\n\nOwner constraint: keep the existing panel controls.\nNext: verify focus return and narrow layouts.\n\nUse retained sources for earlier decisions.\n".repeat(8);
          return json({ preparation_id: id, snapshot_id: "snapshot-a", brief: { text, utf8_bytes: Buffer.byteLength(text), budget_bytes: 32000 }, sources: [{ source_id: "source-a", source_version: "hash-a", read_handle: "source-a:hash-a" }], gaps: [], next_cursor: null });
        }
        return json(preparations.get(id));
      }
      if (request.method() === "POST") {
        switchCount++; const body = request.postDataJSON();
        expect(body.harness).toBe("codex"); expect(body.model).toBe("gpt-6-astra"); expect(body.interrupt).toBe(true);
        current = { ...current, status: "queued", id: "operation-a", memory: body.memory, requested_harness: body.harness,
          requested_model: body.model, interrupt: true, context: "seeded_new_conversation", process_state: "source_running" };
        return json({ id: "operation-a", request_key: body.request_key, preparation_id: body.memory.preparation_id, sequence: 1,
          binding: preparations.get(body.memory.preparation_id)!.binding, status: "queued", can_cancel: true, process_state: "source_running" });
      }
      if (request.method() === "DELETE") { expect(url.searchParams.get("operation_id")).toBe("operation-a"); current = { ...current, status: "canceled" }; }
      return json(current);
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.goto(base()); await page.locator(".rd-row-name", { hasText: "Workspace developer" }).click();
    const menu = await sessionMenu(page);
    for (const name of ["Restart", "Change model", "Checkpoint", "Notes", "Stop", "Archive"]) await expect(menu.getByRole("menuitem", { name, exact: true })).toBeVisible();
    await menu.getByRole("menuitem", { name: "Restart", exact: true }).click();
    await page.getByRole("dialog").getByRole("combobox", { name: "Harness", exact: true }).selectOption("codex");
    await page.getByRole("dialog").getByRole("combobox", { name: "Model", exact: true }).selectOption("gpt-6-astra");
    await expect(page.getByText("Ready to switch", { exact: true })).toBeVisible(); expect(switchCount).toBe(0);
    await page.screenshot({ path: "/tmp/memory-switch-implemented-dark.png" });
    await page.getByText("Handoff brief and available context", { exact: true }).click();
    await expect(page.getByRole("region", { name: "Automatically prepared brief" })).toContainText("Owner constraint");
    await page.screenshot({ path: "/tmp/memory-switch-implemented-details.png" });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/memory-switch-implemented-light.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("button", { name: "Switch to Codex", exact: true })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/memory-switch-implemented-narrow.png" });
    await page.getByRole("button", { name: "Switch to Codex", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "Switch to Codex", exact: true })).toBeInViewport();
    await page.screenshot({ path: "/tmp/memory-switch-implemented-narrow-actions.png" });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("checkbox", { name: "Stop the current turn and switch now" }).check();
    await page.getByRole("button", { name: "Stop and switch now", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0); expect(switchCount).toBe(1);
    await expect.poll(() => releaseCount).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Cancel restart", exact: true }).click();
    await expect(page.getByRole("button", { name: "Cancel restart", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await expect(page.getByText("Before keyboard review", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Session timeline" }).getByText("Owner message", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Session timeline" }).getByRole("article")).toHaveCount(1);
    await page.screenshot({ path: "/tmp/timeline-milestones-implemented.png" });
    await page.getByRole("button", { name: "Progress", exact: true }).click();
    await expect(page.getByRole("region", { name: "Session timeline" }).getByRole("article")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally { await apiDelete(`/sessions/${key}`); }
});
