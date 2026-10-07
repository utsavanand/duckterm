import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPatch, apiPost, base, expandFolder, seedSession } from "./helpers";

test("configure a merge between independent agents, recover a lost response and preserve both sessions", async ({ page }) => {
  const folder = `Merge agents ${Date.now()}`, source = `agent-source-${Date.now()}`, target = `agent-target-${Date.now()}`;
  try {
    await seedSession(source, { name: "Feature implementation", runtime: "generic", group: folder, test: true });
    await seedSession(target, { name: "Main development", runtime: "generic", group: folder, test: true });
    await apiPatch(`/sessions/${source}`, { notes: "Preserve the public API." });
    await apiPatch(`/sessions/${target}`, { notes: "Existing destination notes." });
    expect((await apiPost(`/sessions/${target}/collaboration`, { root: folder })).status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "Main development" }).click();
    const row = page.locator(".rd-row", { has: page.getByText("Feature implementation", { exact: true }) });
    await row.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent", exact: true });
    await dialog.getByLabel("Destination agent").selectOption(target);
    await expect(dialog.getByLabel("Notes to append")).toHaveValue("Preserve the public API.");
    await dialog.getByRole("checkbox", { name: /Conversation context/ }).check();
    const context = "Completed sign-in validation. 🦆 Remaining: verify expiry. \n";
    await dialog.getByLabel("Context to send").fill(context);
    await expect(dialog.getByRole("checkbox", { name: /Code from/ })).toBeDisabled();
    await expect(row).toHaveClass(/context-target/);
    await expect(page.locator(".rd-row.selected")).toContainText("Main development");
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-configure.png" });
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await expect(dialog.getByText(/receives the request in its inbox/)).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await dialog.getByRole("button", { name: "Send merge request" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-review-narrow.png" });
    let sent = 0;
    await page.route(`**/sessions/${source}/agent-merge`, async route => {
      if (route.request().method() === "POST" && sent++ === 0) { await route.fetch(); await route.abort("failed"); }
      else await route.continue();
    });
    await dialog.getByRole("button", { name: "Send merge request" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Retry unchanged");
    await dialog.getByRole("button", { name: "Send merge request" }).click();
    await expect(dialog.getByRole("region", { name: "Saved merge request" })).toBeVisible();
    await expect(dialog.getByText(/Notes appended once/)).toBeVisible();
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await page.reload(); await expandFolder(page, folder);
    await row.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    await dialog.getByText("Recent merge requests (1)", { exact: true }).click();
    await dialog.getByRole("button", { name: /Feature implementation → Main development/ }).click();
    await expect(dialog.locator("pre")).toContainText(context.trim());
    const data = await (await page.request.get(`${base()}/sessions`)).json();
    const destination = data.sessions.find((s: { session_key: string }) => s.session_key === target);
    expect(destination.notes).toBe(`Existing destination notes.\n\nFrom Feature implementation (${source}):\nPreserve the public API.`);
    expect(data.sessions.find((s: { session_key: string }) => s.session_key === source).state).not.toBe("merged");
  } finally { await apiDelete(`/sessions/${source}`); await apiDelete(`/sessions/${target}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`); }
});


test("different worktrees offer the exact committed code for review without changing either worktree", async ({ page }) => {
  const root = mkdtempSync(join(tmpdir(), "duckterm-merge-code-"));
  const repo = join(root, "main"), branch = join(root, "feature");
  const git = (path: string, ...args: string[]) => execFileSync("git", ["-C", path, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  const source = `code-source-${Date.now()}`, target = `code-target-${Date.now()}`, folder = `Code merge ${Date.now()}`;
  try {
    mkdirSync(repo); git(repo, "init", "-b", "main");
    git(repo, "config", "user.name", "QA"); git(repo, "config", "user.email", "qa@example.test");
    writeFileSync(join(repo, "base.txt"), "base"); git(repo, "add", "base.txt"); git(repo, "commit", "-m", "base");
    git(repo, "worktree", "add", "-b", "feature", branch);
    writeFileSync(join(branch, "feature.txt"), "reviewed feature"); git(branch, "add", "feature.txt"); git(branch, "commit", "-m", "feature");
    const original = git(repo, "rev-parse", "HEAD"), reviewed = git(branch, "rev-parse", "HEAD");
    await seedSession(source, { name: "Feature worktree", cwd: branch, runtime: "generic", group: folder, test: true });
    await seedSession(target, { name: "Main worktree", cwd: repo, runtime: "generic", group: folder, test: true });
    expect((await apiPost(`/sessions/${target}/collaboration`, { root: folder })).status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row", { has: page.getByText("Feature worktree", { exact: true }) }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent" });
    await dialog.getByLabel("Destination agent").selectOption(target);
    const option = dialog.getByRole("checkbox", { name: /Code from/ });
    await expect(option).toBeEnabled(); await expect(option).not.toBeChecked();
    await option.check();
    await expect(dialog.locator("code")).toContainText(reviewed);
    await expect(dialog.locator("pre")).toContainText("feature.txt");
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-code-review.png" });
    await dialog.getByRole("button", { name: "Send merge request", exact: true }).click();
    await expect(dialog.getByText(/Code integration was requested/)).toBeVisible();
    expect(git(repo, "rev-parse", "HEAD")).toBe(original);
    expect(git(branch, "rev-parse", "HEAD")).toBe(reviewed);
    expect(git(repo, "status", "--porcelain")).toBe("");
  } finally {
    await apiDelete(`/sessions/${source}`); await apiDelete(`/sessions/${target}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(root, { recursive: true, force: true });
  }
});
