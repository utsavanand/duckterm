import { expect, test } from "@playwright/test";
import { apiPost, seedSession } from "./helpers";

// The Fork modal offers both kinds for a claude-code session on a branch, and
// the API contract around conversation forks stays honest and loud:
//  - a session with NO conversation yet forks anyway (a fresh sibling), and
//    the response says so in `note` instead of failing;
//  - a non-claude session gets a clear 400 naming the reason.
// The UI submit path (in_terminal:false) is covered at the API layer in
// tests/runtime/test_fork_conversation.py — exercising it here would launch a
// real `claude`.

test("fork modal offers both kinds; no-conversation fork starts fresh with a note", async ({
  page,
}) => {
  const key = `e2e-fork-${Date.now()}`;
  await seedSession(key, {
    name: key,
    branch: "feature/x",
    runtime: "claude-code",
  });

  // No conversation yet -> still 200, fresh sibling, plain-language note.
  // (Tab path: DUCKTERM_NO_TERMINAL makes the terminal open a no-op.)
  const res = await apiPost(`/sessions/${key}/fork-conversation`);
  expect(res.status).toBe(200);
  expect(res.body.carried_conversation).toBe(false);
  expect(String(res.body.note)).toContain("no conversation to fork yet");

  // A non-claude session errors clearly, naming the constraint.
  const genericKey = `e2e-fork-generic-${Date.now()}`;
  await seedSession(genericKey, { name: genericKey, runtime: "generic" });
  const bad = await apiPost(`/sessions/${genericKey}/fork-conversation`);
  expect(bad.status).toBe(400);
  expect(String(bad.body.error)).toContain("claude-code");

  await page.goto("/");
  const row = page.locator(".rd-row", { hasText: key }).first();
  await expect(row).toBeVisible();

  await row.hover();
  await row.locator("button", { hasText: "Fork" }).click();

  // The modal opened and offers both fork kinds.
  await expect(page.getByText(`Fork ${key}`)).toBeVisible();
  await expect(page.getByText("Git worktree")).toBeVisible();
  await expect(page.getByText("Conversation only")).toBeVisible();
});
