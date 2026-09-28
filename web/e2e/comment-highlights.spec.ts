import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("saved comments highlight inline text after reload, including keyboard notes and unlocated comments", async ({ page }) => {
  const key = await seedSession("comment-highlights", { name: "Comment review", runtime: "codex", test: true });
  try {
    await page.route(`**/sessions/${key}/messages`, route => route.fulfill({json:{messages:[
      {id:1, role:"user", message_key:"u", blocks:[{type:"text",text:"Review the implementation"}]},
      {id:2, role:"assistant", message_key:"a", blocks:[{type:"text",text:"Keep **session actions** easy to find.\n\nSession notes stay saved."}]},
    ]}}));
    await apiPost(`/sessions/${key}/annotations`, {quote:"session actions",note:"Use equal-size controls"});
    await apiPost(`/sessions/${key}/annotations`, {quote:"Old removed sentence",note:"Keep this feedback available"});
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(base());
    await page.locator(".rd-row-name",{hasText:"Comment review"}).click();
    await page.getByRole("button",{name:"Messages",exact:true}).click();
    await expect(page.locator(".rd-message-comments summary")).toHaveText("2 comments · 1 not located in transcript");
    const mark=page.locator(".rd-msg-text mark");
    await expect(mark).toHaveText("session actions");
    await mark.hover();
    await expect(page.getByRole("tooltip")).toHaveText("Use equal-size controls");
    await page.screenshot({path:"/tmp/duckterm-comment-highlights.png"});
    await page.mouse.move(5,5);
    await mark.focus();
    await expect(page.getByRole("tooltip")).toHaveText("Use equal-size controls");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.reload();
    await page.getByRole("button",{name:"Messages",exact:true}).click();
    await expect(mark).toHaveText("session actions");
    await page.locator(".rd-message-comments summary").click();
    await expect(page.getByText("Keep this feedback available",{exact:true})).toBeVisible();
    await page.screenshot({path:"/tmp/duckterm-comment-list.png"});
  } finally { await apiDelete(`/sessions/${key}`); }
});
