import { expect, test } from "@playwright/test";
import { seedSession } from "./helpers";

// A session with a `group` renders under a collapsible folder header in the left
// panel, and clicking the header collapses/expands it. (Drag-and-drop assignment
// is exercised manually; here we drive the render path via the API.)
test("a grouped session shows under its folder header and collapses", async ({
  page,
}) => {
  const key = `e2e-group-${Date.now()}`;
  // Move it into a folder the way a drag-drop would (PATCH group on /sessions/:key).
  await seedSession(key, { name: key, group: "Billing" });

  await page.goto("/");

  // The folder header appears, with the session nested in its body.
  const header = page.locator(".rd-group-head", { hasText: "Billing" });
  await expect(header).toBeVisible();
  const groupBody = page.locator(".rd-group", { hasText: "Billing" });
  await expect(groupBody.locator(".rd-row", { hasText: key })).toBeVisible();

  // Collapsing the header hides the rows.
  await header.click();
  await expect(
    page.locator(".rd-group-body .rd-row", { hasText: key }),
  ).toHaveCount(0);
});

// The "New folder" button creates an empty folder that persists and shows in the
// left panel before any session is moved into it.
test("New folder button creates an empty folder", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New folder" }).click();
  const name = `Folder-${Date.now()}`;
  await page.getByPlaceholder("e.g. payments").fill(name);
  await page.getByRole("button", { name: "Create folder" }).click();

  // It appears as a (empty) folder header in the left panel.
  await expect(page.locator(".rd-group-head", { hasText: name })).toBeVisible();
});

// Sessions inside a NESTED folder must render indented under it — this
// regressed when folder depth and fork depth shared one prop, leaving grouped
// rows flush-left as if they were outside their folder.
test("a session in a nested folder renders indented under it", async ({
  page,
}) => {
  const key = `e2e-nest-${Date.now()}`;
  await seedSession(key, { name: key, group: "Parent/Child" });

  await page.goto("/");
  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();
  const childHead = page.locator(".rd-group-head", { hasText: "Child" });
  await expect(childHead).toBeVisible();
  const headPad = await childHead.evaluate(
    (el) => parseInt(getComputedStyle(el).paddingLeft),
  );
  expect(headPad).toBeGreaterThan(14); // Child itself is nested under Parent
  // Poll: the row renders Ungrouped until the client refetches its group,
  // then jumps into the folder — wait for the settled indent.
  await expect
    .poll(() =>
      row.evaluate((el) => parseInt(getComputedStyle(el).paddingLeft)),
    )
    .toBeGreaterThan(headPad);
});

// Double-clicking a folder name renames it; grouped sessions follow.
test("double-click renames a folder and its session follows", async ({
  page,
}) => {
  const key = `e2e-ren-${Date.now()}`;
  const from = `Old-${Date.now()}`;
  await seedSession(key, { name: key, group: from });

  await page.goto("/");
  // Wait until the session has settled INSIDE the folder before renaming —
  // the rename handler re-groups known sessions, so it must know this one.
  await expect(
    page.locator(".rd-group", { hasText: from }).locator(".rd-row", { hasText: key }),
  ).toBeVisible();
  const to = `Ren-${Date.now()}`;
  const dialog = page
    .waitForEvent("dialog", { timeout: 5000 })
    .then((d) => d.accept(to));
  await page
    .locator(".rd-group-head", { hasText: from })
    .locator(".rd-group-name")
    .dblclick();
  await dialog;

  const renamed = page.locator(".rd-group-head", { hasText: to });
  await expect(renamed).toBeVisible();
  await expect(page.locator(".rd-group-head", { hasText: from })).toHaveCount(0);
  const body = page.locator(".rd-group", { hasText: to });
  await expect(body.locator(".rd-row", { hasText: key })).toBeVisible();
});
