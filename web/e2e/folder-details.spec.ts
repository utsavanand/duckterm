import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base } from "./helpers";

test("folder details keep categorized files and show removed provenance across reloads", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-folder-details-")));
  const folder = `Website review ${Date.now()}`;
  let key = "";
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name: "Design agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200); key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: folder });
    const ids: string[] = [];
    for (const title of ["Navigation decision", "Accessibility research", "Earlier preview"]) {
      const response = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: join(cwd, `${title}.md`), title, content_base64: Buffer.from(`# ${title}\n\nReview notes.`).toString("base64") }) });
      expect(response.status).toBe(200); ids.push((await response.json()).artifact.id);
    }
    await apiDelete(`/sessions/${key}/artifacts/${ids[2]}`);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" }); await page.goto(base());
    const openFolder = async () => { await page.locator(".rd-group-head").filter({ has: page.getByRole("button", { name: `View interactions in ${folder}`, exact: true }) }).locator(".rd-group-name").click(); };
    await openFolder();
    const details = page.getByRole("complementary", { name: "Folder details", exact: true });
    await expect(details).toContainText("1 session");
    await expect(details).toContainText("2 available · 1 removed");
    await page.getByRole("tab", { name: "Artifacts", exact: true }).click();
    const artifacts = page.getByRole("region", { name: "Folder artifacts", exact: true });
    await expect(artifacts.getByRole("button", { name: "Earlier preview", exact: true })).toHaveCount(0);
    await artifacts.getByRole("button", { name: "Keep Navigation decision", exact: true }).click();
    await expect(artifacts.getByRole("button", { name: "Undo Keep for Navigation decision", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(apiDelete(`/sessions/${key}/artifacts/${ids[0]}`)).rejects.toThrow("409");
    await artifacts.getByRole("combobox", { name: "Kind for Accessibility research" }).selectOption("report");
    await expect(artifacts.getByRole("combobox", { name: "Kind for Accessibility research" })).toBeEnabled();
    await page.reload(); await openFolder(); await page.getByRole("tab", { name: "Artifacts", exact: true }).click();
    await expect(artifacts.getByRole("button", { name: "Undo Keep for Navigation decision", exact: true })).toBeVisible();
    await expect(artifacts.getByRole("combobox", { name: "Kind for Accessibility research" })).toHaveValue("report");
    await artifacts.getByRole("group", { name: "Artifact kinds" }).getByRole("button", { name: "Report", exact: true }).click();
    await expect(artifacts.locator("tbody tr")).toHaveCount(1);
    await artifacts.getByRole("group", { name: "Artifact kinds" }).getByRole("button", { name: "All", exact: true }).click();
    await artifacts.getByRole("checkbox", { name: "Show removed" }).check();
    await page.screenshot({ path: "/tmp/duckterm-folder-details-implemented.png" });
    await artifacts.getByRole("button", { name: "Earlier preview", exact: true }).click();
    const removed = page.getByRole("dialog", { name: "Earlier preview", exact: true });
    await expect(removed).toContainText("Saved content removed");
    await expect(removed.getByRole("link", { name: "Download" })).toHaveCount(0);
    await removed.getByRole("button", { name: "← Back to folder", exact: true }).click();
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: "/tmp/duckterm-folder-details-light.png" });
    await page.setViewportSize({ width: 900, height: 900 });
    await expect(details).toHaveCount(0);
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await expect(details).toBeVisible();
    await expect(details.getByRole("button", { name: "Add widget" })).toBeEnabled();
    await page.screenshot({ path: "/tmp/duckterm-folder-details-narrow.png" });
    await details.getByRole("button", { name: "Close folder details" }).click();
    await expect(details).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    await apiDelete(`/folders/${encodeURIComponent(folder)}`); rmSync(cwd, { recursive: true, force: true });
  }
});
