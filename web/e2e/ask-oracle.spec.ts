import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Ask Oracle: one question about the running fleet -> one answer from the
// summarizer backend (the fake LLM here, which always prints its canned
// rules — asserting them proves the round trip through /fleet/ask). The
// answer must survive closing and reopening the modal.
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
  const input = page.getByLabel("Question");
  await input.fill("what is fleetbot doing?");
  await input.press("Enter");

  await expect(page.locator(".rd-oracle-q").last()).toContainText(
    "what is fleetbot doing?",
  );
  await expect(page.locator(".rd-oracle-a").last()).toContainText(
    "Use rg, not grep",
    { timeout: 15_000 },
  );
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Ask Oracle" }).click();
  await expect(page.locator(".rd-oracle-a").last()).toContainText("Use rg, not grep");
});
