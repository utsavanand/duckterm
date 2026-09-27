import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

test("full-window artifacts preserve the preview, selection feedback and return path", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const launched = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd: "/tmp", runtime: "generic", name: "expanded-artifact-agent", in_terminal: false, test: true });
  expect(launched.status).toBe(200);
  const key = String(launched.body.session_key);
  try {
    await apiPatch(`/sessions/${key}`, { group: "Expanded previews" });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: "Expanded previews" });
    for (const [ext, title, content] of [
      ["md", "Release review", "# Release review\n\nReady for review.\n\n" + "Useful detail.\n\n".repeat(100)],
      ["html", "Website", "<h1>Website review</h1><p>Select this text.</p>"],
      ["txt", "Notes", "Plain text review"],
      ["svg", "Diagram", '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300"><rect width="600" height="300" fill="#eaf0e7"/><text x="40" y="150" font-size="30">Review → Feedback → Revision</text></svg>'],
    ]) {
      const registered = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: `/tmp/expansion.${ext}`, title, content_base64: Buffer.from(content).toString("base64") }) });
      expect(registered.status).toBe(200);
    }
    await page.goto(base());
    await expandFolder(page, "Expanded previews");
    await page.locator(".rd-row-name", { hasText: "expanded-artifact-agent" }).click();
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.type("PRESERVE_EXPANSION_DRAFT");
    await page.getByRole("button", { name: "Artifacts", exact: true }).click();
    await page.getByRole("button", { name: /Markdown Release review/ }).click();
    const heading = page.frameLocator('.rd-artifact-frame').getByRole("heading");
    await heading.click();
    await expect(page.locator('.rd-artifact-expanded')).toHaveCount(0);
    await heading.evaluate(node => { node.ownerDocument.documentElement.dataset.preserved = "yes"; node.ownerDocument.defaultView!.scrollTo(0, 300); });
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    const expanded = page.getByRole("dialog", { name: "Release review", exact: true });
    await expect(expanded).toBeVisible();
    expect(await expanded.boundingBox()).toEqual({ x: 0, y: 0, width: 1440, height: 1000 });
    expect(await heading.evaluate(node => [node.ownerDocument.documentElement.dataset.preserved, node.ownerDocument.defaultView!.scrollY])).toEqual(["yes", 300]);
    await expect(page.getByRole("button", { name: "Back to sessions" })).toBeFocused();
    expect(await page.locator('.rd-view-toggle').evaluate(node => !!node.closest('[inert]'))).toBe(true);
    const nonce = (await page.locator('.rd-artifact-frame').getAttribute('srcdoc'))!.match(/nonce="([a-f0-9]+)"/)![1];
    await page.evaluate(channel => window.postMessage({ type: "artifact-escape", channel }, "*"), nonce);
    await expect(expanded).toBeVisible();
    await heading.evaluate(node => node.ownerDocument.defaultView!.scrollTo(0, 0));
    await page.screenshot({ path: "/tmp/artifact-expanded-implemented.png" });
    await heading.evaluate(node => {
      const doc = node.ownerDocument, range = doc.createRange(); range.selectNodeContents(node);
      const selection = doc.defaultView!.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
      node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    const feedback = page.getByRole("dialog", { name: "Artifact feedback", exact: true });
    await expect(feedback.locator("blockquote")).toHaveText("“Release review”");
    await page.screenshot({ path: "/tmp/artifact-expanded-feedback-implemented.png" });
    await feedback.getByRole("button", { name: "Cancel" }).focus();
    await page.keyboard.press("Escape");
    await expect(feedback).toHaveCount(0);
    await expect(expanded).toBeVisible();
    await page.locator(".rd-artifact-frame").focus();
    await expect(feedback).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(expanded).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Expand", exact: true })).toBeFocused();
    expect(await heading.evaluate(node => node.ownerDocument.documentElement.dataset.preserved)).toBe("yes");
    await page.getByRole("button", { name: "Expand Release review", exact: true }).click();
    await page.goBack();
    await expect(expanded).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Expand Release review", exact: true })).toBeFocused();
    for (const [type, title] of [["HTML", "Website"], ["Text", "Notes"], ["Image", "Diagram"]]) {
      await page.getByRole("button", { name: new RegExp(`${type} ${title}`) }).click();
      await expect(page.getByRole("button", { name: "Feedback", exact: true })).toBeEnabled();
      await page.getByRole("button", { name: "Expand", exact: true }).click();
      await expect(page.getByRole("dialog", { name: title, exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Feedback", exact: true }).click();
      await feedback.getByRole("textbox").fill(`Expanded_${title}`);
      await feedback.getByRole("button", { name: "Send ⌘↵", exact: true }).click();
      await expect(page.getByText("Sent to the agent", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Back to sessions" }).click();
      await expect(page.locator('.rd-artifact-expanded')).toHaveCount(0);
      await expect(page.getByRole("button", { name: new RegExp(`${type} ${title}`) })).toHaveAttribute("aria-pressed", "true");
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await expect(page.locator('.rd-artifact-expanded')).toHaveCSS('width', '390px');
    await page.screenshot({ path: "/tmp/artifact-expanded-mobile-implemented.png" });
    await page.getByRole("button", { name: "Back to sessions" }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: /Markdown Release review/ }).click();
    await expect(heading).toBeVisible();
    writeFileSync('/tmp/duckterm-expansion-native.json', JSON.stringify({ source: await page.locator('.rd-artifact-frame').getAttribute('srcdoc'), origin: base() }));
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("PRESERVE_EXPANSION_DRAFT");
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("Expanded_Website");
  } finally { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
});
