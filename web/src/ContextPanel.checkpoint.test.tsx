import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, CheckpointRecord, CheckpointActivity } from "./api";
import { ContextPanel } from "./ContextPanel";
import type { SessionView } from "./types";
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock("./api", () => ({ api: { checkpoints: vi.fn(), checkpoint: vi.fn(), sendInput: vi.fn() } }));
vi.mock("./ui", () => ({ useToast: () => toast }));
vi.mock("./Duck", () => ({ Duck: () => null, duckPhrase: () => "idle", poseFor: () => "idle" }));
vi.mock("./FileEditModal", () => ({ FileEditModal: () => null }));
const session = { key: "one", state: "idle", label: "Test", startedAt: 1, contextTokens: 190000, ptyOwned: true, runtime: "codex", model: "gpt-6-astra" } as SessionView;
const cp: CheckpointRecord = { id: "c", label: "manual", created_at: 1000, summary: "Saved summary", saved: true, summary_state: "unavailable", record: { prompts: [], files: [], tools: [], event_count: 0 } };
beforeEach(() => { vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [cp] }); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("groups checkpoint information and actions separately from active context", async () => {
  render(<ContextPanel session={session} />);
  fireEvent.click(await screen.findByText("Latest attempt · Summary unavailable"));
  expect(screen.getByText(new Date(cp.created_at).toLocaleString())).toBeInTheDocument();
  expect(screen.getByText("Summary unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Checkpoint" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Compact" })).toBeEnabled();
});
it("ignores a checkpoint fetch from a previous session", async () => {
  let finish!: (value: { checkpoints: CheckpointRecord[] }) => void;
  vi.mocked(api.checkpoints).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue({ checkpoints: [] });
  const view = render(<ContextPanel session={session} />);
  view.rerender(<ContextPanel session={{ ...session, key: "two" }} />);
  await act(async () => { finish({ checkpoints: [cp] }); });
  expect(screen.queryByLabelText("Latest checkpoint")).toBeNull();
});

it("does not invent event counts or infer compaction from lifetime events and model usage", async () => {
  render(<ContextPanel session={{ ...session, eventCount: 40000, contextTokens: 334881, model: "claude-opus-5" }} />);
  await screen.findByText("Latest attempt · Summary unavailable");
  expect(screen.getByText("Events since saved capture").nextElementSibling).toHaveTextContent("Not provided by this server");
  expect(screen.getByText("Last compaction").nextElementSibling).toHaveTextContent("Unknown");
  expect(screen.getByText("Context window").nextElementSibling).toHaveTextContent("Not reported");
  expect(screen.getByText("334,881")).toBeVisible();
  expect(api.sendInput).not.toHaveBeenCalled();
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it("keeps zero reported context distinct from unknown", () => {
  const view = render(<ContextPanel session={{ ...session, contextTokens: 0 }} />);
  expect(screen.getByText("0")).toBeVisible();
  view.rerender(<ContextPanel session={{ ...session, contextTokens: undefined }} />);
  expect(screen.queryByText("tokens reported")).toBeNull();
});

const activity = (count: number): CheckpointActivity => ({ state: "known", checkpoint_id: "saved", events_since_capture: count, event_filter: "work-v1", reason: null });
it.each([0, 1026])("shows measured activity %s despite a newer failed attempt and lifetime totals", async count => {
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [cp], checkpoint_activity: activity(count) });
  render(<ContextPanel session={{ ...session, eventCount: 90000 }} />);
  await screen.findByText("Latest attempt · Summary unavailable");
  expect(screen.getByText("Events since saved capture").nextElementSibling).toHaveTextContent(count.toLocaleString());
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it.each([
  { ...activity(0), state: "never" as const, events_since_capture: null, reason: "no_successful_checkpoint" },
  { ...activity(0), state: "unknown" as const, events_since_capture: null, reason: "boundary_missing" },
])("distinguishes absent evidence: $state", async value => {
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [], checkpoint_activity: value });
  render(<ContextPanel session={session} />);
  await screen.findByText(value.state === "never" ? "No successful checkpoint yet" : "Capture boundary unavailable");
  expect(screen.getByText("Events since saved capture").nextElementSibling).not.toHaveTextContent(/^0$/);
});
it("clears a previously measured count when refresh fails", async () => {
  vi.mocked(api.checkpoints).mockResolvedValueOnce({ checkpoints: [], checkpoint_activity: activity(1026) }).mockRejectedValue(new Error("offline"));
  render(<ContextPanel session={session} />);
  await screen.findByText("1,026");
  act(() => { window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: session.key })); });
  await screen.findByText("Couldn’t load activity");
  expect(screen.queryByText("1,026")).toBeNull();
});
it("ignores activity returned after selection changes", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.checkpoints>>) => void;
  vi.mocked(api.checkpoints).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue({ checkpoints: [], checkpoint_activity: activity(2) });
  const view = render(<ContextPanel session={session} />);
  view.rerender(<ContextPanel session={{ ...session, key: "two" }} />);
  await act(async () => { finish({ checkpoints: [cp], checkpoint_activity: activity(1026) }); });
  expect(screen.getByText("Events since saved capture").nextElementSibling).toHaveTextContent(/^2$/);
  expect(screen.queryByText("1,026")).toBeNull();
});
