import { expect, test } from "@playwright/test";
import { apiPost, base, seedSession } from "./helpers";

test("folder telephone shows attributed conversations and live responses", async ({ page }) => {
  await seedSession("calls-a", { name: "API agent", group: "Calls/backend" });
  await seedSession("calls-b", { name: "UI agent", group: "Calls/frontend" });
  await apiPost("/folders", { name: "No calls" });
  const a = await apiPost("/sessions/calls-a/collaboration", { root: "Calls" });
  const b = await apiPost("/sessions/calls-b/collaboration", { root: "Calls" });
  const response = await fetch(`${base()}/api/v1/session/questions`, {
    method: "POST", headers: { Authorization: `Bearer ${a.body.token}`, "Content-Type": "application/json", "Idempotency-Key": "folder-call" },
    body: JSON.stringify({ target_session_id: "calls-b", question: "Which fields should I include?\nPlease confirm the contract." }),
  });
  expect(response.status).toBe(202);
  const q = await response.json();
  await fetch(`${base()}/api/v1/session/questions/${q.id}/accept`, { method: "POST", headers: { Authorization: `Bearer ${b.body.token}` } });
  await page.goto(base());
  await page.getByRole("button", { name: "View conversations in Calls", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Session conversations" });
  await expect(dialog.locator(".rd-conversation")).toHaveCount(1);
  await expect(dialog.locator(".rd-conversation-participants")).toContainText("API agent");
  await expect(dialog.locator(".rd-conversation-participants")).toContainText("UI agent");
  await expect(dialog).toContainText("Accepted · no response yet");
  await dialog.locator("summary").click();
  await expect(dialog.getByText("No response was recorded.")).toBeVisible();
  const answer = "Include id and status.\nKeep Unicode 🦆 intact.";
  const replied = await fetch(`${base()}/api/v1/session/questions/${q.id}/answer`, {
    method: "POST", headers: { Authorization: `Bearer ${b.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ text: answer }),
  });
  expect(replied.status).toBe(200);
  await expect(dialog.locator(".rd-conversation-reply p")).toHaveText(answer, { timeout: 8000 });
  await page.screenshot({ path: "/tmp/duckterm-folder-conversations.png" });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "View conversations in Calls/backend", exact: true }).click();
  await expect(dialog.locator(".rd-conversation")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Close conversations" }).click();
  await page.getByRole("button", { name: "View conversations in No calls", exact: true }).click();
  await expect(dialog).toContainText("No conversations yet");
  await expect(dialog.locator(".rd-conversation")).toHaveCount(0);
});
