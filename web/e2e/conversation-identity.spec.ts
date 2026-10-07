import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { apiDelete, base, postEvent, seedSession } from "./helpers";

test("missing conversation identity is visible on launch and does not promise Resume", async ({ page }) => {
  const key = `identity-${randomUUID()}`, name = "Recovery reviewer";
  await seedSession(key, { name, launched: true, runtime: "codex", test: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.goto(base());
    await page.getByText(name, { exact: true }).first().click();
    const card = page.getByRole("region", { name: "Session controls", exact: true });
    await expect(card.getByRole("heading", { name: "Resume unavailable" })).toBeVisible();
    await expect(card.getByText(/Conversation ID not recorded/)).toBeVisible();
    // The owner-auth endpoint verifies hooks independently from identity.
    await expect(card.getByRole("button", { name: "Install hooks" })).toBeVisible();
    await page.screenshot({ path: "/tmp/conversation-identity-implemented-dark.png" });
    await postEvent({ event_type: "Notification", session_key: key, lifecycle: "stopped" });
    await expect(card.getByRole("button", { name: "Resume", exact: true })).toBeDisabled();
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/conversation-identity-implemented-light.png" });
    await postEvent({ event_type: "SessionStart", session_key: key, session_id: "owner-test-conversation", runtime: "codex" });
    await page.reload();
    await page.getByText(name, { exact: true }).first().click();
    await expect(card.getByRole("heading", { name: "Conversation identity recorded" })).toBeVisible();
    await expect(card.getByText(/transcript could not be found/)).toBeVisible();
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("owner reviews a real project transcript, stale choices fail, attachment never launches", async ({ page }) => {
  const { mkdtempSync, realpathSync, mkdirSync, writeFileSync, appendFileSync, rmSync } = await import("node:fs");
  const { tmpdir, homedir } = await import("node:os");
  const { join } = await import("node:path");
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "recovery-browser-")));
  const root = join(homedir(), ".claude", "projects", cwd.replace(/[^a-zA-Z0-9]/g, "-"));
  mkdirSync(root, { recursive: true });
  for (const [id, prompt] of [["alpha", "Fix the session sidebar"], ["omega", "Check release downloads"]]) {
    writeFileSync(join(root, id + ".jsonl"), JSON.stringify({ cwd, sessionId: id, message: { role: "user", content: prompt } }) + "\n");
  }
  const key = await seedSession(`recovery-${randomUUID()}`, { name: "Recovery picker reviewer", cwd, launched: true, runtime: "claude-code", test: true });
  await postEvent({ event_type: "Notification", session_key: key, lifecycle: "stopped" });
  try {
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Recovery picker reviewer" }).click();
    const card = page.getByRole("region", { name: "Session controls", exact: true });
    await card.getByRole("button", { name: "Install hooks" }).click();
    await expect(card.getByRole("heading", { name: "Awaiting conversation ID" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Resume", exact: true })).toBeDisabled();
    await card.getByRole("button", { name: "Choose a conversation" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("radio")).toHaveCount(2);
    await expect(dialog.getByRole("radio").first()).not.toBeChecked();
    await expect(dialog.getByRole("button", { name: "Review selection" })).toBeDisabled();
    await page.screenshot({ path: "/tmp/conversation-picker-implemented-dark.png" });
    await dialog.getByRole("radio", { name: "Conversation 1", exact: true }).check();
    await dialog.getByRole("button", { name: "Review selection" }).click();
    appendFileSync(join(root, "alpha.jsonl"), "{}\n");
    await dialog.getByRole("button", { name: "Attach this conversation" }).click();
    await expect(dialog.getByRole("alert")).toContainText("changed");
    await dialog.getByRole("button", { name: "Check again" }).click();
    await expect(dialog.getByRole("radio")).toHaveCount(2);
    await expect(dialog.getByRole("radio").first()).not.toBeChecked();
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/conversation-picker-implemented-light.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeInViewport();
    expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/conversation-picker-implemented-mobile.png" });
    await dialog.getByRole("radio", { name: "Conversation 1", exact: true }).check();
    await dialog.getByRole("button", { name: "Review selection" }).click();
    await dialog.getByRole("button", { name: "Attach this conversation" }).click();
    await expect(dialog).toHaveCount(0);
    const result = await (await page.request.get(base() + "/sessions")).json();
    const row = result.sessions.find((s: { session_key: string }) => s.session_key === key);
    expect(row.state).toBe("stopped");
    expect(row.conversation_identity.source).toBe("adopted");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect(card.getByRole("heading", { name: "Conversation chosen by you" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Resume", exact: true })).toBeEnabled();
  } finally { await apiDelete(`/sessions/${key}`); rmSync(root, { recursive: true, force: true }); rmSync(cwd, { recursive: true, force: true }); }
});
