import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { OracleChat } from "./OracleChat";
vi.mock("./api", () => ({
  api: { oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn() },
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("shows the saved conversation with answers rendered as markdown", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({
    messages: [{ q: "who's stuck?", a: "**architect** is idle\n\n- 4 messages", at: 1 }],
  });
  render(<OracleChat onClose={() => {}} />);
  const bold = await screen.findByText("architect");
  expect(bold.tagName).toBe("STRONG");
  expect(screen.getByText("4 messages").tagName).toBe("LI");
  expect(screen.queryByText(/\*\*architect\*\*/)).toBeNull();
});

it("sends on Enter, not Shift+Enter, and appends the stored exchange", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  let finish!: (v: { answer: string; exchange: { q: string; a: string; at: number }; sessions: string[] }) => void;
  vi.mocked(api.fleetAsk).mockReturnValue(new Promise((r) => { finish = r; }));
  render(<OracleChat onClose={() => {}} />);
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
  render(<OracleChat onClose={() => {}} />);
  const box = screen.getByLabelText("Message Oracle");
  fireEvent.change(box, { target: { value: "status?" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send" })); });
  expect(screen.getByRole("alert")).toHaveTextContent("no summarizer backend");
  expect(box).toHaveValue("status?");
});

it("offers example questions on an empty chat and sends one when clicked", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.fleetAsk).mockReturnValue(new Promise(() => {}));
  render(<OracleChat onClose={() => {}} />);
  const example = await screen.findByRole("button", { name: "Which sessions are waiting on me?" });
  fireEvent.click(example);
  expect(api.fleetAsk).toHaveBeenCalledWith("Which sessions are waiting on me?");
  expect(screen.queryByRole("button", { name: "Which sessions are waiting on me?" })).toBeNull();
});

it("hides the example questions once a conversation exists", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [{ q: "hi", a: "hello", at: 1 }] });
  render(<OracleChat onClose={() => {}} />);
  await screen.findByText("hello");
  expect(screen.queryByRole("button", { name: /waiting on me/ })).toBeNull();
});
