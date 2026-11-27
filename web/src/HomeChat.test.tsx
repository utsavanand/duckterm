import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { ChatSession, HomeChat } from "./HomeChat";
import { edition, FULL } from "./test/editions";

vi.mock("./api", () => ({ authHeaders: () => ({}), api: { oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), messageSession: vi.fn() } }));
beforeEach(() => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.messageSession).mockResolvedValue({ delivered: "inbox" } as never);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

// #501 QA: two sessions named "dev" in different folders; picking the second
// sent to the first.
const SESSIONS: ChatSession[] = [
  { key: "k-dev-a", name: "dev", folder: "Alpha", state: "idle" },
  { key: "k-dev-b", name: "dev", folder: "Beta", state: "busy" },
  { key: "k-qa", name: "qa lead", folder: "Alpha", state: "idle" },
];
const Wrapper = edition([...FULL, "oracle_messages"]);

async function chat(sessions = SESSIONS) {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<Wrapper><HomeChat sessions={sessions} onOpenTerminal={() => undefined} /></Wrapper>); });
  const box = screen.getByRole("textbox", { name: "Ask DuckTerm" });
  const type = (value: string) => fireEvent.change(box, { target: { value } });
  const send = async () => { await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Ask" })); }); };
  const pick = (text: string) => fireEvent.click(within(screen.getByRole("listbox", { name: "Ask a session" })).getByRole("option", { name: text }));
  return { view, box, type, send, pick };
}

it("sends to the session that was picked, even when another has the same name", async () => {
  const { type, send, pick } = await chat();
  type("@dev");
  pick("dev Beta · busy");
  type("@dev ship it?");
  await send();
  expect(api.messageSession).toHaveBeenCalledWith("k-dev-b", "ship it?", "inbox", expect.any(String), "@dev ship it?", expect.stringMatching(/^[a-f0-9]{32}$/));
});

it("refuses a typed name that more than one session has", async () => {
  const { type, send } = await chat();
  type("@dev ship it?");
  await send();
  expect(screen.getByRole("alert")).toHaveTextContent('2 sessions are named "dev". Pick the one you mean');
  expect(api.messageSession).not.toHaveBeenCalled();
});

it("sends to a picked session whose name has a space, with the whole question", async () => {
  const { type, send, pick } = await chat();
  type("@qa");
  pick("qa lead Alpha · idle");
  type("@qa lead is #490 done?");
  await send();
  expect(api.messageSession).toHaveBeenCalledWith("k-qa", "is #490 done?", "inbox", expect.any(String), "@qa lead is #490 done?", expect.stringMatching(/^[a-f0-9]{32}$/));
});

it("sends nothing when the picked session has gone", async () => {
  const { view, type, send, pick } = await chat();
  type("@dev");
  pick("dev Beta · busy");
  type("@dev ship it?");
  view.rerender(<Wrapper><HomeChat sessions={SESSIONS.filter(s => s.key !== "k-dev-b")} onOpenTerminal={() => undefined} /></Wrapper>);
  await send();
  expect(screen.getByRole("alert")).toHaveTextContent("dev is no longer running. Nothing was sent.");
  expect(api.messageSession).not.toHaveBeenCalled();
});

it("still sends a typed name that only one session has", async () => {
  const { type, send } = await chat();
  type("@dev ship it?");
  await send();
  expect(api.messageSession).not.toHaveBeenCalled();
  type("@nobody hi");
  await send();
  expect(screen.getByRole("alert")).toHaveTextContent('No session is named "nobody"');
  cleanup();
  const one = await chat([SESSIONS[0], SESSIONS[2]]);
  one.type("@DEV ship it?");
  await one.send();
  expect(api.messageSession).toHaveBeenCalledWith("k-dev-a", "ship it?", "inbox", expect.any(String), "@DEV ship it?", expect.stringMatching(/^[a-f0-9]{32}$/));
});
