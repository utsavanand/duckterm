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
      const revision = new URL(r.request().url()).searchParams.get("revision") || ""; reads.push(revision);
      return r.fulfill({ json: { state: "available", current_revision_id: "previous-revision", checkpoint_clock: clock, latest_checkpoint: checkpoint(), revisions: [{ id: "previous-revision", created_at: now - 3600000 }],
        revision: { id: revision || "previous-revision", source: "revision:test", created_at: now, summary: revision ? "Exact attempt batch evidence." : "Current memory retained.", overview: null, groups: [], withheld_claims: 0, coverage_at_save: {} } } });
    });
    await page.addInitScript(() => localStorage.setItem("rd.contextView", "session"));
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.goto("/");
    await page.locator(".rd-row", { hasText: "Failed checkpoint evidence" }).locator(".rd-row-click").click();
    const attempt = page.getByLabel("Latest checkpoint", { exact: true });
    await attempt.locator("summary").first().click();
    await expect(attempt.getByText("12 of 20 saved")).toBeVisible();
    await expect(attempt.getByText("8 entries still need summaries.")).toBeVisible();
    await attempt.getByText("Saved revisions (2)").click();
    await attempt.getByRole("button", { name: "Summary 2 · attempt- ↗" }).click();
    const memory = page.getByRole("region", { name: "Session memory" });
    await expect(memory.getByText("Exact attempt batch evidence.")).toBeVisible();
    await expect(memory.getByRole("combobox")).toHaveValue("attempt-second-revision");
    expect(reads).toEqual(["attempt-second-revision"]);
    await page.getByRole("button", { name: "Read saved memory ↗", exact: true }).click();
    await expect(memory.getByText("Current memory retained.")).toBeVisible();
    expect(reads).toEqual(["attempt-second-revision", ""]);
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    await page.screenshot({ path: "/tmp/checkpoint-failure-ui-batch.png" });
    allSaved = true;
    await page.reload();
    await page.locator(".rd-row", { hasText: "Failed checkpoint evidence" }).locator(".rd-row-click").click();
    await attempt.locator("summary").first().click();
    await expect(attempt.getByText("20 of 20 saved")).toBeVisible();
    await expect(attempt.getByText(/final source check failed/)).toBeVisible();
    await expect(attempt.getByText(/Saved memory may have advanced/)).toBeVisible();
    await expect(attempt.getByText(/entries still need|unchanged|Kept ·/)).toHaveCount(0);
    await expect(attempt.getByText("This checkpoint failed. It is not ready for switching.")).toBeVisible();
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await expect(page.locator("html")).toHaveAttribute("data-theme", colorScheme);
      await page.screenshot({ path: `/tmp/checkpoint-failure-ui-${colorScheme}.png` });
    }
    expect(posts).toBe(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
