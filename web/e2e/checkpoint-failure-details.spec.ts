import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("failed checkpoint shows attempt-only evidence and opens exact revisions without checkpoint writes", async ({ page }) => {
  const key = `e2e-failed-checkpoint-${Date.now()}`;
  await seedSession(key, { name: "Failed checkpoint evidence" });
  let allSaved = false, posts = 0;
  const reads: string[] = [];
  const now = Date.now(), clock = { state: "known", succeeded_at: now - 3600000, checkpoint_id: "previous" };
  const checkpoint = () => ({
    id: "failed-attempt", label: "manual", created_at: now, saved: true,
    summary_state: "unavailable", handoff_eligible: false, summary_source_at: clock.succeeded_at,
    summary_update: { state: "failed", reason: allSaved ? "source_changed" : "summary_unverified", revision_id: null,
      span_coverage: { state: "incomplete", scope: "refresh_attempt", available_records: 20, eligible_records: 20, summarized_records: allSaved ? 20 : 12, pending_records: allSaved ? 0 : 8, reference_only_records: 0, revision_ids: ["attempt-first-revision", "attempt-second-revision"] } },
    record: { summary_ref: null, prompts: [], files: [], tools: [], event_count: 28 },
  });
  page.on("request", r => { if (r.method() === "POST" && r.url().endsWith("/checkpoint")) posts++; });
  try {
    await page.route(`**/sessions/${key}/checkpoints`, r => r.fulfill({ json: { checkpoints: [checkpoint()], checkpoint_clock: clock, checkpoint_activity: { state: "known", events_since_capture: 28 } } }));
    await page.route(`**/sessions/${key}/memory**`, r => {
      const url = new URL(r.request().url());
      if (url.pathname.endsWith("/revisions")) return r.fulfill({ json: { revisions: [{ id: "previous-revision", created_at: now - 3600000 }], next_cursor: null } });
      if (url.searchParams.has("checkpoint")) return r.fulfill({ json: { scope: "checkpoint", session_key: key, checkpoint: {
        id: "failed-attempt", label: "manual", created_at: now, summary_update: checkpoint().summary_update,
        attempt_coverage: { state: allSaved ? "complete" : "incomplete", scope: "refresh_attempt", eligible_records: 20, summarized_records: allSaved ? 20 : 12, pending_records: allSaved ? 0 : 8 },
        batch_coverage_known: true, pagination: { snapshot: "attempt", total: 2, offset: 0, returned: 2, next_cursor: null },
        batches: ["attempt-first-revision", "attempt-second-revision"].map(id => ({ revision_id: id, state: "available", revision: { id, created_at: now, summary: "Exact attempt batch evidence.", overview: null, groups: [] } })),
      } } });
      const revision = url.searchParams.get("revision") || ""; reads.push(revision);
      return r.fulfill({ json: { state: "available", current_revision_id: "previous-revision", checkpoint_clock: clock, latest_checkpoint: checkpoint(), revisions: [{ id: "previous-revision", created_at: now - 3600000 }],
        revision: { id: revision || "previous-revision", source: "revision:test", created_at: now, summary: revision ? "Exact attempt batch evidence." : "Current memory retained.", overview: null, groups: [], withheld_claims: 0, coverage_at_save: {} } } });
    });
    await page.addInitScript(() => localStorage.setItem("rd.contextView", "session"));
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.goto("/");
    await page.locator(".rd-row", { hasText: "Failed checkpoint evidence" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: /Latest checkpoint/ }).click();
    const memory = page.getByRole("region", { name: "Session memory" });
    await expect(memory.getByText("12 of 20", { exact: true })).toBeVisible();
    await expect(memory.getByText("Still need summaries").locator("+ dd")).toHaveText("8");
    await memory.getByRole("button", { name: "Open summary attempt- ↗" }).nth(1).click();
    await expect(memory.getByText("Exact attempt batch evidence.")).toBeVisible();
    await expect(memory.getByRole("combobox")).toHaveValue("attempt-second-revision");
    expect(reads).toEqual(["attempt-second-revision"]);
    await page.getByRole("button", { name: "View memory ↗", exact: true }).click();
    await expect(memory.getByRole("combobox")).toHaveValue("previous-revision");
    expect(reads).toEqual(["attempt-second-revision", "previous-revision"]);
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    await page.screenshot({ path: "/tmp/checkpoint-failure-ui-batch.png" });
    allSaved = true;
    await page.reload();
    await page.locator(".rd-row", { hasText: "Failed checkpoint evidence" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: /Latest checkpoint/ }).click();
    await expect(memory.getByText("20 of 20", { exact: true })).toBeVisible();
    await expect(memory.getByText("The final source check failed", { exact: true })).toBeVisible();
    await expect(memory.getByText(/not ready for switching/)).toBeVisible();
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await expect(page.locator("html")).toHaveAttribute("data-theme", colorScheme);
      await page.screenshot({ path: `/tmp/checkpoint-failure-ui-${colorScheme}.png` });
    }
    expect(posts).toBe(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
