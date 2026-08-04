import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// The top chat bar: one question about the running fleet -> one answer from
// the summarizer backend (the fake LLM here, which always prints its canned
// rules — asserting them proves the round trip through /fleet/ask).
test("fleet chat bar answers a question about running sessions", async ({
  page,
}) => {
  const r = await apiPost("/sessions/launch", {
    command: "sh -c 'echo FLEETREADY; exec cat'",
    cwd: "/tmp",
    name: "fleetbot",
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);

  await page.goto(base());
  const input = page.locator(".rd-fleetchat-bar input");
  await input.fill("what is fleetbot doing?");
  await input.press("Enter");

  await expect(page.locator(".rd-fleetchat-q").last()).toContainText(
    "what is fleetbot doing?",
  );
  await expect(page.locator(".rd-fleetchat-a").last()).toContainText(
    "Use rg, not grep",
    { timeout: 15_000 },
  );
});
