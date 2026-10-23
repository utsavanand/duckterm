import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

// A session working in a git checkout whose branch is pushed to a local
// `origin` but has no pull request: the lists must show it from git facts
// alone, with GitHub reported as not connected rather than failing.
function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", ["-c", "user.email=qa@duckterm.local", "-c", "user.name=QA", "-c", "commit.gpgsign=false", ...args], { cwd, encoding: "utf8" }).trim();
}

test("session PRs tab and folder Pull requests tab list a pushed branch without a PR", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const root = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-pr-list-")));
  const origin = join(root, "origin.git");
  const work = join(root, "work");
  git(root, "init", "-q", "--bare", origin);
  git(root, "init", "-q", "-b", "main", work);
  writeFileSync(join(work, "README.md"), "base\n");
  git(work, "add", "README.md");
  git(work, "commit", "-q", "-m", "initial");
  git(work, "remote", "add", "origin", origin);
  git(work, "push", "-q", "-u", "origin", "main");
  git(work, "checkout", "-q", "-b", "duckterm/e2e-review");
  writeFileSync(join(work, "feature.py"), "def feature():\n    return 1\n");
  git(work, "add", "feature.py");
  git(work, "commit", "-q", "-m", "add feature");
  git(work, "push", "-q", "-u", "origin", "duckterm/e2e-review");
  const folder = `Review probes ${Date.now()}`;
  let key = "";
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'exec cat'", cwd: work, runtime: "generic", name: "pr-list-agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200);
    key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });

    await page.goto(base());
    await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "pr-list-agent" }).click();
    await page.getByRole("button", { name: "PRs", exact: true }).click();
    const list = page.getByRole("region", { name: "Pull requests" });
    const row = list.getByRole("row").filter({ hasText: "duckterm/e2e-review" });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row.locator(".rd-pr-status")).toHaveText("Pushed, no PR");
    await expect(row.locator(".rd-prs-size")).toContainText("+2");
    await expect(row.locator(".rd-prs-branch")).toContainText("duckterm/e2e-review → main");
    await expect(list.getByRole("status")).toContainText("GitHub is not connected");
    await page.screenshot({ path: "/tmp/duckterm-pr-list-session.png" });

    const header = page.locator(".rd-group-head").filter({ has: page.getByRole("button", { name: `View interactions in ${folder}`, exact: true }) });
    await header.locator(".rd-group-name").click();
    await page.getByRole("tab", { name: "Pull requests", exact: true }).click();
    const folderList = page.getByRole("region", { name: `Folder ${folder}` }).getByRole("region", { name: "Pull requests" });
    await expect(folderList.getByRole("columnheader", { name: "From" })).toBeVisible();
    const folderRow = folderList.getByRole("row").filter({ hasText: "duckterm/e2e-review" });
    await expect(folderRow.locator(".rd-pr-status")).toHaveText("Pushed, no PR", { timeout: 20_000 });
    await page.screenshot({ path: "/tmp/duckterm-pr-list-folder.png" });
    // Review opens the workspace: the diff against main, bucketed, with viewed state.
    await folderRow.getByRole("button", { name: "Review" }).click();
    const workspace = page.getByRole("region", { name: "Review workspace" });
    await expect(workspace.getByRole("heading", { name: "duckterm/e2e-review" })).toBeVisible();
    const changed = workspace.getByRole("navigation", { name: "Changed files" });
    await expect(changed.getByRole("heading", { level: 3 })).toHaveText(["Worth a read 1"]);
    await expect(workspace.getByRole("article", { name: "feature.py" })).toContainText("Worth a read · Source file");
    await expect(workspace.locator(".rd-hunk-table")).toContainText("def feature():");
    await workspace.getByRole("button", { name: "Mark viewed" }).click();
    await expect(workspace.getByText("1/1 viewed")).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-pr-review-diff.png" });

    // A push that only reindents feature.py and adds a doc keeps feature.py
    // viewed and flags what changed since this look.
    writeFileSync(join(work, "feature.py"), "def feature():\n\treturn 1\n");
    writeFileSync(join(work, "NOTES.md"), "# Notes\n");
    git(work, "add", "feature.py", "NOTES.md");
    git(work, "commit", "-q", "-m", "reindent and add notes");
    git(work, "push", "-q", "origin", "duckterm/e2e-review");
    await workspace.getByRole("button", { name: "← Pull requests" }).click();
    await folderList.getByRole("button", { name: "Refresh" }).click();
    await expect(folderList.getByRole("row").filter({ hasText: "duckterm/e2e-review" }).locator(".rd-prs-size")).toContainText("2 files", { timeout: 20_000 });
    await folderList.getByRole("row").filter({ hasText: "duckterm/e2e-review" }).getByRole("button", { name: "Review" }).click();
    await expect(changed.getByRole("heading", { level: 3 })).toHaveText(["Worth a read 1", "Safe to skim 1"]);
    await expect(changed.getByRole("checkbox", { name: "Viewed feature.py" })).toBeChecked();
    await expect(changed.getByRole("checkbox", { name: "Viewed NOTES.md" })).not.toBeChecked();
    await expect(workspace.getByLabel(/Since my last look · 2 files/)).toBeVisible();
    await expect(workspace.getByText("1/2 viewed")).toBeVisible();
    // The chat panel collapses to a tab and reopens from a hunk.
    await workspace.getByRole("button", { name: "Collapse chat" }).click();
    await expect(workspace.getByRole("textbox", { name: "Ask about this pull request" })).toBeHidden();
    await changed.getByRole("button", { name: /NOTES.md/ }).click();
    await workspace.getByRole("button", { name: "Ask about this hunk" }).click();
    await expect(workspace.getByRole("complementary", { name: "Review chat" })).toContainText("NOTES.md @@");
    await page.screenshot({ path: "/tmp/duckterm-pr-review-since.png" });

    // Review tab: the deterministic release card is ready at once; model cards
    // are omitted with the reason because the test server has no provider.
    await workspace.getByRole("tab", { name: "Review" }).click();
    const strip = workspace.getByRole("tablist", { name: "Review cards" });
    await expect(strip.getByRole("tab")).toHaveCount(10);
    await expect(strip.getByRole("tab", { name: /Release impact/ })).toContainText("Low risk if", { timeout: 20_000 });
    await expect(strip.getByRole("tab", { name: /Intent vs diff/ })).toContainText("Omitted: Automatic preparation is disabled", { timeout: 30_000 });
    // The fixture commits lack the problem / fix / how-tested subject: one mechanical finding.
    await expect(strip.getByRole("tab", { name: /Guardrails/ })).toContainText("1 finding", { timeout: 30_000 });
    await expect(strip.getByRole("tab", { name: /^6 Tests/ })).toContainText("0 slop · no coverage report");
    await strip.getByRole("tab", { name: /Guardrails/ }).click();
    const guard = workspace.getByRole("tabpanel", { name: "7 Guardrails" });
    await expect(guard).toContainText("Slop-rule review omitted: Automatic preparation is disabled");
    await expect(guard).toContainText("Commit messages: problem / fix / how-tested");
    await expect(guard).toContainText("reindent and add notes");
    await strip.getByRole("tab", { name: /Release impact/ }).click();
    const release = workspace.getByRole("tabpanel", { name: "9 Release impact" });
    await expect(release).toContainText("No CI result yet; open a PR so checks run");
    await expect(release.getByText("pass").first()).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-pr-review-cards.png" });
    // Chat (still open from the hunk question): the server's refusal shows inline.
    const panel = workspace.getByRole("complementary", { name: "Review chat" });
    await panel.getByRole("textbox", { name: "Ask about this pull request" }).fill("What changed?");
    await panel.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(panel.getByRole("alert")).toContainText("Automatic preparation is disabled", { timeout: 20_000 });
  } finally {
    if (key) await apiDelete(`/sessions/${key}`);
    rmSync(root, { recursive: true, force: true });
  }
});
