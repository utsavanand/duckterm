import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Messages } from "./Messages";
import { MobilePending } from "./MobilePending";
import { routedFetch, sessionRef } from "./hostTransport";

vi.mock("./hostTransport", async original => ({ ...await original<typeof import("./hostTransport")>(), routedFetch: vi.fn() }));
const timestamp = 1800000000000;
const response = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body }) as Response;
const row = (session = "a", overrides = {}) => ({ request_id: "11111111-1111-4111-8111-111111111111", session_id: session,
  status: "queued", text: "Please check the rollout", created_at: timestamp, updated_at: timestamp, expires_at: timestamp + 900000,
  wait_reason: "agent_busy", ...overrides });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
let visible = "visible";
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(timestamp); vi.mocked(routedFetch).mockReset();
  visible = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visible as DocumentVisibilityState);
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: [row()] }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it("renders pending plain text even with an empty verified transcript", async () => {
  const text = '<img src=x onerror="alert(1)"> **not markdown**';
  vi.mocked(routedFetch).mockImplementation(async path => response(path.endsWith("/mobile-messages")
    ? { mobile_messages: [row("empty-pending", { text })] } : path.endsWith("/annotations") ? { annotations: [] } : { messages: [] }));
  const view = render(<Messages sessionKey="empty-pending" />); await flush();
  expect(screen.getByText("No agent reply yet.")).toBeTruthy();
  expect(screen.getByText(text)).toBeTruthy();
  expect(view.container.querySelector(".rd-mobile-pending img")).toBeNull();
  expect(view.container.querySelector(".rd-mobile-pending strong")).toBeNull();
  expect(view.container.querySelector("[data-message-key]")).toBeNull();
});

it("keeps transcript and composer intact when the optional endpoint is an older 404", async () => {
  vi.mocked(routedFetch).mockImplementation(async path => path.endsWith("/mobile-messages") ? response({}, 404) : response(path.endsWith("/annotations")
    ? { annotations: [] } : { messages: [{ id: 1, role: "assistant", blocks: [{ type: "text", text: "Verified reply stays visible" }] }] }));
  render(<Messages sessionKey="older-host" />); await flush();
  expect(screen.getByText("Verified reply stays visible")).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Follow-up message" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Pending iPhone messages" })).toBeNull();
  expect(screen.queryByText(/Could not load messages/)).toBeNull();
});

it("marks known rows last-confirmed on failure and removes them only after confirmed delivery", async () => {
  render(<MobilePending sessionKey="a" active />); await flush();
  expect(screen.getByText("Waiting for this turn to finish")).toBeTruthy();
  vi.mocked(routedFetch).mockRejectedValue(new Error("Offline")); await advance(3000);
  expect(screen.getByText(/Status unavailable · Last confirmed at/)).toBeTruthy();
  expect(screen.queryByText("Waiting for this turn to finish")).toBeNull();
  expect(screen.getByText("Last confirmed: waiting for this turn to finish")).toBeTruthy();
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: [] })); await advance(3000);
  expect(screen.queryByText("Please check the rollout")).toBeNull();
  expect(vi.mocked(routedFetch).mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
});

it("expires retained rows locally while status reads fail", async () => {
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: [row("a", { expires_at: timestamp + 4000 })] }));
  render(<MobilePending sessionKey="a" active />); await flush();
  vi.mocked(routedFetch).mockRejectedValue(new Error("Offline")); await advance(4001);
  expect(screen.queryByText("Please check the rollout")).toBeNull();
});

