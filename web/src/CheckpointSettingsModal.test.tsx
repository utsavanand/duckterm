import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointSettings } from "./api";
import { CheckpointSettingsModal } from "./CheckpointSettingsModal";

vi.mock("./api", () => ({ api: { checkpointSettings: vi.fn(), saveCheckpointSettings: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const defaults: CheckpointSettings = { lookback_records: 20, batch_records: 20, writer_model: "haiku", reviewer_model: "sonnet" };

it("loads settings, explains batches, and saves only editable preferences", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  vi.mocked(api.saveCheckpointSettings).mockResolvedValue({ ...defaults, lookback_records: 60, writer_model: "sonnet" });
  render(<CheckpointSettingsModal onClose={() => {}} />);
  const input = await screen.findByLabelText("Recent records to include");
  expect(input).toHaveValue(20);
  expect(api.saveCheckpointSettings).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("Summary writer"), { target: { value: "sonnet" } });
  expect(screen.getByText(/At least 3 batches/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByText("Settings saved")).toBeVisible();
  expect(api.saveCheckpointSettings).toHaveBeenCalledExactlyOnceWith({ lookback_records: 60, writer_model: "sonnet", reviewer_model: "sonnet" });
  fireEvent.click(screen.getByRole("button", { name: "Reset defaults" }));
  expect(input).toHaveValue(20);
  expect(screen.getByLabelText("Summary writer")).toHaveValue("haiku");
  expect(api.saveCheckpointSettings).toHaveBeenCalledTimes(1);
});

it("rejects invalid limits and does not claim success after a lost response", async () => {
  vi.mocked(api.checkpointSettings).mockResolvedValue(defaults);
  vi.mocked(api.saveCheckpointSettings).mockRejectedValue(new Error("Connection lost"));
  render(<CheckpointSettingsModal onClose={() => {}} />);
  const input = await screen.findByLabelText("Recent records to include");
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
  expect(await screen.findByLabelText("Recent records to include")).toHaveValue(40);
  expect(api.saveCheckpointSettings).toHaveBeenCalledTimes(1);
});
