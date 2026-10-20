import { expect, test } from "@playwright/test";
import { apiDelete, checkpoints, sessionMenu } from "./helpers";
import { seedCheckpointSession } from "./checkpoint-fixture";

test("checkpoint dialog counts real pending records, cancels cleanly and sends the confirmed selection", async ({ page }) => {
  const key = `e2e-checkpoint-choice-${Date.now()}`;
  await seedCheckpointSession(key, { name: "Checkpoint selection", group: "Memory review" }, 47);
  const posts: Record<string, unknown>[] = [];
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith(`/sessions/${key}/checkpoint`)) posts.push(request.postDataJSON());
  });
  try {
    await page.addInitScript(() => localStorage.setItem("rd.collapsedGroups", "[]"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Memory review" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    await page.locator(".rd-row", { hasText: "Checkpoint selection" }).locator(".rd-row-click").click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Checkpoint", exact: true });
    await expect(dialog.getByRole("button", { name: "Checkpoint 20 records" })).toBeEnabled();
    await expect(dialog.getByRole("radio", { name: "Latest 20 · your configured default" })).toBeChecked();
    await expect(dialog.getByText(/records selected/)).toContainText("20 records selected · 27 left for later");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Checkpoint progress" })).toHaveCount(0);
    expect(await checkpoints(key)).toHaveLength(0);
    for (const colorScheme of ["dark", "light"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.screenshot({ path: `/tmp/checkpoint-dialog-${colorScheme}.png` });
    }
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0); await expect(page.getByRole("menu")).toHaveCount(0);
    expect(posts).toHaveLength(0); expect(await checkpoints(key)).toHaveLength(0);

    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Checkpoint 20 records" })).toBeEnabled();
    await dialog.getByRole("spinbutton").fill("0");
    await expect(dialog.getByRole("button", { name: "Checkpoint", exact: true })).toBeDisabled();
    await dialog.getByRole("spinbutton").fill("35");
    await expect(dialog.getByText(/records selected/)).toContainText("35 records selected · 12 left for later");
    await dialog.getByRole("radio", { name: "All 47 unprocessed records" }).check();
    await expect(dialog.getByText(/records selected/)).toContainText("47 records selected · 0 left for later");
    await page.setViewportSize({ width: 390, height: 844 });
    await dialog.getByRole("button", { name: "Checkpoint 47 records" }).scrollIntoViewIfNeeded();
    expect(await dialog.locator("form").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/checkpoint-dialog-mobile.png" });
    await dialog.getByRole("button", { name: "Checkpoint 47 records" }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]).toMatchObject({ label: "manual", selection: { records: "all", plan: { version: 2, order: "newest_unprocessed_first" } } });
    expect(typeof posts[0].request_id).toBe("string");
    // The fixture provider deliberately produces invalid summaries. The real
    // server still returns a durable failed attempt for this exact selection.
    await expect.poll(async () => (await checkpoints(key)).length).toBe(1);
  } finally { await apiDelete(`/sessions/${key}`); }
});
