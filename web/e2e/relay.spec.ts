import { expect, test } from "@playwright/test";
import { apiPost, base, seedSession } from "./helpers";

// Oracle Relay, through the real server: a blocking permission request shows
// up as a note in the control tower's chat, and Approve there reaches the
// polling hook exactly like the dashboard's approval path.
test("a permission request becomes an Oracle note and is approved from the chat", async ({ page }) => {
  const key = `e2e-relay-${Date.now()}`;
  await seedSession(key, { name: "relay-bot", group: "RelayTeam" });
  const reg = await apiPost("/approvals", {
    session_key: key,
    tool_name: "Bash",
    tool_input: { command: "pytest -q tests/relay" },
  });
  const id = reg.body.id as string;

  await page.goto(base());
  await expect(page.getByRole("button", { name: /^Oracle/ })).toContainText("1");
  await page.getByRole("button", { name: /^Oracle/ }).click();
  const note = page.getByRole("group", { name: "Approval from relay-bot" });
  await expect(note).toContainText("pytest -q tests/relay");
  await expect(page.getByRole("region", { name: "Needs you" })).toContainText("relay-bot");

  await note.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(note).toContainText("You approved. Relayed through the approval request.");

  const res = await fetch(`${base()}/approvals/${id}/decision`);
  expect(((await res.json()) as { status: string }).status).toBe("approve");
});
