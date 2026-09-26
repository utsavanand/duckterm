import { expect, test } from "@playwright/test";
import { apiDelete, sessions } from "./helpers";

// Open New session, confirm the agent picker, choose a folder, launch — and
// verify a real synthetic session lands in the backend without requiring an
// installed or authenticated model CLI on the test machine.
test("new session: agent picker + launch creates a session", async ({
  page,
}) => {
  const name = `new-session-fixture-${Date.now()}`;
  await page.route("**/sessions/launch", async route => {
    await route.continue({ postData: JSON.stringify({ ...route.request().postDataJSON(), test: true }) });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session" }).click();

  // The agent picker offers all known agents plus a custom escape hatch.
  await expect(page.locator(".rd-pill")).toHaveText([
    "Claude Code",
    "Codex",
    "Copilot",
    "Custom…",
  ]);
  await page.getByRole("button", { name: "Custom…", exact: true }).click();
  await page.getByPlaceholder('e.g. aider   ·   claude -p "fix the bug"').fill("/bin/cat");
  await page.getByPlaceholder("e.g. login refactor").fill(name);

  // Pick a folder: open the browser and use the current (home) directory.
  // (These custom buttons aren't exposed with a button role, so match on text.)
  await page.locator("button", { hasText: "Browse" }).click();
  await page.locator("button", { hasText: "Use this folder" }).click();

  // A git home dir shows a run-mode chooser; pick Run in place to skip a
  // worktree. No-op if the chooser isn't shown (non-git folder).
  const inPlace = page.getByText("Run in place");
  if (await inPlace.isVisible().catch(() => false)) {
    await inPlace.click();
  }

  await page.getByRole("button", { name: "Launch", exact: true }).click();

  try {
    await expect
      .poll(async () => (await sessions()).filter(s => s.name === name).length, { timeout: 8000 })
      .toBe(1);
  } finally {
    for (const session of (await sessions()).filter(s => s.name === name)) {
      await apiDelete(`/sessions/${session.session_key}`);
    }
  }
});
