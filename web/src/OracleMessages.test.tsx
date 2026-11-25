import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, OracleRouted } from "./api";
import { OracleChat } from "./OracleChat";
import { edition, FULL } from "./test/editions";
vi.mock("./api", () => ({
  api: { oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), messageSession: vi.fn() },
}));
const NO_RELAY = { notes: [], rules: [], open: 0 };
const AGENTS = [
  { id: "rel", name: "release-dev", folder: "Duckterm/leadership" },
  { id: "md1", name: "main-dev", folder: "Duckterm/core" },
  { id: "md2", name: "main-dev-2", folder: "Duckterm/core" },
];
beforeEach(() => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.messageSession).mockImplementation(async (key, text, _mode, _rk, question) =>
    ({ delivered: "inbox", exchange: { q: question ?? "", a: `Sent to ${AGENTS.find(a => a.id === key)?.name}: ${text}`, at: Date.now() } }));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function chat(features = [...FULL, "oracle_messages"]) {
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} agents={AGENTS} />, { wrapper: edition(features) });
  return screen.getByLabelText("Message Oracle");
}
// fleetAsk's overloads make vi.mocked pick the folder signature.
const routed = (r: OracleRouted) => vi.mocked(api.fleetAsk).mockResolvedValue(r as never);
function type(box: HTMLElement, text: string) {
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

it("sends the owner's words unchanged to the agent picked with @, with no model call", async () => {
  const box = chat();
  fireEvent.click(screen.getByRole("button", { name: "Message one agent" }));
  fireEvent.change(screen.getByLabelText("Find an agent"), { target: { value: "release" } });
  fireEvent.click(screen.getByRole("option", { name: /release-dev/ }));
  type(box, "Hold #470 until QA passes");
  // Saved to the chat history as the owner's @ line and Oracle's confirmation.
  await screen.findByText("Sent to release-dev: Hold #470 until QA passes");
  expect(screen.getByText("@release-dev Hold #470 until QA passes")).toBeVisible();
  expect(screen.queryByRole("group", { name: "Message to release-dev" })).toBeNull();
  expect(api.messageSession).toHaveBeenCalledWith("rel", "Hold #470 until QA passes", "inbox", expect.any(String), "@release-dev Hold #470 until QA passes");
  expect(api.fleetAsk).not.toHaveBeenCalled();
});

it("waits for Send before delivering text the model reworded", async () => {
  routed({ plan: { ...AGENTS[0], text: "Please hold PR #470." } });
  type(chat(), "get release-dev to hold 470");
  const card = await screen.findByRole("group", { name: "Message to release-dev" });
  expect(card).toHaveTextContent("Please hold PR #470.");
  expect(api.messageSession).not.toHaveBeenCalled();
  fireEvent.click(within(card).getByRole("button", { name: "Send" }));
  await screen.findByText("Sent to release-dev: Please hold PR #470.");
  expect(api.messageSession).toHaveBeenCalledWith("rel", "Please hold PR #470.", "inbox", expect.any(String), "get release-dev to hold 470");
});

it("asks which agent when unsure, and sends the owner's own words once one is picked", async () => {
  routed({ clarify: { text: "rebase on main", candidates: AGENTS.slice(1) } });
  type(chat(), "tell main-dev to rebase on main");
  const ask = await screen.findByRole("group", { name: "Which agent did you mean?" });
  expect(ask).toHaveTextContent("Nothing was sent");
  expect(api.messageSession).not.toHaveBeenCalled();
  fireEvent.click(within(ask).getByRole("button", { name: /main-dev-2/ }));
  await waitFor(() => expect(api.messageSession).toHaveBeenCalledWith("md2", "rebase on main", "inbox", expect.any(String), "tell main-dev to rebase on main"));
  expect(within(ask).queryByRole("button")).toBeNull();
});

it("Cancel leaves a reworded message unsent", async () => {
  routed({ plan: { ...AGENTS[0], text: "Please hold PR #470." } });
  type(chat(), "get release-dev to hold 470");
  const card = await screen.findByRole("group", { name: "Message to release-dev" });
  fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
  expect(card).toHaveTextContent("Not sent to release-dev");
  expect(api.messageSession).not.toHaveBeenCalled();
});

it("without the flag there is no @ and questions go out without a request key", async () => {
  vi.mocked(api.fleetAsk).mockResolvedValue({ answer: "ok", exchange: { q: "tell x", a: "ok", at: 1 }, sessions: [] });
  type(chat([...FULL]), "tell release-dev to hold");
  await waitFor(() => expect(api.fleetAsk).toHaveBeenCalledWith("tell release-dev to hold"));
  expect(screen.queryByRole("button", { name: "Message one agent" })).toBeNull();
});
