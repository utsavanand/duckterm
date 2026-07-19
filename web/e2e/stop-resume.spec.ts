import { expect, test } from "@playwright/test";
import { apiPost, findSession } from "./helpers";

// Stop is a pause, resume continues: click Stop on a live PTY session, the row
// flips to stopped and offers Resume; click Resume and the agent relaunches
// with the SAME command it was launched with (recorded on SessionStart), so
// its banner shows up in the browser terminal again.

test("stop pauses a PTY session; resume relaunches its recorded command", async ({
  page,
}) => {
  const r = await apiPost("/sessions/launch", {
    command: "sh -c 'echo READY_CAT; exec cat'",
    cwd: "/tmp",
    name: "stopres",
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);
  const key = r.body.session_key as string;

  await page.goto("/");
  const row = page.locator(".rd-row", { hasText: "stopres" });
  await expect(row).toBeVisible({ timeout: 10_000 });

  await row.hover();
  await row.getByRole("button", { name: "Stop", exact: true }).click();
  await expect
    .poll(async () => (await findSession((s) => s.session_key === key))?.state)
    .toBe("stopped");

  // The stopped row offers Resume; the relaunch runs `sh -c ...` again (the
  // recorded command), so the terminal reconnects and shows the banner.
  await row.hover();
  await row.getByRole("button", { name: "Resume" }).click();
  await expect
    .poll(
      async () => (await findSession((s) => s.session_key === key))?.state,
      {
        timeout: 10_000,
      },
    )
    .toBe("busy");
  await row.locator(".rd-row-name").click();
  await expect(
    page.locator(".rd-terminal-slot:visible .xterm-rows"),
  ).toContainText("READY_CAT", { timeout: 8_000 });
});
