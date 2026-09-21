import { expect, test } from "@playwright/test";
import { apiPost, base, seedSession } from "./helpers";

test("Inbox beside History shows real session questions and replies", async ({ page }) => {
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
    body: JSON.stringify({ target_session_id: "inbox-recipient", question: "Which response fields does the client need?", timeout_seconds: 1 }),
  });
  expect(created.status).toBe(202);
  const question = await created.json();

  await page.goto(base());
  await page.locator(".rd-row-name", { hasText: "Client implementation" }).click();
  const tabs = page.locator(".rd-view-toggle button");
  await expect(page.getByRole("button", { name: "Open Client implementation inbox, 1 pending" })).toBeVisible();
  await expect(tabs).toHaveText(["Terminal", "Messages", "History", "Inbox (1)"]);
  await page.getByRole("button", { name: "Open Client implementation inbox, 1 pending" }).click();
  await expect(page.locator(".rd-session-card")).toContainText("inbox-test/frontend");
  await expect(page.locator(".rd-session-card")).toContainText("inbox-test");
  await expect(page.locator(".rd-session-card")).not.toHaveAttribute("open", "");
  await expect(page.locator(".rd-inbox-setup")).not.toHaveAttribute("open", "");
  await page.getByText("Agent setup and instructions", { exact: true }).click();
  await page.getByRole("button", { name: "Show introduction to paste" }).click();
  await expect(page.getByLabel("Introduction to paste into the agent")).toHaveValue(/Duckterm session capability:/);
  await expect(page.getByLabel("Introduction to paste into the agent")).toHaveValue(/collaboration\.md/);
  await expect(page.locator(".rd-inbox-message strong")).toHaveText("Request fromAPI implementation");
  await expect(page.locator(".rd-inbox-status")).toHaveText("Overdue · awaiting reply", { timeout: 8000 });
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
  await page.getByText("Agent setup and instructions", { exact: true }).click();
  await page.screenshot({ path: "/tmp/duckterm-inbox.png" });
  await page.getByRole("button", { name: "Sent", exact: true }).click();
  await expect(page.locator(".rd-inbox-message strong")).toHaveText("Reply toAPI implementation");
  await expect(page.locator(".rd-inbox-status")).toHaveText("Answered");
  await page.reload();
  await page.locator(".rd-row-name", { hasText: "Client implementation" }).click();
  await page.locator(".rd-view-toggle button", { hasText: "Inbox" }).click();
  await expect(page.locator(".rd-inbox-status")).toHaveText("Answered");

  // Switch participants: Sent must use the same persisted answer.
  await page.goto(base());
  await page.locator(".rd-row-name", { hasText: "API implementation" }).click();
  await page.locator(".rd-view-toggle button", { hasText: "Inbox" }).click();
  await page.getByRole("button", { name: "Sent", exact: true }).click();
  await expect(page.locator(".rd-inbox-message strong")).toHaveText("Request toClient implementation");
  await expect(page.locator(".rd-inbox-status")).toHaveText("Answered");
  await page.locator(".rd-inbox-message summary").click();
  await expect(page.locator(".rd-inbox-answer p")).toContainText("Keep the request ID stable.");
  await page.getByRole("button", { name: "Received", exact: true }).click();
  await expect(page.getByText("No messages yet")).toBeVisible();
 });
