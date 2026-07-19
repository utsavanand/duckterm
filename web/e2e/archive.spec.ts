import { expect, test } from "@playwright/test";
import { apiPost, findSession, seedSession } from "./helpers";

// Archive is FINAL: the row leaves the agents list, history is kept in the
// backend, and resume is refused. (Stop is the pause; archive is the end.)
test("archive hides the session for good; resume is refused", async ({
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
  // ...the backend marks it archived with history kept...
  await expect
    .poll(async () => (await findSession((s) => s.session_key === key))?.state)
    .toBe("archived");

  // ...and it cannot be resumed.
  const res = await apiPost(`/sessions/${key}/resume`);
  expect(res.status).toBe(400);
  expect(String(res.body.error)).toContain("archive is final");
});
