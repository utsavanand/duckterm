import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

test("Markdown diagrams and long documents stay readable in isolated themed previews", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-diagrams-test-")));
  let key = "";
  try {
    const launched = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name: "diagram-test-agent", in_terminal: false, test: true });
    expect(launched.status).toBe(200);
    key = String(launched.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: "Diagram probes" });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: "Diagram probes" });
    expect(enrollment.status).toBe(200);
    const leaked: string[] = [];
    await page.route('**/artifact-leak', route => { leaked.push(route.request().url()); return route.abort(); });
    const source = '<img src="'+base()+'/artifact-leak">\n\n# Diagram report\n\nA readable document.\n\n```mermaid\nflowchart TB\nA[Saved artifact] --> B[Safe preview]\n```\n\n| Field | Meaning | Notes |\n| --- | --- | --- |\n| status | Current work | Clear evidence |\n\n```text\n'+ 'A'.repeat(120)+'\n    B\n```\n\n```mermaid\ninvalid diagram source\n```';
    const saved = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: join(cwd, "diagrams.md"), title: "Diagram report", content_base64: Buffer.from(source).toString("base64") }) });
    expect(saved.status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await expandFolder(page, "Diagram probes");
    await page.locator(".rd-row-name", { hasText: "diagram-test-agent" }).click();
    await page.getByRole("button", { name: "Artifacts", exact: true }).click();
    const iframe = page.locator('iframe[title="Preview of Diagram report"]');
    const frame = page.frameLocator('iframe[title="Preview of Diagram report"]');
    await expect(frame.locator('.diagram svg text')).toHaveCount(2);
    await expect(frame.locator('.diagram')).toContainText("Saved artifact");
    await expect(frame.getByText("Diagram could not render. Source shown below.")).toBeVisible();
    await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    expect(await frame.locator('svg foreignObject').count()).toBe(0);
    expect(await frame.locator('script').count()).toBe(1); // existing selection reporter only
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await expect.poll(() => frame.locator('body').evaluate(e => getComputedStyle(e).color)).toBe("rgb(226, 230, 227)");
    await expect(frame.locator('.diagram svg text')).toHaveCount(2);
    await page.screenshot({ path: "/tmp/artifact-diagrams-implemented-dark.png" });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await expect.poll(() => frame.locator('body').evaluate(e => getComputedStyle(e).color)).toBe("rgb(39, 53, 44)");
    await expect(frame.locator('.diagram svg text')).toHaveCount(2);
    await page.screenshot({ path: "/tmp/artifact-diagrams-implemented-light.png" });
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await frame.locator('body').evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await frame.locator('.table-scroll').evaluate(e => e.scrollWidth > e.clientWidth)).toBe(true);
    const code = frame.locator('pre').first();
    expect(await code.evaluate(e => getComputedStyle(e).whiteSpace)).toBe("pre");
    expect(await code.evaluate(e => e.scrollWidth > e.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/artifact-diagrams-implemented-narrow.png" });
    expect(leaked).toEqual([]);
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    rmSync(cwd, { recursive: true, force: true });
  }
});
