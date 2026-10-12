import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { apiDelete, base } from "./helpers";
import { readOwnedState } from "./run-state";

const fixture = `
import sys,time
from pathlib import Path
from types import SimpleNamespace
from duckterm.core.eventbus import EventBus
from duckterm.persistence.history import HistoryStore
from duckterm.persistence import opencode_launch as journal
from duckterm.runtimes import native_history
from duckterm.server import Server
home,key,action=sys.argv[1:]
h=HistoryStore(Path(home)/'db.sqlite')
try:
 if action=='seed':
  EventBus(sink=h.record).publish(dict(event_type='SessionStart',session_key=key,runtime='opencode',name='OpenCode recovery check',cwd=home,test=True))
  state=journal.Launch('recovery-fixture','local','synthetic-store',str(Path(home).resolve()),123)
  journal.begin(h._conn,key,state)
  journal.claim_create(h._conn,key,state.generation)
  journal.bind_created(h._conn,key,state.generation,dict(id='ses_recovery',directory=state.directory))
  source=Server._message_source(SimpleNamespace(history=h),key)
  Path(source.path).parent.mkdir(parents=True,exist_ok=True)
  native_history.persist(source,[dict(id='msg_user',role='user',blocks=[dict(type='text',text='Add a healthcheck endpoint.')]),dict(id='msg_answer',role='assistant',blocks=[dict(type='text',text='The endpoint is implemented and the focused tests pass.')])],int(time.time()*1000),1)
  h.set_state(key,'unknown',now=int(time.time()*1000))
 else:
  control=h.restart_control(key)
  source=Server._message_source(SimpleNamespace(history=h),key)
  control['opencode_observation']=dict(state='idle',observed_at=int(time.time()*1000),revision=2,content_hash=native_history.read(source)['content_hash'])
  h.set_restart_control(key,control)
  h.set_state(key,'idle',now=int(time.time()*1000))
finally:
 h.close()
`;

test("OpenCode recovery retains verified Messages, labels uncertainty, and clears on recovery", async ({ page, request }) => {
  const key = `opencode-recovery-${Date.now()}`;
  const { home } = readOwnedState();
  const seed = (action: string) => execFileSync(resolve("../.venv/bin/python"), ["-c", fixture, home, key, action], {
    env: { ...process.env, DUCKTERM_HOME: home },
  });
  seed("seed");
  try {
    const payload = await (await request.get(`/sessions/${key}/messages`)).json();
    expect(payload.transcript.status).toBe("native_cached");
    expect(payload.transcript.checked_at).toBeGreaterThan(0);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.getByText("OpenCode recovery check", { exact: true }).first().click();
    await page.locator(".rd-view-toggle button", { hasText: "Messages" }).click();
    await expect(page.locator(`[data-session-key="${key}"] .rd-state`)).toHaveAttribute("aria-label", "Status unavailable");
    await expect(page.locator(".rd-native-history-notice")).toContainText("Showing the last verified conversation");
    await expect(page.getByText("The endpoint is implemented and the focused tests pass.", { exact: true })).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-opencode-recovery-desktop.png" });
    await page.evaluate(() => localStorage.setItem("rd-theme", "dark"));
    await page.reload();
    await page.getByText("OpenCode recovery check", { exact: true }).first().click();
    await page.locator(".rd-view-toggle button", { hasText: "Messages" }).click();
    await expect(page.locator(".rd-native-history-notice")).toBeVisible();
    await page.screenshot({ path: "/tmp/duckterm-opencode-recovery-dark.png" });
    await expect(page.getByLabel("Follow-up message")).toBeDisabled();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".rd-native-history-notice")).toBeVisible();
    const box = await page.locator(".rd-native-history-notice").boundingBox();
    expect(box!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "/tmp/duckterm-opencode-recovery-narrow.png" });
    seed("recover");
    await expect(page.locator(".rd-native-history-notice")).toHaveCount(0, { timeout: 10000 });
    expect((await (await request.get(`/sessions/${key}/messages`)).json()).transcript.status).toBe("native_current");
    await expect(page.getByText("The endpoint is implemented and the focused tests pass.", { exact: true })).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
