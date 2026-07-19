import { expect, test } from "@playwright/test";
import { apiPost, findSession, seedSession } from "./helpers";

// Archive a session from the UI: the row leaves the agents list (the list hides
// archived sessions) and the backend marks it archived with history kept.
// Unarchive (via the API — the redesigned list has no archived view yet) brings
// it back as a stopped, resumable row.
test("archive hides the session; unarchive returns it as stopped", async ({
  page,
}) => {
  // Archive is launched-only — a watched session can't be archived.
  const key = `e2e-archive-${Date.now()}`;
  await seedSession(key, { name: key, launched: true });

  await page.goto("/");

  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();

  // Archive it.
  await row.hover();
  await row.getByRole("button", { name: "Archive" }).click();

  // It leaves the agents list...
  await expect(page.locator(".rd-row", { hasText: key })).toHaveCount(0);
  // ...and the backend marks it archived (history kept).
  await expect
    .poll(async () => (await findSession((s) => s.session_key === key))?.state)
    .toBe("archived");

  // Unarchive brings it back as a stopped (resumable) row in the list.
  const res = await apiPost(`/sessions/${key}/unarchive`);
  expect(res.status).toBe(200);
  await expect
    .poll(async () => (await findSession((s) => s.session_key === key))?.state)
    .toBe("stopped");
  await page.reload();
  const returned = page.locator(".rd-row", { hasText: key });
  await expect(returned).toBeVisible();
  // A stopped launched session offers Resume.
  await returned.hover();
  await expect(returned.getByRole("button", { name: "Resume" })).toBeVisible();
});
