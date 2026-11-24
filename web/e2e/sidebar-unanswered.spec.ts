import { expect, test } from "@playwright/test";
import { api, apiDelete, apiPost, base, postEvent, seedSession } from "./helpers";

test("unanswered inbox filter follows real read, accepted, and answered requests", async ({ page }) => {
  test.setTimeout(60000);
  const folder = "Inbox filter review";
  const keys = ["unanswered-sender", "unanswered-recipient"];
  try {
    for (const key of keys) await seedSession(key, { name: key, group: folder, runtime: "codex", test: true });
    const sender = await apiPost(`/sessions/${keys[0]}/collaboration`, { root: folder });
    const recipient = await apiPost(`/sessions/${keys[1]}/collaboration`, { root: folder });
    expect(sender.status).toBe(200); expect(recipient.status).toBe(200);
    let sequence = 0;
    const request = async (token: unknown, path: string, body?: object) => fetch(`${base()}/api/v1/session${path}`, {
      method: body ? "POST" : "GET",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": `unanswered-filter-${sequence++}` },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const ids: string[] = [];
    for (const question of ["Confirm the API shape", "Review the tests"]) {
      const response = await request(sender.body.token, "/questions", { target_session_id: keys[1], question });
      expect(response.status).toBe(202); ids.push((await response.json()).id);
    }
    await postEvent({ session_key: keys[1], event_type: "Notification", notification_type: "idle_prompt", runtime: "codex" });
    await page.setViewportSize({ width: 1440, height: 950 });
    await page.goto(base());
    await page.getByRole("button", { name: /^Filters(?:,|$)/ }).click();
    const chip = page.getByRole("button", { name: "Unanswered inbox 1", exact: true });
    await expect(chip).toBeVisible();
    await chip.click();
    const rows = page.locator('.rd-agents .rd-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText(keys[1]);
    await expect(rows.first()).toContainText("Inbox 2");
    await page.reload();
    await expect(chip).toHaveAttribute("aria-pressed", "true");
    // Reading, then accepting, must not make the request disappear.
    expect((await request(recipient.body.token, "/inbox")).status).toBe(200);
    expect((await request(recipient.body.token, `/questions/${ids[0]}/accept`, {})).status).toBe(200);
    expect((await (await api('/session-inbox-counts')).json()).unanswered_counts[keys[1]]).toBe(2);
    await page.screenshot({ path: "/tmp/unanswered-filter-implemented-dark.png" });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.evaluate(() => document.documentElement.style.setProperty('--ui-scale', '1.5'));
    const bounds = await page.locator('.rd-sidebar-filters').evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth }));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.width);
    await page.screenshot({ path: "/tmp/unanswered-filter-implemented-light-large.png" });
    for (const id of ids) expect((await request(recipient.body.token, `/questions/${id}/answer`, { text: "Reviewed" })).status).toBe(200);
    await expect(page.getByRole("button", { name: "Unanswered inbox 0", exact: true })).toBeVisible({ timeout: 10000 });
    await expect(rows).toHaveCount(0);
    await expect(page.getByText("No sessions match these filters.")).toBeVisible();
    await page.getByRole('region', { name: 'Session filters' }).getByRole('button', { name: 'Clear filters' }).click();
    await expect(page.getByRole("button", { name: `Expand ${folder}`, exact: true })).toBeVisible();
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});
