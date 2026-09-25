import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Ask Oracle: one question about the running fleet -> one answer from the
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
  await page.getByRole("button", { name: "Ask Oracle" }).click();
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

  // The conversation lives on the server and the panel remembers it was open.
  await page.reload();
  await expect(panel.locator(".rd-oracle-a").last()).toContainText("Use rg, not grep");
  await panel.getByRole("button", { name: "Close Oracle" }).click();
  await expect(panel).toBeHidden();
});
