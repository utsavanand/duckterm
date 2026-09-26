import { writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

test("artifact selections and whole-file feedback reach their agent, with isolated previews and retained errors", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const launched = await apiPost("/sessions/launch", { command: "python3 -u -c 'import sys,json; print(\"FEEDBACK_READY\"); [print(\"RECEIVED_\"+json.loads(line.removeprefix(\"Artifact feedback: \"))[\"feedback\"]) for line in sys.stdin]'", cwd: "/tmp", runtime: "generic", name: "artifact-feedback-agent", in_terminal: false, test: true });
  expect(launched.status).toBe(200);
  const key = String(launched.body.session_key);
  try {
    await apiPatch(`/sessions/${key}`, { group: "Feedback probes" });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: "Feedback probes" });
    const register = async (path: string, title: string, content: string) => {
      const response = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: path, title, content_base64: Buffer.from(content).toString("base64") }) });
      expect(response.status).toBe(200);
      return (await response.json()).artifact;
    };
    await register("/tmp/feedback-report.md", "Report", "# Clearer heading\n\nPlease review this report.");
    await register("/tmp/feedback-layout.html", "Layout", '<h1>Layout heading</h1><script>window.artifactAttack=true;fetch("/artifact-leak")</script><img src="/artifact-leak"><p>Mockup body</p>');
    await register("/tmp/feedback-notes.txt", "Notes", "Text selection works here.");
    await register("/tmp/feedback-image.svg", "Diagram", '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><text x="10" y="50">Diagram</text></svg>');
    await page.goto(base());
    await expandFolder(page, "Feedback probes");
    await page.locator(".rd-row-name", { hasText: "artifact-feedback-agent" }).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("FEEDBACK_READY");
    await page.getByRole("button", { name: "Artifacts", exact: true }).click();
    const leaks: string[] = [];
    await page.route("**/artifact-leak", route => { leaks.push(route.request().url()); return route.abort(); });
    for (const [type, title, quote] of [["Markdown", "Report", "Clearer heading"], ["HTML", "Layout", "Layout heading"], ["Text", "Notes", "Text selection works here."]]) {
      await page.getByRole("button", { name: new RegExp(`${type} ${title}`) }).click();
      const text = title === "Notes" ? page.locator(".rd-artifact-text") : page.frameLocator(`iframe[title="Preview of ${title}"]`).getByRole("heading").first();
      await text.waitFor();
      if (title === "Layout") {
        writeFileSync("/tmp/duckterm-feedback-native.json", JSON.stringify({ source: await page.locator(".rd-artifact-frame").getAttribute("srcdoc"), origin: base() }));
        const attack = await text.evaluate(node => (node.ownerDocument.defaultView as Window & { artifactAttack?: boolean }).artifactAttack);
        expect(attack).toBeUndefined();
        expect(leaks).toEqual([]);
        const nonce = (await page.locator(".rd-artifact-frame").getAttribute("srcdoc"))!.match(/nonce="([a-f0-9]+)"/)![1];
        await page.evaluate(channel => window.postMessage({ type: "artifact-selection", channel, quote: "Spoofed", left: 0, bottom: 0 }, "*"), nonce);
        await expect(page.getByRole("dialog", { name: "Artifact feedback" })).toHaveCount(0);
      }
      await text.evaluate(node => {
        const selection = node.ownerDocument.defaultView!.getSelection()!;
        const range = node.ownerDocument.createRange(); range.selectNodeContents(node); selection.removeAllRanges(); selection.addRange(range);
        node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      });
      const dialog = page.getByRole("dialog", { name: "Artifact feedback" });
      await expect(dialog.locator("blockquote")).toHaveText(`“${quote}”`);
      await dialog.getByRole("textbox").fill(`Improve_${title}`);
      if (title === "Report") await page.screenshot({ path: "/tmp/duckterm-artifact-feedback-implemented.png" });
      await dialog.getByRole("button", { name: "Send ⌘↵", exact: true }).click();
      await expect(page.getByText("Sent to the agent", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Terminal", exact: true }).click();
      await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText(`RECEIVED_Improve_${title}`);
      await page.getByRole("button", { name: "Artifacts", exact: true }).click();
    }
    await page.getByRole("button", { name: /Image Diagram/ }).click();
    await page.getByRole("button", { name: "Feedback", exact: true }).click();
    await expect(page.getByRole("dialog").locator("blockquote")).toHaveText("Feedback on the whole artifact");
    await page.getByRole("textbox", { name: "Feedback to agent" }).fill("Improve_diagram");
    await page.getByRole("button", { name: "Send ⌘↵", exact: true }).click();
    await expect(page.getByText("Sent to the agent", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /Markdown Report/ }).click();
    await page.getByRole("button", { name: "Feedback", exact: true }).click();
    await page.getByRole("textbox", { name: "Feedback to agent" }).fill("Keep this comment");
    await register("/tmp/feedback-report.md", "Report", "# Changed revision");
    await page.getByRole("button", { name: "Send ⌘↵", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("This artifact changed");
    await expect(page.getByRole("textbox", { name: "Feedback to agent" })).toHaveValue("Keep this comment");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.frameLocator('iframe[title="Preview of Report"]').getByRole("heading", { name: "Changed revision" })).toBeVisible({ timeout: 8000 });
    await apiPost(`/sessions/${key}/stop`);
    await page.getByRole("button", { name: "Feedback", exact: true }).click();
    await page.getByRole("textbox", { name: "Feedback to agent" }).fill("Retry later");
    await page.getByRole("button", { name: "Send ⌘↵", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("no live terminal");
    await expect(page.getByRole("textbox", { name: "Feedback to agent" })).toHaveValue("Retry later");
  } finally { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
});
