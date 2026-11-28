import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, OracleRouted } from "./api";
import { OracleChat } from "./OracleChat";
import { edition, FULL } from "./test/editions";
vi.mock("./api", () => ({
  api: {
    oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), messageSession: vi.fn(),
    relayAnswer: vi.fn(), checkpoint: vi.fn(), stop: vi.fn(), restart: vi.fn(), restartOptions: vi.fn(),
  },
}));
vi.mock("./review/ApproveDialog", () => ({
  ApproveDialog: ({ prNumber, ratedHead }: { prNumber: number; ratedHead?: string }) => <div role="dialog">Approve #{prNumber} at {ratedHead}</div>,
}));
const NO_RELAY = { notes: [], rules: [], open: 0 };
const QA = { id: "qa2", name: "main-qa-2", folder: "Duckterm/core" };
beforeEach(() => vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const routed = (r: OracleRouted) => vi.mocked(api.fleetAsk).mockResolvedValue(r as never);
function ask(text: string, onRelayChange = () => {}) {
  render(<OracleChat relay={NO_RELAY} onRelayChange={onRelayChange} />, { wrapper: edition([...FULL, "oracle_messages", "ask_intents"]) });
  const box = screen.getByLabelText("Message Oracle");
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

it("shows what the agent is doing before Stop, and stops only on the click", async () => {
  routed({ action: { kind: "stop", agent: QA, doing: "busy · running tests · for 4 min" } });
  vi.mocked(api.stop).mockResolvedValue({ stopped: true });
  ask("stop the QA run");
  const card = await screen.findByRole("group", { name: "Stop confirmation" });
  expect(card).toHaveTextContent("Stop main-qa-2 · Duckterm/core");
  expect(card).toHaveTextContent("Now: busy · running tests · for 4 min");
  expect(card).toHaveTextContent("cuts off a turn in progress");
  expect(api.stop).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Stop" })); });
  expect(api.stop).toHaveBeenCalledWith("qa2");
  expect(screen.getByText(/Stopped main-qa-2\. Its worktree, branch and conversation are kept/)).toBeVisible();
});

it("restarts with the agent's current harness and model, read at click time", async () => {
  routed({ action: { kind: "restart", agent: QA, doing: "idle" } });
  vi.mocked(api.restartOptions).mockResolvedValue({ current: { harness: "codex", model: "gpt-6" }, resume_restart: { available: true }, harnesses: [] });
  vi.mocked(api.restart).mockResolvedValue({} as never);
  ask("restart main-qa-2");
  const card = await screen.findByRole("group", { name: "Restart confirmation" });
  expect(card).toHaveTextContent("Restarts with the same harness and model.");
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Restart" })); });
  expect(api.restart).toHaveBeenCalledWith("qa2", "gpt-6", "codex");
  expect(screen.getByText("Restarting main-qa-2 with codex · gpt-6.")).toBeVisible();
});

it("answers the agent's open question with the owner's words", async () => {
  routed({ action: { kind: "answer", agent: QA, doing: "waiting", note: { id: "n1", question: "Run QA on #526?" }, text: "yes, ship it" } });
  vi.mocked(api.relayAnswer).mockResolvedValue({ note: {} as never });
  const onRelayChange = vi.fn();
  ask("tell main-qa-2 yes, ship it", onRelayChange);
  const card = await screen.findByRole("group", { name: "Answer confirmation" });
  expect(card).toHaveTextContent("Run QA on #526?");
  expect(card).toHaveTextContent("yes, ship it");
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Send answer" })); });
  expect(api.relayAnswer).toHaveBeenCalledWith("n1", "yes, ship it");
  expect(onRelayChange).toHaveBeenCalled();
});

it("checkpoints on the click and keeps the card when it fails", async () => {
  routed({ action: { kind: "checkpoint", agent: QA, doing: "idle" } });
  vi.mocked(api.checkpoint).mockRejectedValueOnce(new Error("Checkpoint already running"));
  ask("checkpoint main-qa-2 now");
  const card = await screen.findByRole("group", { name: "Checkpoint confirmation" });
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Checkpoint" })); });
  expect(api.checkpoint).toHaveBeenCalledWith("qa2", "From Oracle chat");
  expect(within(card).getByRole("alert")).toHaveTextContent("Checkpoint already running");
  fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
  expect(screen.getByText("Cancelled. Nothing was done.")).toBeVisible();
});

it("approve only opens the Approve screen, pinned to the head shown", async () => {
  routed({ action: { kind: "approve", pr: { repo: "/r", number: 532, title: "Mono lab workspace", label: "Ready", head_sha: "abc123" } } });
  ask("merge #532");
  const card = await screen.findByRole("group", { name: "Approve confirmation" });
  expect(card).toHaveTextContent("Approve #532 · Ready");
  expect(card).toHaveTextContent("The merge happens only after Touch ID.");
  fireEvent.click(within(card).getByRole("button", { name: "Open Approve" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("Approve #532 at abc123");
});

it("shows what the records say before asking an agent, and still lets the owner ask", async () => {
  routed({ plan: { ...QA, text: "Is the fleet chat fix done?", records: "Yes: **#536** merged on Nov 27." } });
  vi.mocked(api.messageSession).mockResolvedValue({ delivered: "inbox", exchange: { q: "q", a: "Sent", at: 1 } } as never);
  ask("ask main-qa-2 if the fleet chat fix is done");
  const card = await screen.findByRole("group", { name: "Message to main-qa-2" });
  expect(within(card).getByText("#536").tagName).toBe("STRONG");
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Ask anyway" })); });
  expect(api.messageSession).toHaveBeenCalledWith("qa2", "Is the fleet chat fix done?", "inbox", expect.any(String), expect.any(String));
});
