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
    // The From column opens the authoring session.
    await folderRow.getByRole("button", { name: "pr-list-agent" }).click();
    await expect(page.getByRole("region", { name: "Pull requests" })).toBeVisible();
  } finally {
    if (key) await apiDelete(`/sessions/${key}`);
    rmSync(root, { recursive: true, force: true });
  }
});
