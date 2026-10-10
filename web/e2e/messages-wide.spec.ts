import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

const answer = `## A gradual rollout gives us a measurable checkpoint

Start with a small share of traffic. Compare the quality of search results and latency before expanding the rollout.

| Approach | How it works | What to verify |
| --- | --- | --- |
| Shadow traffic | Run the new service alongside production. Continue returning the existing results. | Compare ranking quality and latency across varied requests. |
| Small canary | Send a limited share of requests to the new service, with a fast return to the previous version. | Watch error rates and slow requests. |
| Gradual expansion | Increase traffic in stages after the observation window passes. | Verify performance as query diversity increases. |
| Full cutover | Make the new service the default after all checks pass. | Keep the previous version available for rollback. |

### Recommended next steps

- Agree on ranking quality, error rate, and tail latency checks.
- Run shadow traffic before the canary.

\`\`\`yaml
rollout:
  mode: canary
  traffic_percent: 5
\`\`\`

${"Keep the previous version available until the new service meets the agreed checks.\n\n".repeat(14)}`;

test("wide Messages keeps controls visible, tables contained, copy and turn navigation working", async ({ page }) => {
  const key = await seedSession("messages-wide", { name: "Search rollout", runtime: "codex", test: true });
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  const texts = ["Establish a baseline.", "Measure ranking quality and latency.", "Compare the rollout options.", answer];
  try {
    await page.route(`**/sessions/${key}/messages`, route => route.fulfill({ json: { messages: texts.map((text, id) => ({
      id, message_key: `wide-${id}`, role: id % 2 ? "assistant" : "user", blocks: [{ type: "text", text }],
    })) } }));
    await page.addInitScript(() => {
      localStorage.setItem("rd-theme", "dark");
      Object.defineProperty(navigator, "clipboard", { value: { writeText: async (text: string) => { document.documentElement.dataset.copied = text; } } });
    });
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(base()); await page.locator(".rd-row-name", { hasText: "Search rollout" }).click();
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    await expect(page.getByRole("heading", { name: "A gradual rollout gives us a measurable checkpoint" })).toBeVisible();
    const collapse = page.getByRole("button", { name: "Collapse Context panel" });
    if (await collapse.isVisible()) await collapse.click();
    await expect.poll(() => page.locator(".rd-message-reader").evaluate(el => el.clientWidth)).toBeGreaterThan(900);
    const table = page.getByRole("region", { name: "Message table, scroll horizontally for more columns" });
    await expect(table.getByRole("table")).toBeVisible();
    expect((await table.boundingBox())!.width).toBeGreaterThan(800);
    await page.getByRole("button", { name: "Copy", exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.copied)).toBe(answer);
    await page.screenshot({ path: "/tmp/messages-wide-implemented-dark.png" });
    await page.locator(".rd-message-reader").evaluate(el => el.scrollTop = el.scrollHeight);
    await expect(page.getByRole("button", { name: "Previous turn" })).toBeInViewport();
    await expect(page.getByRole("textbox", { name: "Follow-up message" })).toBeInViewport();
    await page.getByRole("button", { name: "Previous turn" }).click();
    await expect(page.getByText("Measure ranking quality and latency.", { exact: true })).toBeVisible();
    await expect(page.locator(".rd-message-reader")).toHaveJSProperty("scrollTop", 0);
    await page.getByRole("button", { name: "Next turn" }).click();
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await expect(page.getByRole("button", { name: "Copy", exact: true })).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await page.screenshot({ path: "/tmp/messages-wide-implemented-light.png" });
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.getByRole("button", { name: "Show Context panel" }).click();
    await expect.poll(() => table.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
    await table.focus(); await page.keyboard.press("ArrowRight");
    await expect.poll(() => table.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
    await expect(page.getByRole("textbox", { name: "Follow-up message" })).toBeInViewport();
    expect(await page.locator(".rd-message-reader").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/messages-wide-implemented-context.png" });
    expect(errors).toEqual([]);
  } finally { await apiDelete(`/sessions/${key}`); }
});
