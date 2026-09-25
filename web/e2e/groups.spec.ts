import { expect, test } from "@playwright/test";
import { apiDelete, expandFolder, postEvent, seedSession } from "./helpers";

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

  // Folders start collapsed; expanding reveals the nested session.
  const header = page.locator(".rd-group-head", { hasText: "Billing" });
  await expect(header).toBeVisible();
  await expect(header.locator(".rd-group-caret")).toHaveText("▸");
  await expandFolder(page, "Billing");
  const groupBody = page.locator(".rd-group", { hasText: "Billing" });
  await expect(groupBody.locator(".rd-row", { hasText: key })).toBeVisible();

  // Collapsing the header hides the rows (click the name area — the header's
  // geometric center may land on one of its action buttons).
  await header.locator(".rd-group-caret").click();
  await expect(
    page.locator(".rd-group-body .rd-row", { hasText: key }),
  ).toHaveCount(0);
});

// The "New folder" button creates an empty folder that persists and shows in the
// left panel before any session is moved into it.
test("New folder button creates an empty folder", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
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
  await expandFolder(page, "Parent/Child");
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
  await expandFolder(page, from);
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
  await expandFolder(page, to);
  const body = page.locator(".rd-group", { hasText: to });
  await expect(body.locator(".rd-row", { hasText: key })).toBeVisible();
});

// Ungroup via the row's explicit button (drag-to-UNGROUPED also works, but a
// visible control is the discoverable path).
test("Ungroup button moves a session out of its folder", async ({ page }) => {
  const key = `e2e-ung-${Date.now()}`;
  const folder = `Grp-${Date.now()}`;
  await seedSession(key, { name: key, group: folder });

  await page.goto("/");
  await expandFolder(page, folder);
  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByRole("button", { name: "Ungroup" }).click();

  // The row now renders in the root drop zone, not inside any folder body.
  await expect(
    page.locator(".rd-group-body .rd-row", { hasText: key }),
  ).toHaveCount(0);
  await expect(page.locator(".rd-row", { hasText: key })).toBeVisible();
});

// Un-nest a folder to the top level via its header button.
test("nested folder moves to top level via the unnest button", async ({
  page,
}) => {
  const key = `e2e-unn-${Date.now()}`;
  const parent = `Top-${Date.now()}`;
  await seedSession(key, { name: key, group: `${parent}/Inner` });

  await page.goto("/");
  await expandFolder(page, parent);
  const inner = page.locator(".rd-group-head", { hasText: "Inner" });
  await expect(inner).toBeVisible();
  await inner.hover();
  await inner.locator(".rd-group-unnest").click();

  // Inner is now a top-level folder (base padding, no parent prefix).
  await expect
    .poll(() =>
      page
        .locator(".rd-group-head", { hasText: "Inner" })
        .evaluate((el) => parseInt(getComputedStyle(el).paddingLeft)),
    )
    .toBe(14);
  // Its session followed the move.
  await expandFolder(page, "Inner");
  const body = page.locator(".rd-group", { hasText: "Inner" });
  await expect(body.locator(".rd-row", { hasText: key })).toBeVisible();
});

// A terminated session's run is over: workflow actions (Rename, Notes,
// Checkpoint, Fork) must not render — only Resume/Archive/Delete apply, and
// a watched one says "Delete", never "Stop watching" (nothing is running).
test("terminated session rows show only end-state actions", async ({
  page,
}) => {
  const key = `e2e-ended-${Date.now()}`;
  await seedSession(key, { name: key });
  await postEvent({ event_type: "SessionEnd", session_key: key });

  await page.goto("/");
  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();
  await row.hover();
  await expect(row.getByRole("button", { name: "Delete" })).toBeVisible();
  for (const gone of ["Rename", "Notes", "Checkpoint", "Fork", "Stop watching"]) {
    await expect(row.getByRole("button", { name: gone })).toHaveCount(0);
  }
});

// A reload models the new dashboard mount after a native app restart.
test("restart collapses parent and nested folders without losing sessions", async ({ page }) => {
  const key = `e2e-restart-folder-${Date.now()}`;
  const parent = `Restart-${Date.now()}`;
  await seedSession(key, { name: key, group: `${parent}/Child` });
  try {
    await page.goto("/");
    const parentHead = page.locator(".rd-group-head", { hasText: parent });
    const row = page.locator(".rd-row", { hasText: key });
    await expect(parentHead.locator(".rd-group-caret")).toHaveText("▸");
    await expect(row).toHaveCount(0);
    await expandFolder(page, `${parent}/Child`);
    await expect(row).toBeVisible();
    await page.reload();
    await expect(parentHead.locator(".rd-group-caret")).toHaveText("▸");
    await expect(row).toHaveCount(0);
    await expandFolder(page, parent);
    const childHead = page.locator(".rd-group-head", { hasText: "Child" }).filter({ has: page.getByRole("button", { name: `View interactions in ${parent}/Child`, exact: true }) });
    await expect(childHead.locator(".rd-group-caret")).toHaveText("▸");
    await expect(row).toHaveCount(0);
    await expandFolder(page, `${parent}/Child`);
    await expect(row).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(parent)}`);
  }
});
