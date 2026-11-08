import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { apiDelete, base, seedSession } from "./helpers";

const pending = (key: string) => ({ request_id: "11111111-1111-4111-8111-111111111111", session_id: key,
  status: "queued", text: "have we also released the horizontal view part?", created_at: Date.now(), updated_at: Date.now(),
  expires_at: Date.now() + 900000, wait_reason: "agent_busy" });
const reply = { messages: [{ id: 1, role: "assistant", blocks: [{ type: "text", text:
  "The horizontal map is tested and awaiting release. I’m implementing the approved folder and Oracle question screens now." }] }] };

test("pending iPhone messages sit above the composer in both themes and narrow layouts, including empty transcripts", async ({ page }) => {
  const key = await seedSession("mobile-pending-layout", { name: "Phone pending review", runtime: "codex", test: true });
  const directory = process.env.DUCKY_STATUS_SCREENSHOTS;
  if (directory) mkdirSync(directory, { recursive: true });
  let empty = false, reads = 0;
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  try {
    await page.route(`**/sessions/${key}/messages`, route => route.fulfill({ json: empty ? { messages: [] } : reply }));
    await page.route(`**/sessions/${key}/mobile-messages`, route => { reads++; return route.fulfill({ json: { mobile_messages: [pending(key)] } }); });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base()); await page.locator(".rd-row-name", { hasText: "Phone pending review" }).click();
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    const region = page.getByRole("region", { name: "Pending iPhone messages" });
    await expect(region.getByText("Waiting for this turn to finish")).toBeVisible();
    const collapse = page.getByRole("button", { name: "Collapse Context panel" });
    if (await collapse.isVisible()) await collapse.click();
    for (const [width, height] of [[1440, 900], [800, 800]]) {
      await page.setViewportSize({ width, height });
      for (const theme of ["dark", "light"]) {
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        await expect(region).toBeInViewport();
        const composer = page.getByRole("textbox", { name: "Follow-up message" });
        await expect(composer).toBeInViewport();
        const bounds = await region.boundingBox(), input = await composer.boundingBox();
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(input!.y);
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        expect(await region.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
        if (directory) await page.screenshot({ path: `${directory}/pending-${width}-${theme}.png` });
      }
    }
    empty = true;
    await expect(page.getByText("No agent reply yet.", { exact: true })).toBeVisible({ timeout: 5000 });
    await expect(region).toBeVisible();
    expect(await page.locator("[data-message-key]").count()).toBe(0);
    if (directory) await page.screenshot({ path: `${directory}/pending-empty.png` });
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    const paused = reads; await page.waitForTimeout(3300); expect(reads).toBe(paused);
    expect(errors).toEqual([]);
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("failed pending reads show last-confirmed status without disturbing the transcript or resending", async ({ page }) => {
  const key = await seedSession("mobile-pending-recovery", { name: "Phone pending recovery", runtime: "codex", test: true });
  let status = 200, rows = [pending(key)];
  const methods: string[] = [];
  try {
    await page.route(`**/sessions/${key}/messages`, route => route.fulfill({ json: reply }));
    await page.route(`**/sessions/${key}/mobile-messages`, route => {
      methods.push(route.request().method()); return route.fulfill({ status, json: { mobile_messages: rows } });
    });
    await page.goto(base()); await page.locator(".rd-row-name", { hasText: "Phone pending recovery" }).click();
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    await expect(page.getByText("Waiting for this turn to finish", { exact: true })).toBeVisible();
    status = 503;
    await expect(page.getByText(/Status unavailable · Last confirmed at/)).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("Waiting for this turn to finish", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Last confirmed: waiting for this turn to finish")).toBeVisible();
    await expect(page.locator(".rd-msg-text")).toContainText("The horizontal map is tested");
    if (process.env.DUCKY_STATUS_SCREENSHOTS) await page.screenshot({ path: `${process.env.DUCKY_STATUS_SCREENSHOTS}/pending-stale.png` });
    status = 200; rows = [];
    await expect(page.getByRole("region", { name: "Pending iPhone messages" })).toHaveCount(0, { timeout: 5000 });
    await expect(page.locator(".rd-msg-text")).toContainText("The horizontal map is tested");
    expect(methods.every(method => method === "GET")).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); }
});
