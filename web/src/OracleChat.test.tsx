import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, RelayNote, RelayRule } from "./api";
import { OracleChat } from "./OracleChat";
vi.mock("./api", () => ({
  api: { oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), relayAnswer: vi.fn(), proposeRule: vi.fn(), createRule: vi.fn(), deleteRule: vi.fn() },
}));
const NO_RELAY = { notes: [], rules: [], open: 0 };
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("shows the saved conversation with answers rendered as markdown", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({
    messages: [{ q: "who's stuck?", a: "**architect** is idle\n\n- 4 messages", at: 1 }],
  });
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} onClose={() => {}} />);
  const bold = await screen.findByText("architect");
  expect(bold.tagName).toBe("STRONG");
  expect(screen.getByText("4 messages").tagName).toBe("LI");
  expect(screen.queryByText(/\*\*architect\*\*/)).toBeNull();
});

it("sends on Enter, not Shift+Enter, and appends the stored exchange", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  let finish!: (v: { answer: string; exchange: { q: string; a: string; at: number }; sessions: string[] }) => void;
  vi.mocked(api.fleetAsk).mockReturnValue(new Promise((r) => { finish = r; }));
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} onClose={() => {}} />);
  const box = screen.getByLabelText("Message Oracle");
  fireEvent.change(box, { target: { value: "status?" } });
  fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
  expect(api.fleetAsk).not.toHaveBeenCalled();
  fireEvent.keyDown(box, { key: "Enter" });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(api.fleetAsk).toHaveBeenCalledTimes(1);
  expect(api.fleetAsk).toHaveBeenCalledWith("status?");
  expect(box).toHaveValue("");
  expect(screen.getByRole("status")).toHaveTextContent("Reading your sessions");
  await act(async () => {
    finish({ answer: "all _quiet_", exchange: { q: "status?", a: "all _quiet_", at: 2 }, sessions: [] });
  });
  expect(screen.getByText("quiet").tagName).toBe("EM");
  expect(screen.queryByRole("status")).toBeNull();
});

it("keeps a failed question in the box so it can be resent", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.fleetAsk).mockRejectedValue(new Error("no summarizer backend"));
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} onClose={() => {}} />);
  const box = screen.getByLabelText("Message Oracle");
  fireEvent.change(box, { target: { value: "status?" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send" })); });
  expect(screen.getByRole("alert")).toHaveTextContent("no summarizer backend");
  expect(box).toHaveValue("status?");
});

it("offers example questions on an empty chat and sends one when clicked", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.fleetAsk).mockReturnValue(new Promise(() => {}));
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} onClose={() => {}} />);
  const example = await screen.findByRole("button", { name: "Which sessions are waiting on me?" });
  fireEvent.click(example);
  expect(api.fleetAsk).toHaveBeenCalledWith("Which sessions are waiting on me?");
  expect(screen.queryByRole("button", { name: "Which sessions are waiting on me?" })).toBeNull();
});

it("hides the example questions once a conversation exists", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [{ q: "hi", a: "hello", at: 1 }] });
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} onClose={() => {}} />);
  await screen.findByText("hello");
  expect(screen.queryByRole("button", { name: /waiting on me/ })).toBeNull();
});

const question: RelayNote = { id: "n1", session_key: "pm", name: "product-manager", folder: "Entourage", runtime: "claude-code", kind: "question", status: "open", created_at: 1, question: "Want me to spec that fix?" };

it("shows an open question note and relays a typed reply", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.relayAnswer).mockResolvedValue({ note: { ...question, status: "answered" } });
  const onRelayChange = vi.fn();
  render(<OracleChat relay={{ notes: [question], rules: [], open: 1 }} onRelayChange={onRelayChange} />);
  expect(screen.getByText("1 needs you")).toBeVisible();
  expect(screen.getByText("Want me to spec that fix?")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Reply to product-manager"), { target: { value: "Yes, spec it" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Reply" })); });
  expect(api.relayAnswer).toHaveBeenCalledWith("n1", "Yes, spec it");
  expect(onRelayChange).toHaveBeenCalled();
});

it("says how a closed note was delivered", () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  const inbox: RelayNote = { ...question, status: "answered", answer: "Yes", answered_by: "owner", route: "inbox", route_reason: "Its prompt isn't empty, so there may be a draft. Send it to the inbox instead." };
  render(<OracleChat relay={{ notes: [inbox], rules: [], open: 0 }} onRelayChange={() => {}} />);
  expect(screen.getByText(/You replied "Yes"\. It couldn't be typed right now \(its prompt isn't empty, so there may be a draft\), so it's in its inbox/)).toBeVisible();
  expect(screen.queryByLabelText("Reply to product-manager")).toBeNull();
});

it("pre-fills a reply drafted by an answer rule and shows how close it is to acting alone", () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  const drafted: RelayNote = { ...question, suggestion: { rule_id: "R2", reply: "Yes, continue." } };
  const rules: RelayRule[] = [{ id: "R2", kind: "answer", keywords: ["keep going"], reply: "Yes, continue.", mode: "draft", streak: 3 }];
  render(<OracleChat relay={{ notes: [drafted], rules, open: 1 }} onRelayChange={() => {}} />);
  expect(screen.getByLabelText("Reply to product-manager")).toHaveValue("Yes, continue.");
  expect(screen.getByText(/Drafted by R2\. Send it unchanged 7 more times/)).toBeVisible();
});

it("answers approvals and multiple-choice notes with their buttons", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.relayAnswer).mockResolvedValue({ note: question });
  const approval: RelayNote = { ...question, id: "a1", kind: "approval", question: undefined, tool: "Bash", detail: "pytest -q" };
  const choice: RelayNote = { ...question, id: "c1", kind: "choice", question: "Pick one color:", options: ["Red", "Green"] };
  render(<OracleChat relay={{ notes: [approval, choice], rules: [], open: 2 }} onRelayChange={() => {}} />);
  expect(screen.getByText("pytest -q")).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Approve" })); });
  expect(api.relayAnswer).toHaveBeenCalledWith("a1", "approve");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "2. Green" })); });
  expect(api.relayAnswer).toHaveBeenCalledWith("c1", 1);
});

it("turns an always sentence into a proposed rule, creates it on confirm, and stops it by id", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  const rule: RelayRule = { kind: "approval", summary: "Approve pytest in Duckterm.", tool: "Bash", command_pattern: "^pytest\\b", folder: "Duckterm", action: "approve" };
  vi.mocked(api.proposeRule).mockResolvedValue({ rule });
  vi.mocked(api.createRule).mockResolvedValue({ rule: { ...rule, id: "R1" } });
  vi.mocked(api.deleteRule).mockResolvedValue(undefined);
  render(<OracleChat relay={NO_RELAY} onRelayChange={() => {}} />);
  const box = screen.getByLabelText("Message Oracle");
  fireEvent.change(box, { target: { value: "Always approve pytest in Duckterm" } });
  await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
  expect(api.proposeRule).toHaveBeenCalledWith("Always approve pytest in Duckterm");
  expect(api.fleetAsk).not.toHaveBeenCalled();
  expect(screen.getByText("Duckterm folder")).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Create rule" })); });
  expect(api.createRule).toHaveBeenCalledWith(rule);
  expect(screen.getByText(/Created R1: Approve pytest in Duckterm\./)).toBeVisible();
  fireEvent.change(box, { target: { value: "stop r1" } });
  await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
  expect(api.deleteRule).toHaveBeenCalledWith("R1");
  expect(screen.getByText("Stopped R1.")).toBeVisible();
});
