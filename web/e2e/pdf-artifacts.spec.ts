import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base, expandFolder } from "./helpers";

function samplePdf(): Buffer {
  const streams = ["BT /F1 24 Tf 45 720 Td (Reader review) Tj 0 -40 Td /F1 14 Tf (Same words on each page.) Tj ET", "BT /F1 24 Tf 45 720 Td (Second page) Tj 0 -40 Td /F1 14 Tf (Same words on each page.) Tj ET", "0.2 0.6 0.4 rg 45 550 400 150 re f"];
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R] /Count 3 >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"];
  streams.forEach((stream, i) => { objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${5 + i * 2} 0 R >>`); objects.push(`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`); });
  let text = "%PDF-1.4\n"; const offsets = [0];
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(text)); text += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(text); text += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, "0") + " 00000 n \n").join("")}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(text);
}

test("PDF pages, selections, scans and feedback preserve exact versions", async ({ page }) => {
  test.setTimeout(90_000);
  // Native WebKit lacks these methods even when the current Chromium has them.
  await page.addInitScript(() => {
    for (const prototype of [Map.prototype, WeakMap.prototype]) Reflect.deleteProperty(prototype, "getOrInsertComputed");
  });
  await page.setViewportSize({ width: 1600, height: 1000 });
  const root = realpathSync(mkdtempSync(join(tmpdir(), "pdf-review-")));
  const pdf = samplePdf(); writeFileSync(join(root, "guide.pdf"), pdf);
  const launched = await apiPost("/sessions/launch", { command: "python3 -u -c 'import sys; print(\"READY\"); [print(\"RECEIVED \"+line) for line in sys.stdin]'", cwd: root, runtime: "generic", name: "PDF review", in_terminal: false, test: true });
  expect(launched.status).toBe(200); const key = String(launched.body.session_key);
  try {
    await apiPatch(`/sessions/${key}`, { group: "PDF review" });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: "PDF review" });
    const register = async (path: string, title: string, bytes: Buffer) => {
      const res = await fetch(`${base()}/api/v1/session/artifacts`, { method: "POST", headers: { Authorization: `Bearer ${enrollment.body.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ source_path: path, title, kind: "spec", content_base64: bytes.toString("base64") }) }); expect(res.status).toBe(200);
    };
    await register("/saved/review.pdf", "Saved PDF review", pdf); await register("/saved/broken.pdf", "Unreadable PDF", Buffer.from("not a PDF"));
    await page.goto(base()); await expandFolder(page, "PDF review"); await page.locator(".rd-row-name", { hasText: "PDF review" }).click();
    const tabs = page.locator(".rd-view-toggle"); await tabs.getByRole("button", { name: "Artifacts", exact: true }).click();
    await page.getByLabel("Artifact format", { exact: true }).selectOption("pdf"); await page.getByRole("button", { name: "PDF Saved PDF review" }).click();
    const reader = page.getByRole("region", { name: "PDF: Saved PDF review" });
    await expect(reader.getByText("Page 1 of 3 · Select text to leave feedback", { exact: true })).toBeVisible();
    await reader.getByRole("button", { name: "Next page" }).click();
    await expect(reader.locator(".rd-pdf-text-layer")).toContainText("Second page");
    await reader.locator(".rd-pdf-text-layer span", { hasText: "Same words" }).evaluate(node => { const r=document.createRange();r.selectNodeContents(node);const s=getSelection()!;s.removeAllRanges();s.addRange(r);node.dispatchEvent(new MouseEvent("mouseup",{bubbles:true})); });
    const feedback = page.getByRole("dialog", { name: "Artifact feedback" }); await expect(feedback).toContainText("Page 2");
    await feedback.getByLabel("Feedback to agent").fill("Clarify the second occurrence"); await feedback.getByRole("button", { name: "Send ⌘↵" }).click();
    await expect(page.getByRole("complementary", { name: "Comment history" })).toContainText("Page 2");
    await page.getByRole("button", { name: "Close comments" }).click();
    await reader.getByRole("button", { name: "Previous page" }).click(); await expect(reader.locator(".rd-pdf-text-layer")).toContainText("Reader review");
    await page.getByRole("button", { name: "Comments (1)" }).click(); await page.getByRole("button", { name: "Go to page 2" }).click();
    await expect(reader.getByLabel("PDF page number")).toHaveValue("2"); await expect(page.getByText("Quote selected in this version.")).toBeVisible();
    await reader.getByLabel("PDF zoom").selectOption("150"); await expect(reader.locator(".rd-pdf-paper")).toHaveCSS("width", "1224px");
    await reader.getByRole("button", { name: "Text", exact: true }).click(); await expect(reader.locator(".rd-pdf-plain")).toContainText("Second page");
    await reader.getByRole("button", { name: "PDF", exact: true }).click(); await reader.getByLabel("PDF zoom").selectOption("fit");
    await reader.getByRole("button", { name: "Next page" }).click(); await expect(reader.getByText(/No selectable text on this page/)).toBeVisible();
    await reader.getByRole("button", { name: "Comment on page" }).click(); await expect(feedback.locator("blockquote")).toHaveText("Feedback on page 3");
    await feedback.getByLabel("Feedback to agent").fill("Explain the green diagram"); await feedback.getByRole("button", { name: "Send ⌘↵" }).click();
    await expect(feedback).not.toBeVisible();
    await expect(page.getByRole("complementary", { name: "Comment history" })).toContainText("Page 3");
    await page.getByRole("button", {name:"Close comments"}).click();
    await reader.getByRole("button", {name:"Previous page"}).click();
    await expect(reader.getByText("Page 2 of 3 · Select text to leave feedback", {exact:true})).toBeVisible();
    await page.screenshot({ path: "/tmp/pdf-artifact-implemented.png" });
    await register("/saved/review.pdf", "Saved PDF review", Buffer.concat([pdf,Buffer.from("\n% updated")]));
    await page.getByRole("button", { name: "Leave feedback" }).click(); await feedback.getByLabel("Feedback to agent").fill("Keep my draft"); await feedback.getByRole("button", { name: "Send ⌘↵" }).click();
    await expect(feedback.getByRole("alert")).toContainText("This artifact changed"); await expect(feedback.getByLabel("Feedback to agent")).toHaveValue("Keep my draft"); await feedback.getByRole("button", { name: "Cancel" }).click();
    await page.getByText("Earlier versions (2)").click(); await expect(page.getByRole("button", { name: "Go to page 2" })).toHaveCount(0);
    await page.getByRole("button", { name: "PDF Unreadable PDF" }).click(); await expect(page.getByRole("alert")).toContainText("could not be read"); await expect(page.getByRole("link", { name: "Download" })).toBeVisible();
    await tabs.getByRole("button", { name: "Code explorer", exact: true }).click(); await page.getByRole("button", { name: /guide.pdf/ }).click();
    const file = page.getByRole("region", { name: "PDF: guide.pdf" }); await expect(file.locator(".rd-pdf-text-layer")).toContainText("Reader review");
    await file.getByRole("button", { name: "Comment on page" }).click(); const fileFeedback=page.getByRole("dialog", {name:"File feedback"}); await fileFeedback.getByLabel("Feedback to agent").fill("Keep page context"); await fileFeedback.getByRole("button", {name:"Send ⌘↵"}).click();
    await expect(page.getByRole("complementary",{name:"Comment history"})).toContainText("Page 1");
    await page.getByRole("button",{name:"Close comments"}).click(); await page.setViewportSize({width:390,height:844}); await page.getByRole("button",{name:"Expand",exact:true}).click(); await expect(file.getByText("Page 1 of 3 · Select text to leave feedback", {exact:true})).toBeVisible(); await page.screenshot({path:"/tmp/pdf-artifact-mobile.png",fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  } finally { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); rmSync(root, { recursive:true,force:true }); }
});
