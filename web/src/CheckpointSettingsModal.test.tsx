import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type AiState, type CheckpointSettings } from "./api";
import { CheckpointSettingsModal } from "./CheckpointSettingsModal";

vi.mock("./api", () => ({ api: { checkpointSettings: vi.fn(), saveCheckpointSettings: vi.fn(), aiSettings: vi.fn() } }));
function aiState(backend: AiState["settings"]["backend"], harness: AiState["harness"] = "claude-code"): AiState {
  return {
    settings: { backend, harness: "auto", provider: "anthropic", base_url: "", fast_model: "", deep_model: "", harness_fast_model: "haiku", harness_deep_model: "sonnet" },
    keys: {} as AiState["keys"], harness, models: { fast: "claude-haiku-5-5", deep: "claude-sonnet-5-5" },
  };
}
beforeEach(() => { vi.mocked(api.aiSettings).mockResolvedValue(aiState("harness")); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const defaults: CheckpointSettings = { lookback_records: "all", writer_model: "sonnet" };

it("defaults to all unsummarized messages and saves a latest-N choice", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  vi.mocked(api.saveCheckpointSettings).mockResolvedValue({ ...defaults, lookback_records: 60, writer_model: "opus" });
  render(<CheckpointSettingsModal onClose={() => {}} />);
  const all = await screen.findByRole("radio", { name: "All unsummarized messages (default)" });
  expect(all).toBeChecked();
  const input = screen.getByLabelText("Latest records per checkpoint");
  expect(input).toBeDisabled();
  // Other AI features' models live in AI models settings, not here.
  expect(screen.queryByLabelText("Pull request reviewer")).toBeNull();
  expect(screen.queryByText(/batch/i)).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: /Only the latest/ }));
  expect(input).toBeEnabled();
  fireEvent.change(input, { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("Summary writer (Claude Code)"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Settings saved")).toBeVisible();
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: 60, writer_model: "opus" });
  fireEvent.click(screen.getByRole("button", { name: "Reset defaults" }));
  expect(all).toBeChecked();
  expect(screen.getByLabelText("Summary writer (Claude Code)")).toHaveValue("sonnet");
  expect(api.saveCheckpointSettings).toHaveBeenCalledTimes(1);
});

it("saves all as the literal setting", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue({ ...defaults, lookback_records: 40 });
  vi.mocked(api.saveCheckpointSettings).mockResolvedValue(defaults);
  render(<CheckpointSettingsModal onClose={() => {}} />);
  expect(await screen.findByLabelText("Latest records per checkpoint")).toHaveValue(40);
  fireEvent.click(screen.getByRole("radio", { name: "All unsummarized messages (default)" }));
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Settings saved")).toBeVisible();
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: "all", writer_model: "sonnet" });
});

it("rejects invalid limits and does not claim success after a lost response", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue({ ...defaults, lookback_records: 20 });
  vi.mocked(api.saveCheckpointSettings).mockRejectedValue(new Error("Connection lost"));
  render(<CheckpointSettingsModal onClose={() => {}} />);
  const input = await screen.findByLabelText("Latest records per checkpoint");
  const save = screen.getByRole("button", { name: "Save settings" });
  for (const value of ["", "0", "20.5", "1001"]) {
    fireEvent.change(input, { target: { value } });
    expect(save).toBeDisabled();
  }
  fireEvent.change(input, { target: { value: "40" } });
  fireEvent.click(save);
  expect(await screen.findByRole("alert")).toHaveTextContent("Settings save was not confirmed");
  expect(screen.queryByText("Settings saved")).toBeNull();
  vi.mocked(api.checkpointSettings).mockResolvedValue({ ...defaults, lookback_records: 40 });
  fireEvent.click(screen.getByRole("button", { name: "Reload settings" }));
  expect(await screen.findByLabelText("Latest records per checkpoint")).toHaveValue(40);
  expect(api.saveCheckpointSettings).toHaveBeenCalledTimes(1);
});

it.each([
  [aiState("api"), "Checkpoints use your fast API model, anthropic/claude-haiku-5-5. This setting applies when Claude Code runs them."],
  [aiState("duckterm"), "Checkpoints use DuckTerm's fast model. This setting applies when Claude Code runs them."],
  [aiState("harness", "codex"), "Codex runs checkpoints with its own default model. This setting applies when Claude Code runs them."],
])("says which model runs checkpoints when Claude Code does not %#", async (ai, note) => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  vi.mocked(api.aiSettings).mockResolvedValue(ai);
  render(<CheckpointSettingsModal onClose={() => {}} />);
  expect(await screen.findByText(note)).toBeVisible();
});

it("adds no note when Claude Code runs checkpoints or AI settings fail to load", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  render(<CheckpointSettingsModal onClose={() => {}} />);
  await screen.findByLabelText("Summary writer (Claude Code)");
  expect(screen.queryByText(/This setting applies when Claude Code runs them/)).toBeNull();
  cleanup();
  vi.mocked(api.aiSettings).mockRejectedValue(new Error("offline"));
  render(<CheckpointSettingsModal onClose={() => {}} />);
  expect(await screen.findByLabelText("Summary writer (Claude Code)")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
});
