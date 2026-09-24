import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { OracleExchange, OracleModal } from "./OracleModal";
vi.mock("./api", () => ({ api: { fleetAsk: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const earlier: OracleExchange[] = [
  { q: "one", a: "1" }, { q: "two", a: "2" }, { q: "three", a: "3" },
];

it("sends only the last two exchanges as follow-up context and ignores a second Enter while asking", async () => {
  let finish!: (v: { answer: string; sessions: string[] }) => void;
  vi.mocked(api.fleetAsk).mockReturnValue(new Promise((r) => { finish = r; }));
  const onLog = vi.fn();
  render(<OracleModal log={earlier} onLog={onLog} onClose={() => {}} />);
  const field = screen.getByLabelText("Question");
  fireEvent.change(field, { target: { value: "who's stuck?" } });
  fireEvent.keyDown(field, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Asking…" }));
  expect(api.fleetAsk).toHaveBeenCalledTimes(1);
  expect(api.fleetAsk).toHaveBeenCalledWith("who's stuck?", earlier.slice(1));
  await act(async () => { finish({ answer: "main-qa has stale mail", sessions: [] }); });
  expect(onLog).toHaveBeenCalledWith({ q: "who's stuck?", a: "main-qa has stale mail" });
  expect(field).toHaveValue("");
});

it("records a failed ask as an answer instead of dropping the question", async () => {
  vi.mocked(api.fleetAsk).mockRejectedValue(new Error("no summarizer backend"));
  const onLog = vi.fn();
  render(<OracleModal log={[]} onLog={onLog} onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText("Question"), { target: { value: "status?" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Ask" })); });
  expect(onLog).toHaveBeenCalledWith({ q: "status?", a: "Failed: no summarizer backend" });
});
