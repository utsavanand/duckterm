import { execFileSync } from "node:child_process";
import { readOwnedState } from "./run-state";
import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession, sessionMenu } from "./helpers";

test("session sections preserve meaningful state, notes and checkpoint review without background writes", async ({ page }) => {
  const key = await seedSession(`panel-${Date.now()}`, { name: "Panel review", runtime: "generic", test: true });
  const savedAt = Date.now() - 2 * 86400000;
  let writes = 0;
  page.on("request", r => { if (r.method() === "POST" && /\/(checkpoint|input)$/.test(new URL(r.url()).pathname)) writes++; });
  try {
    await page.route(`**/sessions/${key}/checkpoints`, route => route.fulfill({ json: {
      context_evidence: { state: "available", reason: null, context_window_tokens: 258400, last_compaction_at: "2026-10-19T10:00:00Z", history_scope: "recent" },
      checkpoint_activity: { state: "known", checkpoint_id: "saved", events_since_capture: 1026, event_filter: "work-v1", reason: null },
      checkpoint_clock: { state: "known", succeeded_at: savedAt, checkpoint_id: "saved" },
      checkpoints: [{ id: "failed", label: "manual", created_at: Date.now(), summary_update: { state: "failed", reason: "summary_unverified" }, record: { prompts: [], files: [], tools: [], event_count: 9000 } }],
    } }));
    const { home } = readOwnedState();
    execFileSync("python", ["-c", `
import sys
from pathlib import Path
from duckterm.persistence.history import HistoryStore
from duckterm.persistence.digests import DigestStore
from duckterm.persistence.saved_state import transaction
home,key,now=sys.argv[1:]
h=HistoryStore(Path(home)/'db.sqlite')
assert h.session(key)['test']
d=DigestStore(conn=h._conn)
with transaction(h._conn):
 for i in range(8):
  d.add_revision(key,dict(summary='Saved test decision '+str(i),source={},conversation={},summary_validation={}),int(now)-i*3600000)
h.close()
`, home, key, String(savedAt)], { env: { ...process.env, DUCKTERM_HOME: home } });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Panel review" }).click();
    const panel = page.locator("#context-view-session");
    const saved = panel.locator(".rd-session-section").filter({ has: page.locator("summary > span", { hasText: /^Saved memory$/ }) });
    const context = panel.locator(".rd-session-section").filter({ has: page.locator("summary > span", { hasText: /^Active context$/ }) });
    await expect(saved).toHaveAttribute("open", "");
    await expect(saved.getByRole("columnheader", { name: "Date" })).toBeVisible();
    await expect(saved.getByRole("button", { name: "View memory ↗" })).toHaveCount(8);
    await expect(context.locator("time")).toHaveAttribute("datetime", "2026-10-19T10:00:00Z");
    await expect(context.getByLabel("Context tokens")).toHaveText(/used · — left/);
    await expect(context.getByRole("button", { name: "Compact", exact: true })).toBeDisabled();
    await saved.locator(":scope > summary").focus(); await page.keyboard.press("Enter");
    await expect(saved).not.toHaveAttribute("open", "");
    await page.keyboard.press("Enter");
    await expect(saved.getByRole("button", { name: /Latest checkpoint/ })).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-session-panel-e2e-dark.png" });
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Notes", exact: true }).click();
    await expect(page.getByLabel("Session notes")).toBeVisible();
    await page.getByLabel("Session notes").fill("A reviewed note");
    await panel.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByLabel("Session notes")).toHaveCount(0);
    await page.route(`**/sessions/${key}/checkpoint-plan`, route => route.fulfill({ status: 503, json: { error: "Count unavailable" } }));
    await saved.getByRole("button", { name: "Retry checkpoint", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Checkpoint", exact: true });
    await expect(dialog.getByText("Couldn’t count the available history.")).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(writes).toBe(0);
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(saved.getByRole("button", { name: "Retry checkpoint", exact: true })).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await page.screenshot({ path: "/tmp/duckterm-session-panel-e2e-light.png" });
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally { await apiDelete(`/sessions/${key}`); }
});
