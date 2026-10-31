import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointSettings } from "./api";
import { CheckpointSettingsModal } from "./CheckpointSettingsModal";

vi.mock("./api", () => ({ api: { checkpointSettings: vi.fn(), saveCheckpointSettings: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const defaults: CheckpointSettings = { lookback_records: "all", writer_model: "sonnet", reviewer_model: "sonnet" };

it("defaults to all unsummarized messages and saves a latest-N choice", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  vi.mocked(api.saveCheckpointSettings).mockResolvedValue({ ...defaults, lookback_records: 60, writer_model: "opus" });
  render(<CheckpointSettingsModal onClose={() => {}} />);
  const all = await screen.findByRole("radio", { name: "All unsummarized messages (default)" });
  expect(all).toBeChecked();
  const input = screen.getByLabelText("Latest records per checkpoint");
  expect(input).toBeDisabled();
  expect(screen.getByLabelText("Pull request reviewer")).toHaveValue("sonnet");
  expect(screen.queryByText(/batch/i)).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: /Only the latest/ }));
  expect(input).toBeEnabled();
  fireEvent.change(input, { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("Summary writer"), { target: { value: "opus" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Settings saved")).toBeVisible();
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: 60, writer_model: "opus", reviewer_model: "sonnet" });
  fireEvent.click(screen.getByRole("button", { name: "Reset defaults" }));
  expect(all).toBeChecked();
  expect(screen.getByLabelText("Summary writer")).toHaveValue("sonnet");
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
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: "all", writer_model: "sonnet", reviewer_model: "sonnet" });
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
