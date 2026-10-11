import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, checkpoints, seedSession, sessionMenu } from "./helpers";

test("checkpoint progress opens the Session panel immediately and survives navigation", async ({ page }) => {
  const key = `e2e-progress-${Date.now()}`, other = key + "-other";
  await seedSession(key, { name: "Checkpoint review", group: "Checkpoint QA" });
  await seedSession(other, { name: "Other checkpoint session", group: "Checkpoint QA" });
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
    const progress = page.getByRole("region", { name: "Checkpoint progress" });
    await expect(progress.getByRole("progressbar")).toBeVisible();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Session", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(progress.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    await expect(progress.getByLabel("Elapsed time")).not.toHaveText("0s");
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
    await expect(progress.getByText(/Any previously saved summary is unchanged/)).toBeVisible();
    const records = await checkpoints(key);
    expect(records).toHaveLength(2);
    expect(records.find(cp => cp.id === earlier.id)).toEqual(earlier);
    await page.setViewportSize({ width: 390, height: 844 });
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/checkpoint-progress-implemented-mobile.png" });
    expect(await progress.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); await apiDelete(`/sessions/${other}`); }
});
