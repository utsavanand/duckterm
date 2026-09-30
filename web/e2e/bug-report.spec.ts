import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPost, base } from "./helpers";

test("bug report downloads the exact reviewed body and captured attachments", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-bug-report-")));
  let key = "";
  try {
    const result = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name: "Report test", in_terminal: false, test: true });
    expect(result.status).toBe(200); key = String(result.body.session_key);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(base());
    await page.getByRole('button', { name: 'Settings', exact: false }).click();
    await page.getByRole('button', { name: 'Report a bug', exact: false }).click();
    const report = page.getByRole('dialog', { name: 'Report a bug', exact: true });
    await report.getByRole('textbox', { name: 'Summary' }).fill('Clipboard test');
    await report.getByRole('textbox', { name: 'What happened?' }).fill('Selected terminal text did not copy.\nExpected the selected text in the clipboard.');
    await expect(report.getByRole('checkbox', { name: /DuckTerm version/ })).toBeChecked();
    await report.getByRole('checkbox', { name: /DuckTerm version/ }).uncheck();
    const files = [1, 2, 3].map(n => ({ name: `attachment-${n}.bin`, mimeType: 'application/octet-stream', buffer: Buffer.alloc(5 * 1024 * 1024, 255) }));
    await report.getByLabel('Attachments', { exact: true }).setInputFiles(files);
    await expect(report.getByRole('button', { name: 'Remove attachment-3.bin', exact: true })).toBeVisible();
    const reviewed = await report.getByLabel('Complete report').innerText();
    expect(reviewed).not.toContain('### DuckTerm version');
    await page.screenshot({ path: '/tmp/duckterm-bug-report-implemented.png', fullPage: true });
    await report.getByRole('button', { name: 'Prepare mail draft', exact: true }).click();
    await expect(report.getByRole('heading', { name: 'Draft prepared', exact: true })).toBeVisible();
    await expect(report.getByText(/Nothing has been sent/)).toBeVisible();
    const mail = await report.getByRole('link', { name: 'Open mail draft', exact: true }).getAttribute('href');
    expect(new URL(mail!).searchParams.get('body')).toBe(reviewed);
    const downloading = page.waitForEvent('download');
    await report.getByRole('button', { name: 'Download ZIP', exact: true }).click();
    const download = await downloading;
    const zip = join(cwd, 'report.zip'); await download.saveAs(zip);
    const extracted = JSON.parse(execFileSync('python3', ['-c', 'import zipfile,json,sys; from email import policy; from email.parser import BytesParser; z=zipfile.ZipFile(sys.argv[1]); d=BytesParser(policy=policy.default).parsebytes(z.read("draft.eml")); print(json.dumps({"body":z.read("report.md").decode(),"draft":d.get_payload(decode=True).decode(),"attachments":[[n,len(z.read(n)),all(b==255 for b in z.read(n))] for n in z.namelist() if n.startswith("attachments/")]}))', zip], { encoding: 'utf8' }));
    expect(extracted.body).toBe(reviewed); expect(extracted.draft).toBe(reviewed);
    expect(extracted.attachments).toEqual([1, 2, 3].map(n => [`attachments/${n}-attachment-${n}.bin`, 5 * 1024 * 1024, true]));
    await page.screenshot({ path: '/tmp/duckterm-bug-report-prepared.png' });
    await report.getByRole('button', { name: 'Edit report', exact: true }).click();
    await page.emulateMedia({ colorScheme: 'light' });
    await page.screenshot({ path: '/tmp/duckterm-bug-report-light.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: '/tmp/duckterm-bug-report-mobile.png', fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    rmSync(cwd, { recursive: true, force: true });
  }
});
