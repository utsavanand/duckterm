import { seedCheckpointSession, confirmCheckpoint } from "./checkpoint-fixture";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, checkpoints, sessionMenu } from "./helpers";

test("recovers the exact saved checkpoint after its POST response is lost", async ({ page }) => {
  const key = `e2e-checkpoint-recovery-${Date.now()}`;
  await seedCheckpointSession(key, { name: "Checkpoint recovery", group: "Recovery QA" });
  try {
    await apiPost(`/sessions/${key}/checkpoint`, { label: "Unrelated earlier attempt" });
    const earlier = (await checkpoints(key))[0];
    let requestId = "", checkpointId = "", posts = 0;
    await page.route(`**/sessions/${key}/checkpoint`, async route => {
      posts++;
      requestId = route.request().postDataJSON().request_id;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      const cp = await response.json();
      checkpointId = cp.id;
      expect(cp.request_id).toBe(requestId);
      // Backend really saved this attempt; only its browser response is lost.
      await route.abort("failed");
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const folder = page.locator(".rd-group-head", { hasText: "Recovery QA" });
    if (await folder.locator(".rd-group-caret").textContent() === "▸") await folder.locator(".rd-group-caret").click();
    await page.locator(".rd-row", { hasText: "Checkpoint recovery" }).locator(".rd-row-click").click();
    const receiptResponse = page.waitForResponse(response => response.url().includes(`/sessions/${key}/checkpoint-attempts/`) && response.status() === 200);
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();
    await confirmCheckpoint(page);
    const receipt = await (await receiptResponse).json();
    expect(receipt.request_id).toBe(requestId);
    expect(receipt.state).toBe("complete");
    expect(receipt.result.id).toBe(checkpointId);
    expect(receipt.result.id).not.toBe(earlier.id);
    const progress = page.getByRole("region", { name: "Checkpoint progress" });
    // The isolated test summarizer is not a real provider, so this exact saved
    // result is a summary failure. Request recovery must preserve that outcome.
    await expect(progress.getByRole("status")).toHaveText("Summary update failed");
    await expect(progress.getByText("Result not confirmed")).toHaveCount(0);
    expect(posts).toBe(1);
    expect(await checkpoints(key)).toHaveLength(2);
  } finally { await apiDelete(`/sessions/${key}`); }
});
