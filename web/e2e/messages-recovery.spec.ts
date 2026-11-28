import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

test("Messages leaves a stalled loading state and recovers on retry", async ({ page }) => {
  const key = await seedSession("messages-recovery", { name: "Messages recovery", runtime: "codex", test: true });
  let reads = 0;
  try {
    await page.clock.install();
    await page.route(`**/sessions/${key}/messages`, async route => {
      if (++reads === 1) return; // Deliberately never deliver the first response.
      await route.fulfill({ json: { messages: [
        { id: 1, message_key: "recovery-user", role: "user", blocks: [{ type: "text", text: "Check recovery" }] },
        { id: 2, message_key: "recovery-answer", role: "assistant", blocks: [{ type: "text", text: "The conversation recovered." }] },
      ] } });
    });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Messages recovery" }).click();
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    await expect(page.getByText("Loading messages…", { exact: true })).toBeVisible();
    await expect.poll(() => reads).toBe(1);
    await page.clock.runFor(30001);
    await expect(page.getByText("Could not load messages. Retrying…", { exact: true })).toBeVisible();
    await page.clock.runFor(3000);
    await expect(page.getByText("The conversation recovered.", { exact: true })).toBeVisible();
    await expect(page.getByText("Could not load messages. Retrying…", { exact: true })).toHaveCount(0);
    expect(reads).toBe(2);
  } finally { await apiDelete(`/sessions/${key}`); }
});
