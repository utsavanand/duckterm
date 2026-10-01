import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("Messages explains missing identity and unavailable local transcript", async ({ page }) => {
  const key = `missing-conversation-${Date.now()}`;
  await seedSession(key, { name: "Conversation availability check", cwd: "/tmp/rd-no-conversation" });
  try {
    const initial = await (await page.request.get(`${base()}/sessions/${key}/messages`)).json();
    expect(initial.transcript.status).toBe("identity_missing");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.getByText("Conversation availability check", { exact: true }).first().click();
    await page.locator(".rd-view-toggle button", { hasText: "Messages" }).click();
    await expect(page.getByText("Conversation not identified yet", { exact: true })).toBeVisible();
    await expect(page.getByText(initial.transcript.reason, { exact: true })).toBeVisible();
    await expect(page.getByText("No agent reply yet.", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-messages-identity-missing.png" });
    await apiPost("/events", { event_type: "SessionStart", session_key: key,
      session_id: `absent-${key}`, runtime: "claude-code", cwd: "/tmp/rd-no-conversation", test: true });
    await expect(page.getByText("Conversation transcript not found on this machine", { exact: true })).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("Conversation not identified yet", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-messages-transcript-unavailable.png" });
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
