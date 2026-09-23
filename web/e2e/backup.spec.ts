import { expect, test } from "@playwright/test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("manual backup remembers destination, reports a real local archive, and recovers from failure", async ({ page }) => {
  const directory = mkdtempSync(join(tmpdir(), "rd-backup-ui-"));
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "Back up to remote", exact: true }).click();
    const destination = page.getByLabel("Backup destination", { exact: true });
    await expect(destination).toBeEnabled();
    await destination.fill(directory);
    await page.screenshot({ path: "/tmp/backup-ui-real.png" });
    await page.getByRole("button", { name: "Back up now", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("Backup complete", { timeout: 15000 });
    const saved = await page.getByText(/^Saved to /).textContent();
    const archive = saved!.slice("Saved to ".length);
    expect(archive.startsWith(directory + "/")).toBeTruthy();
    const original = readFileSync(archive);
    expect(original.length).toBeGreaterThan(100);
    await page.screenshot({ path: "/tmp/backup-ui-complete.png" });
    await page.reload();
    await page.getByRole("button", { name: "Back up to remote", exact: true }).click();
    await expect(destination).toHaveValue(directory);
    await expect(page.getByRole("status")).toHaveText("Backup complete");
    // Real backend refuses overwriting an existing archive and retains the form.
    await destination.fill(archive);
    await page.getByRole("button", { name: "Back up now", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("Backup failed", { timeout: 15000 });
    await expect(page.getByRole("alert")).toContainText("Refusing to overwrite");
    await expect(page.getByRole("button", { name: "Back up now", exact: true })).toBeEnabled();
    expect(readFileSync(archive)).toEqual(original);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