it("does not poll an inactive tab or hidden document and rechecks on return", async () => {
  const view = render(<MobilePending sessionKey="a" active={false} />); await advance(9000);
  expect(routedFetch).not.toHaveBeenCalled();
  view.rerender(<MobilePending sessionKey="a" active />); await flush();
  expect(routedFetch).toHaveBeenCalledTimes(1);
  act(() => { visible = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
  await advance(9000); expect(routedFetch).toHaveBeenCalledTimes(1);
  act(() => { visible = "visible"; document.dispatchEvent(new Event("visibilitychange")); });
  await flush(); expect(routedFetch).toHaveBeenCalledTimes(2);
  view.rerender(<MobilePending sessionKey="a" active={false} />); await advance(9000);
  expect(routedFetch).toHaveBeenCalledTimes(2);
});

it("discards an old local response after switching to an equal session ID on a remote host", async () => {
  let finish!: (result: Response) => void;
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue(response({ mobile_messages: [row("a", { text: "Remote phone message" })] }));
  const view = render(<MobilePending sessionKey="a" active />); await flush();
  view.rerender(<MobilePending sessionKey={sessionRef("other-mac", "a")} active />); await flush();
  expect(screen.getByText("Remote phone message")).toBeTruthy();
  await act(async () => { finish(response({ mobile_messages: [row("a", { text: "Old local private message" })] })); });
  expect(screen.queryByText("Old local private message")).toBeNull();
  expect(vi.mocked(routedFetch).mock.calls[1][0]).toContain(encodeURIComponent(sessionRef("other-mac", "a")));
});

it("clears the old session synchronously while a new selection loads", async () => {
  const view = render(<MobilePending sessionKey="a" active />); await flush();
  vi.mocked(routedFetch).mockImplementation(() => new Promise(() => {}));
  view.rerender(<MobilePending sessionKey="b" active />);
  expect(screen.queryByText("Please check the rollout")).toBeNull();
  await advance(5001); expect(screen.queryByText(/Last confirmed/)).toBeNull();
});

it("times out a stalled status read without labelling old pending rows current", async () => {
  render(<MobilePending sessionKey="a" active />); await flush();
  vi.mocked(routedFetch).mockImplementation(() => new Promise(() => {}));
  await advance(8001);
  expect(screen.getByText(/Status unavailable · Last confirmed at/)).toBeTruthy();
  expect(screen.queryByText("Waiting for this turn to finish")).toBeNull();
});

it("ignores a response completed behind another tab and fetches current status on return", async () => {
  let finish!: (result: Response) => void;
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue(response({ mobile_messages: [] }));
  const view = render(<MobilePending sessionKey="a" active />); await flush();
  view.rerender(<MobilePending sessionKey="a" active={false} />);
  await act(async () => { finish(response({ mobile_messages: [row()] })); });
  view.rerender(<MobilePending sessionKey="a" active />); await flush();
  expect(screen.queryByText("Please check the rollout")).toBeNull();
  expect(routedFetch).toHaveBeenCalledTimes(2);
});

it.each(["too_many", "duplicate"])("refuses an invalid pending collection: %s", async condition => {
  const items = condition === "too_many" ? Array.from({ length: 6 }, (_, index) => row("a", {
    request_id: `${String(index).padStart(8, "0")}-1111-4111-8111-111111111111`,
  })) : [row(), row()];
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: items }));
  render(<MobilePending sessionKey="a" active />); await flush();
  expect(screen.queryByRole("region", { name: "Pending iPhone messages" })).toBeNull();
});

it.each([
  ["approval_pending", "Waiting for approval on this Mac"],
  ["desktop_input", "Waiting for desktop input to finish"],
  ["safe_prompt", "Waiting for a ready prompt"],
  ["delivering", "Delivering to the agent"],
])("uses an honest fixed label for %s", async (wait_reason, label) => {
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: [row("a", { wait_reason, status: wait_reason === "delivering" ? "delivering" : "queued" })] }));
  render(<MobilePending sessionKey="a" active />); await flush(); expect(screen.getByText(label)).toBeTruthy();
});

it.each([
  { session_id: "other" }, { status: "delivered" }, { wait_reason: "invented" }, { text: "x".repeat(4097) }, { expires_at: timestamp },
])("refuses malformed, unrelated or terminal rows: %o", async overrides => {
  vi.mocked(routedFetch).mockResolvedValue(response({ mobile_messages: [row("a", overrides)] }));
  render(<MobilePending sessionKey="a" active />); await flush();
  expect(screen.queryByRole("region", { name: "Pending iPhone messages" })).toBeNull();
});
