import { seedCheckpointSession, confirmCheckpoint } from "./checkpoint-fixture";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, checkpoints, sessionMenu } from "./helpers";

test("checkpoint progress opens the Session panel immediately and survives navigation", async ({ page }) => {
  const key = `e2e-progress-${Date.now()}`, other = key + "-other";
  await seedCheckpointSession(key, { name: "Checkpoint review", group: "Checkpoint QA" });
  await seedCheckpointSession(other, { name: "Other checkpoint session", group: "Checkpoint QA" });
  try {
    await apiPost(`/sessions/${key}/checkpoint`, { label: "Earlier attempt" });
    const earlier = (await checkpoints(key))[0];
    let release!: () => void;
    const held = new Promise<void>(resolve => { release = resolve; });
    let posts = 0;
    // Delay a real backend response; do not invent an outcome or saved record.
    await page.route(`**/sessions/${key}/checkpoint`, async route => {
      posts++;
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    });
    await page.addInitScript(() => {
      localStorage.setItem("rd.contextView", "connectors");
      localStorage.setItem("rd.panelsCollapsed", JSON.stringify({ right: true }));
      localStorage.setItem("rd.collapsedGroups", "[]");
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Checkpoint QA" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    const row = page.locator(".rd-row", { hasText: "Checkpoint review" });
    await row.locator(".rd-row-click").click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    await confirmCheckpoint(page);
    const progress = page.getByRole("region", { name: "Checkpoint progress" });
    await expect(progress.getByRole("progressbar")).toBeVisible();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Session", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(progress.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    await expect(progress.getByLabel("Elapsed time")).toHaveCount(0);
    await expect((await sessionMenu(page)).getByRole("menuitem", { name: "Updating summary…" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await expect(progress.locator(".rd-checkpoint-progress-track span")).toHaveCSS("animation-name", "none");
    await page.screenshot({ path: "/tmp/checkpoint-progress-implemented-dark.png" });
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/checkpoint-progress-implemented-light.png" });
    await page.locator(".rd-row", { hasText: "Other checkpoint session" }).locator(".rd-row-click").click();
    await expect(progress).toHaveCount(0);
    await row.locator(".rd-row-click").click();
    await expect(progress.getByRole("progressbar")).toBeVisible();
    expect(posts).toBe(1);
    release();
    await expect(progress.getByRole("status")).toHaveText("Summary update failed");
    await expect(progress.getByRole("progressbar")).toHaveCount(0);
    await expect(progress.getByRole("button", { name: "Details ↗" })).toBeVisible();
    const records = await checkpoints(key);
    expect(records).toHaveLength(2);
    expect(records.find(cp => cp.id === earlier.id)).toEqual(earlier);
    await page.setViewportSize({ width: 390, height: 844 });
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/checkpoint-progress-implemented-mobile.png" });
    expect(await progress.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); await apiDelete(`/sessions/${other}`); }
});


test("a saved partial checkpoint reports incomplete failure instead of success", async ({ page }) => {
  const key = `e2e-partial-${Date.now()}`;
  await seedCheckpointSession(key, { name: "Incomplete checkpoint", group: "Checkpoint QA" });
  try {
    // The observed attempt saved a reviewed draft but had incomplete coverage.
    // This response fixture exercises display only; it does not claim a backend repair.
    await page.route(`**/sessions/${key}/checkpoint`, route => route.fulfill({ json: {
      id: "partial-fixture", label: "manual", created_at: Date.now(), saved: true,
      format: "checkpoint_marker_v2", summary: "Reviewed draft with incomplete history",
      summary_state: "ready", handoff_eligible: false,
      summary_update: { state: "partial", reason: "summary_coverage_partial" },
      reason_codes: ["summary_coverage_partial"], coverage: { state: "retained" },
      record: { summary_ref: "partial-revision", prompts: [], files: [], tools: [], event_count: 0 },
    } }));
    await page.addInitScript(() => localStorage.setItem("rd.collapsedGroups", "[]"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Checkpoint QA" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    const row = page.locator(".rd-row", { hasText: "Incomplete checkpoint" });
    await row.locator(".rd-row-click").click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    await confirmCheckpoint(page);
    const progress = page.getByRole("region", { name: "Checkpoint progress" });
    await expect(progress.getByRole("status")).toHaveText("Checkpoint failed · summary incomplete");
    await expect(progress).toHaveClass(/rd-checkpoint-progress-failure/);
    await expect(progress.getByRole("button", { name: "Details ↗" })).toBeVisible();
    await expect(progress.getByRole("progressbar")).toHaveCount(0);
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `/tmp/checkpoint-partial-${colorScheme}.png` });
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/checkpoint-partial-mobile.png" });
    expect(await progress.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("checkpoint displays checked work instead of elapsed time at real panel sizes", async ({ page }) => {
  const key = `e2e-measured-${Date.now()}`;
  await seedCheckpointSession(key, { name: "Progress review", group: "Checkpoint QA" });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let completed = 12, stage = "reviewing";
  let requestId = "";
  try {
    await page.route(`**/sessions/${key}/checkpoint`, async route => {
      requestId = route.request().postDataJSON().request_id;
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    });
    // Real response outcome above; progress fixture exercises intermediate UI.
    // Backend progress counts and shared-writer boundaries are tested in Python.
    await page.route(`**/sessions/${key}/checkpoint-attempts/*`, route => route.fulfill({ json: {
      request_id: requestId, state: "running", progress: { stage, completed, total: 40 },
    } }));
    await page.addInitScript(() => localStorage.setItem("rd.collapsedGroups", "[]"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Checkpoint QA" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    await page.locator(".rd-row", { hasText: "Progress review" }).locator(".rd-row-click").click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    await confirmCheckpoint(page);
    const progress = page.getByRole("region", { name: "Checkpoint progress" });
    const bar = progress.getByRole("progressbar");
    await expect(bar).toHaveAttribute("aria-valuenow", "12");
    await expect(bar).toHaveAttribute("aria-valuemax", "40");
    await expect(progress.getByRole("status")).toHaveText("Reviewing summary… · 12 of 40 records");
    await expect(progress.getByLabel("Elapsed time")).toHaveCount(0);
    await expect(bar.locator("span")).toHaveCSS("animation-name", "none");
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `/tmp/checkpoint-measured-${colorScheme}.png` });
    }
    completed = 28; stage = "writing";
    await expect(bar).toHaveAttribute("aria-valuenow", "28");
    await page.setViewportSize({ width: 390, height: 844 });
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/checkpoint-measured-mobile.png" });
    expect(await progress.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    completed = 40; stage = "saving";
    await expect(progress.getByRole("status")).toHaveText("Saving checkpoint… · 40 of 40 records");
    await expect(progress.getByText(/Summary updated/)).toHaveCount(0);
    release();
    await expect(progress.getByRole("status")).toHaveText("Summary update failed");
    await expect(bar).toHaveCount(0);
  } finally { release?.(); await apiDelete(`/sessions/${key}`); }
});
