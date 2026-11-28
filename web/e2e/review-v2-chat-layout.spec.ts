import { expect, test, type Locator } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

// The review v2 chat with a long conversation and a fix draft open: the board
// keeps its width in a narrow window, and the suggestions, composer and draft
// never overlap (ui-dev's legacy chat fix found the same squeeze on the old page).
function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", ["-c", "user.email=qa@duckterm.local", "-c", "user.name=QA", "-c", "commit.gpgsign=false", ...args], { cwd, encoding: "utf8" }).trim();
}

async function box(locator: Locator) {
  const found = await locator.boundingBox();
  if (!found) throw new Error("element not laid out");
  return found;
}

test("review v2 chat keeps the board usable and nothing overlaps at any width", async ({ page }) => {
  test.setTimeout(60000);
  await page.emulateMedia({ colorScheme: "dark" });
  const root = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-rv2-chat-")));
  const origin = join(root, "origin.git"), work = join(root, "work");
  git(root, "init", "-q", "--bare", origin);
  git(root, "init", "-q", "-b", "main", work);
  writeFileSync(join(work, "README.md"), "base\n");
  git(work, "add", "README.md");
  git(work, "commit", "-q", "-m", "initial");
  git(work, "remote", "add", "origin", origin);
  git(work, "push", "-q", "-u", "origin", "main");
  git(work, "checkout", "-q", "-b", "duckterm/e2e-chat");
  writeFileSync(join(work, "feature.py"), "def feature():\n    return 1\n");
  git(work, "add", "feature.py");
  git(work, "commit", "-q", "-m", "add feature");
  git(work, "push", "-q", "-u", "origin", "duckterm/e2e-chat");
  const folder = `Review chat ${Date.now()}`;
  let key = "";
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'exec cat'", cwd: work, runtime: "generic", name: "chat-layout-agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200);
    key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });
    const reviewPath = `/review/${encodeURIComponent(work)}/${encodeURIComponent("duckterm/e2e-chat")}`;
    await page.route("**/edition", async route => { const r = await route.fetch(), d = await r.json(); if (!d.features.includes("review_v2")) d.features.push("review_v2"); await route.fulfill({ response: r, json: d }); });
    const answer = "The Unit tests row now reads CI and gate results. ".repeat(14);
    const messages = Array.from({ length: 6 }, (_, i) => ({ q: `Question ${i + 1}`, a: answer, citations: [{ path: "feature.py", line: 1 }], hunk: null, files: [], at: i }));
    await page.route(`**${reviewPath}/chat`, route => route.fulfill({ json: { messages, suggestions: ["Which callers are untested?", "What breaks if CI is pending?"] } }));
    await page.route(`**${reviewPath}/cards`, route => route.fulfill({ json: { head_sha: "x", stale: false, running: false, cards: [
      { id: "release", number: 9, title: "Release impact", status: "ready", data: { low_risk: "false", condition: "", verdict: "", checks: [{ name: "Conflict with base", hits: [], status: "fail", note: "" }] } },
    ] } }));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "chat-layout-agent" }).click();
    await page.getByRole("button", { name: "PRs", exact: true }).click();
    await page.getByRole("region", { name: "Pull requests" }).getByRole("row").filter({ hasText: "duckterm/e2e-chat" }).getByRole("button", { name: "Review", exact: true }).click();
    const workspace = page.getByRole("region", { name: "Review workspace" });
    await workspace.getByRole("tab", { name: /Release readiness/ }).click();
    await workspace.getByRole("button", { name: /to fix →/ }).click();
    const rail = page.getByRole("complementary", { name: "Review chat" });
    await expect(rail.getByRole("textbox", { name: "Fix request" })).toBeVisible();
    for (const [width, height] of [[1440, 900], [820, 700], [600, 640]]) {
      await page.setViewportSize({ width, height });
      const board = await box(workspace.locator(".rv2-main"));
      // Side by side when wide; over the board when narrow, which keeps its full width.
      expect(board.width).toBeGreaterThanOrEqual(width >= 1000 ? width - 400 : width - 1);
      expect((await box(rail)).width).toBeLessThanOrEqual(Math.min(380, width));
      // The rail's own chat header is hidden; the Chat / My notes tabs replace it.
      await expect(rail.locator(".rd-review-chat-head")).toBeHidden();
      const send = rail.getByRole("button", { name: /^Send to / });
      await send.scrollIntoViewIfNeeded();
      await expect(send).toBeInViewport();
      const composer = rail.getByRole("textbox", { name: /Ask about|Message/ }).last();
      await composer.scrollIntoViewIfNeeded();
      await expect(composer).toBeInViewport();
      const suggestion = await box(rail.getByRole("button", { name: "What breaks if CI is pending?" }));
      const field = await box(composer);
      expect(suggestion.y + suggestion.height).toBeLessThanOrEqual(field.y);
      const log = await box(rail.locator(".rd-review-chat-body"));
      expect(log.height).toBeGreaterThanOrEqual(150);
    }
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(root, { recursive: true, force: true });
  }
});
