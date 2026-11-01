import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("saved Memory opens from the session panel and reads original evidence without checkpointing", async ({ page }) => {
  const key = `e2e-memory-${Date.now()}`;
  await seedSession(key, { name: "Memory acceptance", group: "Memory QA" });
  let checkpoints = 0;
  page.on("request", request => { if (request.method() === "POST" && request.url().endsWith("/checkpoint")) checkpoints++; });
  const clock = { state: "known", succeeded_at: Date.now() - 3 * 86400000, checkpoint_id: "saved" };
  try {
    // Display fixture only. Provider behavior and evidence authorization are
    // covered by backend tests; no LLM call or production session is involved.
    await page.route(`**/sessions/${key}/checkpoints`, route => route.fulfill({ json: { checkpoints: [], checkpoint_clock: clock } }));
    await page.route(`**/sessions/${key}/memory?revision=*`, route => route.fulfill({ json: {
      state: "available", current_revision_id: "revision-one", checkpoint_clock: clock,
      revisions: [{ id: "revision-one", created_at: clock.succeeded_at }], revisions_truncated: false,
      revision: { id: "revision-one", source: "revision:one", created_at: clock.succeeded_at, is_current: true, verified_at_save: true,
        summary: "The owner chose manual checkpoints. A checkpoint updates saved memory; merely opening this view does not generate a new summary.",
        overview: "Keep saved decisions linked to their original evidence.", coverage_at_save: {}, withheld_claims: 0,
        groups: [{ field: "decisions", claims: [{ record: "decision:0", text: "Keep checkpoints manual.", kind: "decision", state: "open", evidence: [{ source: "conversation:one", record: "turn-1", title: "Owner decision", kind: "conversation", role: "user", retained: true }] }] }],
      },
    } }));
    await page.route(`**/sessions/${key}/memory/revisions?*`, route => route.fulfill({ json: { revisions: [{ id: "revision-one", created_at: clock.succeeded_at }], next_cursor: null } }));
    await page.route(`**/sessions/${key}/memory/read?*`, route => route.fulfill({ json: { source: "conversation:one", text: "Please keep checkpoint generation manual.", next_offset: null } }));
    await page.addInitScript(() => { localStorage.setItem("rd.contextView", "session"); localStorage.setItem("rd.collapsedGroups", "[]"); });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Memory QA" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    await page.locator(".rd-row", { hasText: "Memory acceptance" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: "View memory ↗" }).click();
    const memory = page.getByRole("region", { name: "Session memory" });
    await expect(memory.getByText("Keep checkpoints manual.")).toBeVisible();
    await expect(memory.getByText("Last checkpointed 3d ago")).toBeVisible();
    await memory.getByText("Evidence: 1 message", { exact: true }).click();
    await memory.getByText("user · #turn-1", { exact: true }).click();
    await expect(memory.getByText("Please keep checkpoint generation manual.")).toBeVisible();
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `/tmp/memory-screen-${colorScheme}.png` });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await memory.scrollIntoViewIfNeeded();
    expect(await memory.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/memory-screen-mobile.png" });
    expect(checkpoints).toBe(0);
  } finally { await apiDelete(`/sessions/${key}`); }
});
