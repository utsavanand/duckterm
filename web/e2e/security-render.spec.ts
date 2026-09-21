import { expect, test } from "@playwright/test";
import { tmpdir } from "node:os";
import { apiPost, base } from "./helpers";

test("patched diagram dependencies render while message HTML stays sanitized", async ({ page }) => {
  const launched = await apiPost("/sessions/launch", {
    command: "cat",
    cwd: tmpdir(),
    name: "security-render",
    runtime: "generic",
    in_terminal: false,
    test: true,
  });
  expect(launched.status).toBe(200);
  const key = launched.body.session_key as string;
  await page.route(`**/sessions/${key}/messages`, (route) => route.fulfill({
    json: {
      messages: [{
        id: 1,
        role: "assistant",
        blocks: [{
          type: "text",
          text: [
            '<img src="invalid" onerror="document.body.dataset.compromised=1">',
            '<script>document.body.dataset.compromised=1</script>',
            "```mermaid\nflowchart LR\nA[Safe] --> B[Diagram]\n```",
            "```mermaid\nmindmap\n  root((Safe))\n    Diagram\n```",
          ].join("\n\n"),
        }],
      }],
    },
  }));
  try {
    const response = await page.goto(base());
    expect(response?.headers()["x-frame-options"]).toBe("DENY");
    expect(response?.headers()["cache-control"]).toBe("no-store");
    await page.locator(".rd-row-name", { hasText: "security-render" }).click();
    await page.locator(".rd-view-toggle button", { hasText: "Messages" }).click();
    await expect(page.locator(".rd-mermaid-svg svg")).toHaveCount(2);
    await expect(page.locator(".rd-mermaid-svg").first()).toContainText("Safe");
    expect(await page.locator("body").getAttribute("data-compromised")).toBeNull();
    await expect(page.locator(".rd-msg-text script, .rd-msg-text [onerror]")).toHaveCount(0);
  } finally {
    await apiPost(`/sessions/${key}/stop`);
  }
});
