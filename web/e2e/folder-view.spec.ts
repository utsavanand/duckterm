import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

test("folder chat and artifacts preserve terminals and follow folder renames", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-folder-view-")));
  const folder = `Website-${Date.now()}`;
  let key = "";
  let current = folder;
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name: "Folder preview agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200);
    key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: folder });
    const response = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: join(cwd, "review.md"), title: "Navigation review", content_base64: Buffer.from("# Navigation review\n\nReady for review.").toString("base64") }) });
    expect(response.status).toBe(200);
    await page.goto(base());
    const header = page.locator(".rd-group-head").filter({ has: page.getByRole("button", { name: `View interactions in ${folder}`, exact: true }) });
    await header.locator(".rd-group-name").click();
    await expect(header.locator(".rd-group-caret")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("region", { name: `Folder ${folder}`, exact: true })).toBeVisible();
    await expect(page.locator(".rd-folder-view .rd-row")).toHaveCount(0);
    await expandFolder(page, folder);
    await expect(page.getByRole("tab", { name: "Chat", exact: true })).toBeVisible();
    await page.locator(".rd-row-name", { hasText: "Folder preview agent" }).click();
    const terminal = page.locator(".rd-terminal-slot:visible .xterm-helper-textarea");
    await terminal.focus();
    await page.keyboard.type("FOLDER_DRAFT_UNSENT");
    await header.locator(".rd-group-name").click();
    await page.getByRole("textbox", { name: "Ask this folder" }).fill("What needs my attention?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.locator(".rd-folder-answer")).toContainText("Use rg");
    await page.screenshot({ path: "/tmp/duckterm-folder-chat-implemented.png" });
    await page.getByRole("tab", { name: "Artifacts", exact: true }).click();
    await expect(page.locator(".rd-folder-files")).toContainText("Folder preview agent");
    await page.screenshot({ path: "/tmp/duckterm-folder-artifacts-implemented.png" });
    await page.getByRole("button", { name: "Navigation review", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Navigation review", exact: true })).toBeVisible();
    await expect(page.frameLocator('iframe[title="Preview of Navigation review"]').getByRole("heading", { name: "Navigation review" })).toBeVisible();
    await page.getByRole("button", { name: "← Back to folder", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Navigation review", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Navigation review", exact: true })).toBeFocused();
    await page.locator(".rd-row-name", { hasText: "Folder preview agent" }).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("FOLDER_DRAFT_UNSENT");
    await header.locator(".rd-group-name").click();
    await expect(page.locator(".rd-folder-answer")).toContainText("Use rg");
    page.once("dialog", dialog => dialog.accept("Renamed website"));
    await header.getByTitle("Rename this folder (double-clicking the name works too)").click();
    current = "Renamed website";
    await expect(page.locator(".rd-folder-heading h1")).toHaveText(current);
    await expect(page.locator(".rd-folder-answer")).toContainText("Use rg");
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/duckterm-folder-chat-light-implemented.png" });
    await page.setViewportSize({ width: 900, height: 900 });
    await expect(page.getByRole("textbox", { name: "Ask this folder" })).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-folder-chat-narrow-implemented.png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    await apiDelete(`/folders/${encodeURIComponent(current)}`);
    rmSync(cwd, { recursive: true, force: true });
  }
});
