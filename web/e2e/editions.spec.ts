import { expect, test, type Page } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder, routeEdition, sessionMenu } from "./helpers";
import { CODE, FULL, LITE } from "../src/test/editionFeatures";

// Endpoint families that belong to features outside Lite.
const ADVANCED = [/\/relay/, /\/session-inbox-counts/, /\/agents-md/, /\/checkpoints?\b/, /\/artifacts/, /\/prs\b/, /\/connectors/,
  /\/oracle/, /\/layouts/, /\/tasks/, /\/timeline/, /\/digest/, /\/diff\b/, /\/branches/, /\/folders\/.+\/(chat|stats)/, /\/folder-interactions/];

function recordRequests(page: Page): string[] {
  const paths: string[] = [];
  page.on("request", request => { if (request.url().startsWith(base())) paths.push(new URL(request.url()).pathname); });
  return paths;
}

async function withSession(name: string, run: (folder: string, cwd: string) => Promise<void>) {
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-edition-")));
  writeFileSync(join(cwd, "notes.txt"), "EDITION_FILE\n");
  const folder = `Edition probes ${Date.now()}`;
  const launched = await apiPost("/sessions/launch", { command: "sh -c 'echo EDITION_AGENT; exec cat'", cwd, runtime: "generic", name, in_terminal: false, test: true });
  expect(launched.status).toBe(200);
  const key = String(launched.body.session_key);
  try {
    await apiPatch(`/sessions/${key}`, { group: folder });
    await run(folder, cwd);
  } finally {
    await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(cwd, { recursive: true, force: true });
  }
}

async function open(page: Page, folder: string, name: string) {
  await page.goto(base());
  await expandFolder(page, folder);
  await page.locator(".rd-row-name", { hasText: name }).click();
  await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("EDITION_AGENT");
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
});

test("Lite runs the core session flows without any advanced control or request", async ({ page }) => {
  await routeEdition(page, "lite", LITE);
  const requests = recordRequests(page);
  await withSession("lite-agent", async folder => {
    await open(page, folder, "lite-agent");
    const views = page.locator(".rd-view-toggle");
    await expect(views.getByRole("button")).toHaveText(["Terminal", "Messages", /Shell/]);
    await expect(page.getByRole("button", { name: "Oracle", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "AGENTS.md", exact: true })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Connectors" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: `View interactions in ${folder}` })).toHaveCount(0);

    // Core flows: the shell, messages, the session menu and folder controls.
    await page.locator("#rd-shell-toggle button").click();
    await expect(page.getByRole("region", { name: "Session shell" })).toBeVisible();
    await views.getByRole("button", { name: "Messages" }).click();
    await views.getByRole("button", { name: "Terminal" }).click();
    const menu = await sessionMenu(page);
    await expect(menu.getByRole("menuitem", { name: "Rename" })).toBeVisible();
    for (const name of ["Checkpoint", "Move to remote", "Merge back", "Merge with agent"]) await expect(menu.getByRole("menuitem", { name })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Settings" }).click();
    for (const name of ["Collaboration", "Checkpointing"]) await expect(page.getByRole("button", { name: new RegExp(`^${name}`) })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Harnesses/ })).toBeVisible();
    await expect(page.getByText("Voice announcements")).toHaveCount(0);
    await page.keyboard.press("Escape");

    // Clicking a folder name opens no folder page (folder chat is not in Lite).
    await page.locator(".rd-group-name", { hasText: folder }).click();
    await expect(page.getByRole("region", { name: `Folder ${folder}` })).toHaveCount(0);
    await page.waitForTimeout(3500); // past the inbox and relay poll intervals
  });
  expect(requests).toContain("/edition");
  expect(requests.filter(path => ADVANCED.some(pattern => pattern.test(path)))).toEqual([]);
});

test("Code's Explorer browses files without artifact requests and offers pull requests but not the Oracle", async ({ page }) => {
  await routeEdition(page, "code", CODE);
  const requests = recordRequests(page);
  await withSession("code-agent", async folder => {
    await open(page, folder, "code-agent");
    const views = page.locator(".rd-view-toggle");
    for (const name of ["Timeline", "Explorer", "PRs"]) await expect(views.getByRole("button", { name, exact: true })).toBeVisible();
    await expect(views.getByRole("button", { name: /^Inbox/ })).toBeVisible();
    for (const name of ["Artifacts", "Oracle"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    await views.getByRole("button", { name: "Explorer" }).click();
    const files = page.getByRole("region", { name: "Session files" });
    await files.getByRole("button", { name: /notes.txt/ }).click();
    await expect(files).toContainText("EDITION_FILE");
    await expect(files.getByRole("group", { name: "Files filter" })).toHaveCount(0);
    await page.locator(".rd-group-name", { hasText: folder }).click();
    await expect(page.getByRole("region", { name: `Folder ${folder}` }).getByRole("tab")).toHaveText(["Chat", "Pull requests"]);
  });
  expect(requests.filter(path => /\/artifacts|\/relay|\/oracle/.test(path))).toEqual([]);
});

test("Full keeps its views and excludes pull request review", async ({ page }) => {
  await routeEdition(page, "full", FULL);
  const requests = recordRequests(page);
  await withSession("full-agent", async folder => {
    await open(page, folder, "full-agent");
    const views = page.locator(".rd-view-toggle");
    for (const name of ["Timeline", "Artifacts"]) await expect(views.getByRole("button", { name, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Oracle", exact: true })).toBeVisible();
    await expect(views.getByRole("button", { name: "PRs", exact: true })).toHaveCount(0);
    await page.locator(".rd-group-name", { hasText: folder }).click();
    await expect(page.getByRole("region", { name: `Folder ${folder}` }).getByRole("tab")).toHaveText(["Chat", "Artifacts"]);
  });
  expect(requests.filter(path => /\/prs\b|\/review\//.test(path))).toEqual([]);
});

test("an unavailable edition keeps only the core instead of assuming Full", async ({ page }) => {
  await page.route("**/edition", route => route.fulfill({ status: 503, json: { error: "Unavailable" } }));
  const requests = recordRequests(page);
  await withSession("unknown-agent", async folder => {
    await open(page, folder, "unknown-agent");
    await expect(page.getByText(/Feature list unavailable/)).toBeVisible();
    await expect(page.locator(".rd-view-toggle").getByRole("button")).toHaveText(["Terminal", "Messages", /Shell/]);
  });
  expect(requests.filter(path => ADVANCED.some(pattern => pattern.test(path)))).toEqual([]);
});
