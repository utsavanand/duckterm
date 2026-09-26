import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Ask Oracle, in the control tower: one question about the running fleet -> one answer from the
// summarizer backend (the fake LLM here, which always prints its canned
// rules — asserting them proves the round trip through /fleet/ask). The
// conversation must survive a page reload.
test("Ask Oracle answers a question about running sessions", async ({ page }) => {
  const r = await apiPost("/sessions/launch", {
    command: "sh -c 'echo FLEETREADY; exec cat'",
    cwd: "/tmp",
    name: "fleetbot",
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);

  await page.goto(base());
  await page.getByRole("button", { name: "Oracle", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Control tower/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^fleetbot, / })).toBeVisible();
  const panel = page.getByRole("complementary", { name: "Oracle chat" });
  const box = panel.getByLabel("Message Oracle");
  await box.fill("what is fleetbot doing?");
  await box.press("Enter");

  await expect(panel.locator(".rd-oracle-q").last()).toHaveText(
    "what is fleetbot doing?",
  );
  await expect(panel.locator(".rd-oracle-a").last()).toContainText(
    "Use rg, not grep",
    { timeout: 15_000 },
  );
  // A new answer scrolls only the chat; the tower's header stays in view.
  await expect(page.getByRole("button", { name: "← Sessions" })).toBeInViewport();

  // The conversation lives on the server, so it survives a reload.
  await page.reload();
  await page.getByRole("button", { name: "Oracle", exact: true }).click();
  await expect(panel.locator(".rd-oracle-a").last()).toContainText("Use rg, not grep");
  await page.getByRole("button", { name: "← Sessions" }).click();
  await expect(page.getByRole("heading", { name: /Control tower/ })).toBeHidden();
});
