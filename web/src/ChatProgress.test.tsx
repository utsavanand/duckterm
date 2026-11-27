import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { AgentReplyProgress, ChatProgress, useChatProgress, type ChatTiming } from "./ChatProgress";
vi.mock("./api", () => ({ api: { chatProgress: vi.fn() } }));
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });
function Fixture({ send }: { send: (id: string) => Promise<unknown> }) {
  const progress = useChatProgress();
  return <><button onClick={() => void progress.run(send).catch(() => undefined)}>Ask</button><ChatProgress value={progress.value} startedAt={progress.startedAt} /></>;
}
it("counts actual elapsed time, follows server stages, retains final receipt, and stops polling", async () => {
  let finish!: (value: unknown) => void;
  const send = vi.fn(() => new Promise(r => { finish = r; }));
  vi.mocked(api.chatProgress).mockResolvedValue({ state: "running", elapsed_ms: 500, stages: [{ stage: "memories", started_ms: 0 }] });
  render(<Fixture send={send} />);
  fireEvent.click(screen.getByText("Ask"));
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("0s");
  await act(async () => { await vi.advanceTimersByTimeAsync(2200); });
  expect(screen.getByRole("status")).toHaveTextContent("Reading saved memories");
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("2s");
  const receipt: ChatTiming = { state: "finished", elapsed_ms: 2200, stages: [{ stage: "memories", started_ms: 0 }, { stage: "answer", started_ms: 1000 }] };
  await act(async () => { finish({ chat_progress: receipt }); });
  expect(screen.getByRole("status")).toHaveTextContent("Answered");
  expect(screen.getByText(/Preparing your answer/)).toBeInTheDocument();
  const calls = vi.mocked(api.chatProgress).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(api.chatProgress).toHaveBeenCalledTimes(calls);
  expect(send).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("2s");
});
it("a failed poll never resends a question or invents a stage; failure stops the timer", async () => {
  let fail!: (error: Error) => void;
  const send = vi.fn(() => new Promise((_r, reject) => { fail = reject; }));
  vi.mocked(api.chatProgress).mockRejectedValue(new Error("unavailable"));
  render(<Fixture send={send} />); fireEvent.click(screen.getByText("Ask"));
  await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
  expect(screen.getByRole("status")).toHaveTextContent("Starting");
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => { fail(new Error("model failed")); });
  expect(screen.getByRole("status")).toHaveTextContent("Stopped");
  expect(screen.queryByText("Answered")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("3s");
});
it("unmount stops polling, and saved durations render without a live timer", async () => {
  vi.mocked(api.chatProgress).mockResolvedValue({state:"running",elapsed_ms:0,stages:[]});
  const view = render(<Fixture send={() => new Promise(() => {})} />);fireEvent.click(screen.getByText("Ask"));view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(api.chatProgress).not.toHaveBeenCalled();
  render(<ChatProgress value={{state:"finished",elapsed_ms:12300,stages:[{stage:"answer",started_ms:0}]}} />);
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("12s");
});
it("sending an inbox message is not answering it", async () => {
  vi.setSystemTime(10000);
  const view=render(<AgentReplyProgress at={5000} status="accepted" name="main-qa" />);
  expect(screen.getByRole("status")).toHaveTextContent("Waiting for main-qa to reply");
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("5s");
  view.rerender(<AgentReplyProgress at={5000} status="answered" answeredAt={12000} name="main-qa" />);
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("7s");
});
