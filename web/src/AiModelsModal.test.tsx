import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type AiState, type CheckpointSettings } from "./api";
import { AiModelsModal } from "./AiModelsModal";

vi.mock("./api", () => ({
  api: {
    aiSettings: vi.fn(), saveAiSettings: vi.fn(), saveAiKey: vi.fn(), forgetAiKey: vi.fn(),
    checkpointSettings: vi.fn(), saveCheckpointSettings: vi.fn(),
  },
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const checkpoint: CheckpointSettings = { lookback_records: 40, writer_model: "haiku", reviewer_model: "sonnet" };
const noKeys = { saved: false, source: "" } as const;
function state(over: Partial<AiState["settings"]> = {}, saved = false): AiState {
  return {
    settings: { backend: "harness", harness: "auto", provider: "anthropic", base_url: "", fast_model: "", deep_model: "", ...over },
    keys: { anthropic: saved ? { saved: true, source: "stored" } : noKeys, openai: noKeys, openrouter: noKeys, custom: noKeys },
    harness: "claude-code",
    models: { fast: "claude-haiku-5-5", deep: "claude-sonnet-5-5" },
  };
}

it("defaults to the detected harness login and saves tier models as the checkpoint models", async () => {
  vi.mocked(api.aiSettings).mockResolvedValue(state());
  vi.mocked(api.checkpointSettings).mockResolvedValue(checkpoint);
  vi.mocked(api.saveAiSettings).mockResolvedValue(state());
  vi.mocked(api.saveCheckpointSettings).mockResolvedValue({ ...checkpoint, reviewer_model: "opus" });
  render(<AiModelsModal onClose={() => {}} />);
  expect(await screen.findByText("detected")).toBeVisible();
  expect(screen.getByRole("radio", { name: /Your Claude Code login/ })).toBeChecked();
  expect(screen.getByRole("radio", { name: /DuckTerm models/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Deep model"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Settings saved")).toBeVisible();
  // The lookback the checkpoint dialog owns is carried through unchanged.
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: 40, writer_model: "haiku", reviewer_model: "opus" });
});

it("verifies a key, offers its models, and never keeps the key in the form", async () => {
  vi.mocked(api.aiSettings).mockResolvedValueOnce(state()).mockResolvedValue(state({ backend: "api" }, true));
  vi.mocked(api.checkpointSettings).mockResolvedValue(checkpoint);
  vi.mocked(api.saveAiKey).mockResolvedValue({ provider: "anthropic", saved: true, models: ["claude-haiku-5-5", "claude-sonnet-5-5"] });
  render(<AiModelsModal onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Your API key/ }));
  const verify = screen.getByRole("button", { name: "Verify and save" });
  expect(verify).toBeDisabled();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: " sk-test " } });
  fireEvent.click(verify);
  expect(await screen.findByText("Saved in your Keychain · 2 models available")).toBeVisible();
  expect(api.saveAiKey).toHaveBeenCalledExactlyOnceWith("anthropic", "sk-test", "");
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(screen.getByLabelText("Fast model")).toHaveAttribute("placeholder", "claude-haiku-5-5");
  expect(screen.getByRole("button", { name: "Forget key" })).toBeVisible();
});

it("shows a rejected key's reason and does not claim it was saved", async () => {
  vi.mocked(api.aiSettings).mockResolvedValue(state({ backend: "api" }));
  vi.mocked(api.checkpointSettings).mockResolvedValue(checkpoint);
  vi.mocked(api.saveAiKey).mockRejectedValue(new Error("Provider returned 401: invalid x-api-key"));
  render(<AiModelsModal onClose={() => {}} />);
  fireEvent.change(await screen.findByLabelText("API key"), { target: { value: "sk-bad" } });
  fireEvent.click(screen.getByRole("button", { name: "Verify and save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("invalid x-api-key");
  expect(screen.queryByText(/Saved in your Keychain/)).toBeNull();
});

it("drops a custom base URL when the provider changes, so a named provider's key never goes there", async () => {
  vi.mocked(api.aiSettings).mockResolvedValue(state({ backend: "api", provider: "custom", base_url: "https://gateway.example/v1", fast_model: "m", deep_model: "m" }));
  vi.mocked(api.checkpointSettings).mockResolvedValue(checkpoint);
  vi.mocked(api.saveAiKey).mockResolvedValue({ provider: "anthropic", saved: true, models: [] });
  render(<AiModelsModal onClose={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "anthropic" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-test" } });
  fireEvent.click(screen.getByRole("button", { name: "Verify and save" }));
  expect(await screen.findByText(/Saved in your Keychain/)).toBeVisible();
  expect(api.saveAiKey).toHaveBeenCalledExactlyOnceWith("anthropic", "sk-test", "");
});
