import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type AgentMemoryHealth } from "./api";
import { CheckpointHealth } from "./CheckpointHealth";

vi.mock("./api", () => ({ api: { checkpointHealth: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const now = Date.now();
const agent = (over: Partial<AgentMemoryHealth>): AgentMemoryHealth => ({
  key: "k", name: "a", runtime: "claude-code", status: "current", saved_at: now - 5 * 60000,
  new_messages: 0, gaps: 0, active_at: now, last_attempt: null, ...over,
});

it("counts each status, explains a failure and opens an agent", async () => {
  vi.mocked(api.checkpointHealth).mockResolvedValue({ automatic: false, agents: [
    agent({ key: "m", name: "main-dev", runtime: "codex", status: "failing", saved_at: null, last_attempt: { at: now - 2 * 3600000, state: "failed", reason: "source_unavailable" } }),
    agent({ key: "p", name: "pr-review", status: "none", saved_at: null, new_messages: 177 }),
    agent({ key: "r", name: "release-dev", status: "behind", saved_at: now - 3 * 3600000, new_messages: 10, gaps: 1 }),
    agent({ key: "c", name: "architect" }),
  ] });
  const open = vi.fn();
  render(<CheckpointHealth onOpenAgent={open} />);
  expect(await screen.findByText("1 current")).toBeVisible();
  expect(screen.getByText("1 behind")).toBeVisible();
  expect(screen.getByText("1 failing")).toBeVisible();
  expect(screen.getByText("1 no memory yet")).toBeVisible();
  expect(screen.getByText("Automatic checkpoints: off")).toBeVisible();
  expect(screen.getByText("Last attempt 2 h ago: couldn't read its history")).toBeVisible();
  expect(screen.getByText("177 messages not summarized")).toBeVisible();
  expect(screen.getByText("10 messages since")).toBeVisible();
  expect(screen.getByText("Up to date")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "release-dev" }));
  expect(open).toHaveBeenCalledExactlyOnceWith("r");
});

it("offers a retry when the report fails to load", async () => {
  vi.mocked(api.checkpointHealth).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ automatic: true, agents: [] });
  render(<CheckpointHealth />);
  expect(await screen.findByText("Agent memory could not be loaded: offline")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("No agents were active in the last 7 days.")).toBeVisible();
  expect(screen.getByText("Automatic checkpoints: on")).toBeVisible();
});
