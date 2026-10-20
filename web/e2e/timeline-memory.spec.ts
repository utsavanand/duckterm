import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("Timeline opens an exact historical revision; the session panel still opens current memory", async ({ page }) => {
  const key = `e2e-timeline-memory-${Date.now()}`;
  await seedSession(key, { name: "Timeline memory acceptance" });
  let checkpoints = 0;
  const reads: string[] = [];
  page.on("request", request => { if (request.method() === "POST" && request.url().endsWith("/checkpoint")) checkpoints++; });
  const now = Date.now(), clock = { state: "known", succeeded_at: now, checkpoint_id: "newer" };
  const checkpoint = { id: "older", label: "Earlier saved checkpoint", created_at: now - 60000, summary: "Earlier checkpoint summary", summary_update: { state: "updated", revision_id: "older-revision" }, record: { summary_ref: "older-revision", prompts: [], files: [], tools: [], event_count: 1 } };
  try {
    await page.route(`**/sessions/${key}/checkpoints`, route => route.fulfill({ json: { checkpoints: [checkpoint], checkpoint_clock: clock } }));
    await page.route(`**/sessions/${key}/timeline?*`, route => route.fulfill({ json: { summary: { total: 1, counts: {}, text: "", updated_at: now }, entries: [{ id: "entry", ts: checkpoint.created_at, kind: "checkpoint", one_line: checkpoint.label, detail: { text: checkpoint.label }, refs: [{ source: "checkpoints", id: "older" }] }], next_cursor: null } }));
    // Presentation fixture; backend evidence authorization is tested separately.
    await page.route(`**/sessions/${key}/memory**`, route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/read")) return route.fulfill({ json: { source: "conversation:old", text: "Original evidence for the earlier saved decision.", next_offset: null } });
      if (url.searchParams.get("checkpoint") === "older") return route.fulfill({ json: {
        scope: "checkpoint", session_key: key, checkpoint: { id: "older", label: "Earlier saved checkpoint", created_at: now - 60000,
          summary_update: { state: "failed", reason: "source_changed" },
          attempt_coverage: { state: "complete", scope: "refresh_attempt", eligible_records: 20, summarized_records: 20, pending_records: 0 },
          batch_coverage_known: true, combined_summary: null, combined_summary_reason: "independent_batch_summaries",
          pagination: { snapshot: "exact-attempt", total: 1, offset: 0, returned: 1, next_cursor: null },
          batches: [{ revision_id: "older-revision", state: "available", revision: { id: "older-revision", created_at: now - 60000,
            summary: "Saved batch from the failed checkpoint.", overview: null, groups: [] } }],
        },
      } });
      const revision = url.searchParams.get("revision") || ""; reads.push(revision);
      const historical = revision === "older-revision";
      return route.fulfill({ json: {
        state: "available", current_revision_id: "newer-revision", checkpoint_clock: clock,
        revisions: [{ id: "newer-revision", created_at: now }], revisions_truncated: true,
        revision: { id: historical ? "older-revision" : "newer-revision", source: "revision:example", created_at: historical ? now - 60000 : now, is_current: !historical, verified_at_save: true,
          summary: historical ? "Historical summary from the clicked checkpoint." : "Newer current summary.", overview: null, coverage_at_save: {}, withheld_claims: 0,
          groups: [{ field: "decisions", claims: [{ record: "decision:0", text: "Keep the earlier decision linked.", evidence: [{ source: "conversation:old", record: "turn-1", title: "Earlier decision evidence", kind: "conversation", role: "user", retained: true }] }] }],
        },
      } });
    });
    await page.addInitScript(() => { localStorage.setItem("rd.contextView", "session"); });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.locator(".rd-row", { hasText: "Timeline memory acceptance" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await page.getByText("Earlier saved checkpoint", { exact: true }).click();
    await page.getByRole("button", { name: "Read saved summary and evidence ↗" }).click();
    const memory = page.getByRole("region", { name: "Session memory" });
    await expect(memory.getByText("Historical summary from the clicked checkpoint.")).toBeVisible();
    await expect(memory.getByRole("combobox")).toHaveValue("older-revision");
    expect(reads).toEqual(["older-revision"]);
    await memory.getByText("Earlier decision evidence · conversation").click();
    await expect(memory.getByText("Original evidence for the earlier saved decision.")).toBeVisible();
    await page.screenshot({ path: "/tmp/timeline-memory-implemented.png" });
    await memory.getByRole("button", { name: "← Timeline" }).click();
    await expect(page.getByRole("region", { name: "Session timeline" })).toBeVisible();
    await page.getByRole("button", { name: "Read saved memory ↗", exact: true }).click();
    await expect(memory.getByText("Newer current summary.")).toBeVisible();
    await expect(memory.getByRole("combobox")).toHaveValue("");
    expect(reads).toEqual(["older-revision", ""]);
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await page.getByRole("button", { name: "Read checkpoint result ↗" }).first().click();
    await expect(memory.getByText("Saved batch from the failed checkpoint.")).toBeVisible();
    await expect(memory.getByText("Checkpoint failed", { exact: true })).toBeVisible();
    await page.screenshot({ path: "/tmp/whole-checkpoint-implemented.png" });
    await memory.getByRole("button", { name: "Open summary older-re ↗" }).click();
    await expect(memory.getByText("Historical summary from the clicked checkpoint.")).toBeVisible();
    expect(checkpoints).toBe(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
