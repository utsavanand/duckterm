import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { MessageFolderModal } from "./MessageFolderModal";
vi.mock("./api", () => ({ api: { broadcastTargets: vi.fn(), broadcast: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const targets = [
  { session_id: "a", name: "Main", state: "busy", eligible: true, reason: "" },
  { session_id: "b", name: "Private", state: "idle", eligible: false, reason: "Not enrolled" },
];
it("reviews eligibility and retains the idempotency key after an ambiguous send failure", async () => {
  vi.mocked(api.broadcastTargets).mockResolvedValue({ targets });
  vi.mocked(api.broadcast).mockRejectedValueOnce(new Error("Connection lost"));
  vi.mocked(api.broadcast).mockResolvedValue({ queued: 1, skipped: 1, results: targets.map((t) => ({ ...t, status: t.eligible ? "queued" : "skipped" })) });
  render(<MessageFolderModal folder="Project" onClose={() => {}} />);
  const send = await screen.findByRole("button", { name: "Send to 1 session" });
  expect(send).toBeDisabled();
  expect(screen.getByText(/Skipped: Not enrolled/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Please review the roadmap" } });
  fireEvent.click(send);
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
  expect(screen.getByLabelText("Message")).toHaveValue("Please review the roadmap");
  fireEvent.click(send);
  expect(await screen.findByRole("status")).toHaveTextContent("Message queued for 1 session. 1 skipped.");
  expect(vi.mocked(api.broadcast).mock.calls[0]).toEqual(vi.mocked(api.broadcast).mock.calls[1]);
});
it("blocks empty recipient lists and oversized UTF-8 messages", async () => {
  vi.mocked(api.broadcastTargets).mockResolvedValue({ targets: [] });
  render(<MessageFolderModal folder="Empty" onClose={() => {}} />);
  await screen.findByText("No eligible sessions in this folder.");
  expect(screen.getByRole("button", { name: "Send to 0 sessions" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: "😀".repeat(5000) } });
  expect(screen.getByRole("alert")).toHaveTextContent("16 KB");
  expect(api.broadcast).not.toHaveBeenCalled();
});
