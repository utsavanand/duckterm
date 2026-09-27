import { expect, test } from "@playwright/test";
import { apiPost, base, seedSession, expandFolder, apiDelete } from "./helpers";

const createdSessions = new Set<string>();
test.afterEach(async () => {
  for (const key of createdSessions) await apiDelete(`/sessions/${key}`);
  createdSessions.clear();
});

test("Inbox beside History shows real session questions and replies", async ({ page }) => {
  createdSessions.add("inbox-sender");
  createdSessions.add("inbox-recipient");
  await seedSession("inbox-sender", { name: "API implementation", group: "inbox-test/backend" });
  await seedSession("inbox-recipient", { name: "Client implementation", group: "inbox-test/frontend" });
  const sender = await apiPost("/sessions/inbox-sender/collaboration", { root: "inbox-test" });
  const recipient = await apiPost("/sessions/inbox-recipient/collaboration", { root: "inbox-test" });
  expect(sender.status).toBe(200);
  expect(recipient.status).toBe(200);
  const created = await fetch(`${base()}/api/v1/session/questions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sender.body.token}`,
      "Content-Type": "application/json",
      "Idempotency-Key": "inbox-e2e",
    },
    body: JSON.stringify({ target_session_id: "inbox-recipient", question: "Which response fields does the client need?" }),
  });
  expect(created.status).toBe(202);
  const question = await created.json();

  await page.goto(base());
  await expandFolder(page, "inbox-test/frontend");
  await page.locator(".rd-row-name", { hasText: "Client implementation" }).click();
  const tabs = page.locator(".rd-view-toggle button");
  await expect(page.getByRole("button", { name: "Open Client implementation inbox, 1 pending" })).toBeVisible();
  await expect(tabs).toHaveText(["Terminal", "Messages", "History", "Inbox (1)", "Artifacts"]);
  await page.getByRole("button", { name: "Open Client implementation inbox, 1 pending" }).click();
  await expect(page.locator(".rd-session-card")).not.toHaveAttribute("open", "");
  await page.locator(".rd-session-card > summary").click();
  await expect(page.locator(".rd-session-card")).toContainText("inbox-test/frontend");
  await expect(page.locator(".rd-session-card")).toContainText("inbox-test");
  await page.locator(".rd-session-card > summary").click();
  await page.getByRole("button", { name: "Session tools & help" }).click();
  await page.getByRole("button", { name: "Show introduction to paste" }).click();
  await expect(page.getByLabel("Introduction to paste into the agent")).toHaveValue(/Duckterm session capability:/);
  await expect(page.getByLabel("Introduction to paste into the agent")).toHaveValue(/collaboration\.md/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Session tools & help" })).toBeFocused();
  await expect(page.locator(".rd-inbox-message strong")).toHaveText("API implementation");
  await expect(page.locator(".rd-inbox-status")).toHaveText("Awaiting reply");
  await expect(page.getByText("This agent isn’t running in a terminal Duckterm owns.")).toHaveCount(0);

  const reply = await fetch(`${base()}/api/v1/session/questions/${question.id}/answer`, {
    method: "POST",
    headers: { Authorization: `Bearer ${recipient.body.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text: "Include id, status, and updated_at.\nKeep the request ID stable." }),
  });
  expect(reply.status).toBe(200);
  await expect(page.locator(".rd-inbox-status")).toHaveText("Answered", { timeout: 8000 });
  await expect(page.getByRole("button", { name: "Open Client implementation inbox, 1 pending" })).toHaveCount(0);
  await page.locator(".rd-inbox-message summary").click();
  await expect(page.locator(".rd-inbox-answer p")).toHaveText("Include id, status, and updated_at.\nKeep the request ID stable.");
  await page.screenshot({ path: "/tmp/duckterm-inbox.png" });
  await page.reload();
  await expandFolder(page, "inbox-test/frontend");
  await page.locator(".rd-row-name", { hasText: "Client implementation" }).click();
  await page.locator(".rd-view-toggle button", { hasText: "Inbox" }).click();
  await expect(page.locator(".rd-inbox-status")).toHaveText("Answered");
});

test("Inbox scrolls independently, preserves open replies on refresh, and adapts inside narrow panes", async ({ page }) => {
  createdSessions.add("inbox-scroll");
  await seedSession("inbox-scroll", { name: "Inbox layout", group: "inbox-layout-test" });
  const messages = Array.from({ length: 50 }, (_, i) => ({
    id: `layout-${i}`, sender: "layout-sender", recipient: "inbox-scroll", sender_name: i === 0 ? "Release review" : `Sender ${i}`,
    recipient_name: "Inbox layout", question: `Message ${i}: Please review the complete implementation and its validation results.`,
    status: i % 2 ? "answered" : "queued", answer: i % 2 ? "Verified the changes and regression checks." : null,
    created_at: Date.now() - i * 60000, answered_at: i % 2 ? Date.now() : null, expires_at: 0,
  }));
  let refresh = false;
  await page.route("**/sessions/inbox-scroll/inbox*", (route) => route.fulfill({ json: {
    card: { name: "Inbox layout", api_name: "inbox-scroll", purpose: "Review the approved Inbox layout", activity: "Verify the scroll region with realistic message volume.", folder: "inbox-layout-test", root: "inbox-layout-test", cwd: "/tmp/e2e", next_actions: Array.from({ length: 12 }, (_, i) => `Review step ${i}`), updated_at: Date.now() },
    messages: messages.map((m, i) => refresh && i === 0 ? { ...m, status: "answered", answer: "Updated reply arrived during polling." } : m), next_cursor: null,
  } }));
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
  await page.goto(base());
  await expandFolder(page, "inbox-layout-test");
  await page.locator(".rd-row-name", { hasText: "Inbox layout" }).click();
  await page.locator(".rd-view-toggle button", { hasText: "Inbox" }).click();
  const list = page.getByRole("region", { name: "Scrollable inbox" });
  await expect(page.locator(".rd-inbox-message")).toHaveCount(50);
  await expect(page.locator(".rd-session-card")).not.toHaveAttribute("open", "");
  const heading = await page.locator(".rd-inbox-heading").boundingBox();
  expect(await list.evaluate((e) => e.scrollHeight > e.clientHeight)).toBe(true);
  await list.focus();
  await page.keyboard.press("PageDown");
  await expect.poll(() => list.evaluate((e) => e.scrollTop)).toBeGreaterThan(0);
  expect((await page.locator(".rd-inbox-heading").boundingBox())!.y).toBe(heading!.y);
  expect(await page.locator(".rd-inbox-wrap").evaluate((e) => e.scrollTop)).toBe(0);
  await list.evaluate((e) => { e.scrollTop = 0; });
  await page.locator(".rd-inbox-message summary").first().click();
  refresh = true;
  await expect(page.locator(".rd-inbox-message").first().locator(".rd-inbox-answer")).toContainText("Updated reply arrived", { timeout: 8000 });
  await expect(page.locator(".rd-inbox-message").first()).toHaveAttribute("open", "");
  await page.locator(".rd-inbox-message summary").first().click();
  await page.locator(".rd-session-card summary").click();
  expect(await list.evaluate((e) => e.clientHeight)).toBeGreaterThan(200);
  await page.locator(".rd-session-card summary").click();
  await page.getByRole("button", { name: "Awaiting reply 24" }).click();
  await expect(page.locator(".rd-inbox-message")).toHaveCount(24);
  await page.getByRole("button", { name: "All 50" }).click();
  await page.getByRole("searchbox", { name: "Search loaded messages" }).fill("Release review");
  await expect(page.locator(".rd-inbox-message")).toHaveCount(1);
  await page.getByRole("searchbox").fill("");
  await page.screenshot({ path: "/tmp/duckterm-inbox-layout-desktop.png" });
  await page.setViewportSize({ width: 1280, height: 900 });
  expect(await list.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/duckterm-inbox-layout-narrow.png" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.screenshot({ path: "/tmp/duckterm-inbox-layout-light.png" });
  await page.route("**/folder-interactions*", (route) => route.fulfill({ json: { messages, next_cursor: null } }));
  await page.getByRole("button", { name: "View interactions in inbox-layout-test", exact: true }).click();
  const folderInbox = page.locator(".rd-folder-inbox");
  await expect(folderInbox.locator(".rd-inbox-message")).toHaveCount(50);
  await expect(folderInbox.getByRole("button", { name: "Message folder", exact: true })).toBeVisible();
  await expect(folderInbox.locator(".rd-inbox-recipient").first()).toContainText("Inbox layout");
  expect(await folderInbox.locator(".rd-inbox-list").evaluate((e) => e.scrollHeight > e.clientHeight && e.scrollWidth <= e.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/duckterm-inbox-layout-folder.png" });
});
