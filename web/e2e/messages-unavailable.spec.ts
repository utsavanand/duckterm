import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("Messages explains missing identity and unavailable local transcript", async ({ page }) => {
  const key = `missing-conversation-${Date.now()}`;
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "rd-transcript-availability-")));
  const project = join(homedir(), ".claude", "projects", cwd.replace(/[^a-zA-Z0-9]/g, "-"));
  await seedSession(key, { name: "Conversation availability check", cwd });
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
      session_id: `absent-${key}`, runtime: "claude-code", cwd, test: true });
    await expect(page.getByText("Conversation transcript not found on this machine", { exact: true })).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("Conversation not identified yet", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-messages-transcript-unavailable.png" });
    // A recorded, readable transcript with no messages is genuinely empty.
    mkdirSync(project, { recursive: true });
    writeFileSync(join(project, `absent-${key}.jsonl`), "");
    await expect(page.getByText("No agent reply yet.", { exact: true })).toBeVisible({ timeout: 8000 });
    await expect(page.getByText("Conversation transcript not found on this machine", { exact: true })).toHaveCount(0);
    const empty = await (await page.request.get(`${base()}/sessions/${key}/messages`)).json();
    expect(empty.messages).toEqual([]);
    expect(empty.transcript?.status).toBeUndefined();
    await page.screenshot({ path: "/tmp/duckterm-messages-genuinely-empty.png" });
  } finally {
    await apiDelete(`/sessions/${key}`);
    rmSync(project, { recursive: true, force: true });
    rmSync(cwd, { recursive: true, force: true });
  }
});
