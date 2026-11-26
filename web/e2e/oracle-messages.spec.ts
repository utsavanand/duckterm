import { expect, test } from "@playwright/test";
import { apiPost, base, postEvent, seedSession } from "./helpers";

// Ask Oracle telling one agent something, through the real server and inbox.
test("Ask Oracle sends to one agent after the owner confirms, and asks when unsure", async ({ page }) => {
  const folder = `OracleMsg-${Date.now()}`;
  const credentials: Record<string, string> = {};
  for (const name of ["release-dev", "main-dev", "main-dev-2"]) {
    const key = await seedSession(`${folder}-${name}`, { name, group: folder });
    await postEvent({ event_type: "Stop", session_key: key });
    credentials[name] = String((await apiPost(`/sessions/${key}/collaboration`, { root: folder })).body.token);
  }
  const inbox = async (name: string) =>
    ((await (await fetch(`${base()}/api/v1/session/inbox`, { headers: { Authorization: `Bearer ${credentials[name]}` } })).json()) as { messages: { question: string }[] }).messages.map(m => m.question);

  await page.goto(base());
  await page.locator(".rd-topbar").getByRole("button", { name: /^Oracle/ }).click();
  const box = page.getByLabel("Message Oracle");

  // The model chose the recipient, so nothing goes out before the owner's click.
  await box.fill("Tell release-dev to hold #470 until QA passes");
  await box.press("Enter");
  const card = page.getByRole("group", { name: "Message to release-dev" });
  await expect(card).toContainText("hold #470 until QA passes");
  expect(await inbox("release-dev")).toEqual([]);
  await card.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Sent to release-dev:", { exact: false })).toBeVisible();
  expect(await inbox("release-dev")).toEqual(["hold #470 until QA passes"]);

  await box.fill("tell main-dev to rebase on main");
  await box.press("Enter");
  const which = page.getByRole("group", { name: "Which agent did you mean?" });
  await which.getByRole("button", { name: /main-dev-2/ }).click();
  await expect(page.getByText("Sent to main-dev-2:", { exact: false })).toBeVisible();
  expect(await inbox("main-dev-2")).toContain("rebase on main");
  expect(await inbox("main-dev")).toEqual([]);
});
