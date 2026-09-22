import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("folder phone survives collapse and reload and shows answered exchanges", async ({ page }) => {
  await seedSession("folder-phone-a", { name: "Folder sender", group: "Phone review/backend" });
  await seedSession("folder-phone-b", { name: "Folder recipient", group: "Phone review/frontend" });
  try {
    const sender = await apiPost("/sessions/folder-phone-a/collaboration", { root: "Phone review" });
    const recipient = await apiPost("/sessions/folder-phone-b/collaboration", { root: "Phone review" });
    const created = await fetch(`${base()}/api/v1/session/questions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${sender.body.token}`, "Content-Type": "application/json", "Idempotency-Key": "folder-phone" },
      body: JSON.stringify({ target_session_id: "folder-phone-b", question: "Which response fields are ready?" }),
    });
    expect(created.status).toBe(202);
    const question = await created.json();
    const reply = await fetch(`${base()}/api/v1/session/questions/${question.id}/answer`, {
      method: "POST",
      headers: { Authorization: `Bearer ${recipient.body.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ text: "The id, status, and updated_at fields are ready." }),
    });
    expect(reply.status).toBe(200);
    await page.goto("/");
    await page.locator(".rd-group-name").filter({ hasText: /^Phone review$/ }).click();
    const phone = page.getByRole("button", { name: "View interactions in Phone review", exact: true });
    await expect(phone).toBeVisible();
    await phone.click();
    await expect(page.getByRole("heading", { name: "Phone review · Interactions" })).toBeVisible();
    await expect(page.locator(".rd-inbox-message")).toHaveCount(1);
    await expect(page.locator(".rd-inbox-message strong")).toHaveText("Folder sender → Folder recipient");
    await expect(page.locator(".rd-inbox-status")).toHaveText("Answered");
    await page.locator(".rd-inbox-message summary").click();
    await expect(page.locator(".rd-inbox-answer p")).toContainText("updated_at");
    await page.screenshot({ path: "/tmp/duckterm-folder-interactions.png" });
    await page.reload();
    await expect(phone).toBeVisible();
    await page.getByRole("button", { name: "View interactions in Phone review/backend", exact: true }).click();
    await expect(page.locator(".rd-inbox-message")).toHaveCount(1); // outgoing from this child
  } finally {
    await apiDelete("/sessions/folder-phone-a");
    await apiDelete("/sessions/folder-phone-b");
    await apiDelete("/folders/Phone%20review");
  }
});

test("new session lists empty and nested sidebar folders and sends the selected assignment", async ({ page }) => {
  await apiPost("/folders", { name: "Launch review/Child" });
  try {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "View interactions in Launch review", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "New session", exact: true }).click();
    const select = page.getByLabel("Sidebar folder", { exact: true });
    await expect(select).toHaveValue("");
    await expect(select.locator("option")).toContainText(["Ungrouped", "Launch review", "Launch review/Child"]);
    await select.selectOption("Launch review/Child");
    await page.screenshot({ path: "/tmp/duckterm-launch-folder.png" });
    await page.locator("button", { hasText: "Browse…" }).click();
    await page.locator("button", { hasText: "Use this folder" }).click();
    if (await page.getByText("Run in place", { exact: true }).isVisible()) await page.getByText("Run in place", { exact: true }).click();
    // A flagged fixture stands in for the new agent; no real CLI is started.
    await seedSession("folder-launch", { name: "Folder launch" });
    await page.route("**/sessions/launch", (route) => route.fulfill({ json: { session_key: "folder-launch" } }));
    const assigned = page.waitForResponse((r) => r.url().endsWith("/sessions/folder-launch") && r.request().method() === "PATCH");
    await page.getByRole("button", { name: "Launch", exact: true }).click();
    const response = await assigned;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON()).toEqual({ group: "Launch review/Child" });
    await page.reload();
    const child = page.locator(".rd-group").filter({ has: page.locator('.rd-group-name', { hasText: /^Child$/ }) }).last();
    await expect(child).toContainText("Folder launch");
    await child.getByTitle("New session in this folder", { exact: true }).click();
    await expect(select).toHaveValue("Launch review/Child");
    await select.selectOption("");
    await expect(select).toHaveValue("");
  } finally {
    await apiDelete("/sessions/folder-launch");
    await apiDelete("/folders/Launch%20review");
  }
});
