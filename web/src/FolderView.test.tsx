import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { FolderView } from "./FolderView";
vi.mock("./api", () => ({ api: { folderChat: vi.fn(), fleetAsk: vi.fn(), folderArtifacts: vi.fn(), folderRecipients: vi.fn(), folderDispatch: vi.fn() } }));
vi.mock("./FolderDetails", () => ({ FolderDetails: () => <aside>Folder details</aside> }));
vi.mock("./ArtifactsView", () => ({ ArtifactsView: ({ folder }: { folder: string }) => <p>Files from {folder}</p> }));
beforeEach(() => { vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("sends only the selected folder and keeps the answer when switching tabs", async () => {
  vi.mocked(api.folderChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.fleetAsk).mockResolvedValue({ answer: "Ready", exchange: { q: "Status?", a: "Ready", at: 1 }, sessions: [] });
  render(<FolderView folder="Website/Research" />);
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Status?" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Ask" })); });
  expect(api.fleetAsk).toHaveBeenCalledExactlyOnceWith("Status?", "Website/Research");
  expect(screen.getByText("Ready")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Sessions" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
  expect(screen.getByText("Files from Website/Research")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(screen.getByText("Ready")).toBeVisible();
});
it("ignores in-flight answers after changing folders", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.fleetAsk>>) => void;
  vi.mocked(api.folderChat).mockImplementation(folder => Promise.resolve({ messages: [{ q: folder, a: folder + " history", at: 1 }] }));
  vi.mocked(api.fleetAsk).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<FolderView folder="a" />);
  await screen.findByText("a history");
  fireEvent.click(screen.getByRole("button", { name: "What needs my attention?" }));
  view.rerender(<FolderView folder="b" />);
  await screen.findByText("b history");
  await act(async () => { finish({ answer: "Old answer", exchange: { q: "old", a: "Old answer", at: 2 }, sessions: [] }); });
  expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
  expect(screen.getByText("b history")).toBeVisible();
});
it("preserves a failed question and lets history failures retry", async () => {
  vi.mocked(api.folderChat).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ messages: [] });
  vi.mocked(api.fleetAsk).mockRejectedValue(new Error("summarizer unavailable"));
  render(<FolderView folder="a" />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep my question" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("summarizer unavailable");
  expect(screen.getByRole("textbox")).toHaveValue("Keep my question");
});

const recipients = { identity: "folder-id", sessions: [
  { session_id: "ui-1", name: "ui-dev", state: "busy", eligible: true, reason: null },
  { session_id: "qa-1", name: "qa", state: "idle", eligible: false, reason: "No inbox" },
], folders: [{ path: "Website/Research", name: "Research", recipients: [{ session_id: "research-1", name: "research", state: "busy", eligible: true, reason: null }] }] };
async function openPicker() {
  vi.mocked(api.folderChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.folderRecipients).mockResolvedValue(recipients);
  render(<FolderView folder="Website" />);
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "@" } });
  await screen.findByRole("option", { name: /ui-dev/ });
}
it("selects a scoped recipient with the keyboard and sends an owner request", async () => {
  await openPicker();
  expect(screen.getByRole("option", { name: /qa/ })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowDown" });
  expect(screen.getByRole("option", { name: /ui-dev/ })).toHaveFocus();
  fireEvent.click(screen.getByRole("option", { name: /ui-dev/ }));
  expect(screen.getByText("@ui-dev · Session")).toBeVisible();
  vi.mocked(api.folderDispatch).mockResolvedValue({ exchange: { q: "Review this", a: "", at: 2, dispatch: { request_key: "test", target: { kind: "session", id: "ui-1" }, label: "ui-dev", recipients: [{ session_id: "ui-1", name: "ui-dev", message_id: "q-test", status: "queued", answer: null }] } } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Review this" } });
  fireEvent.click(screen.getByRole("button", { name: "Send to ui-dev" }));
  await screen.findByText("ui-dev · Queued in inbox");
  expect(api.folderDispatch).toHaveBeenCalledWith("Website", expect.objectContaining({ identity: "folder-id", text: "Review this", target: { kind: "session", id: "ui-1" }, recipients: ["ui-1"] }));
  expect(api.fleetAsk).not.toHaveBeenCalled();
});
it("reviews subfolder recipients before sending and preserves cancelled drafts", async () => {
  await openPicker();
  fireEvent.click(screen.getByRole("option", { name: /Research/ }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Check sources" } });
  fireEvent.click(screen.getByRole("button", { name: "Review recipients" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("research · busy");
  expect(api.folderDispatch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("textbox")).toHaveValue("Check sources");
  expect(api.folderDispatch).not.toHaveBeenCalled();
});
it("reuses the submission key after a network failure and keeps the message", async () => {
  await openPicker();
  fireEvent.click(screen.getByRole("option", { name: /ui-dev/ }));
  vi.mocked(api.folderDispatch).mockRejectedValue(new Error("offline"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Keep this task" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send to ui-dev" })); });
  expect(screen.getByRole("textbox")).toHaveValue("Keep this task");
  const first = vi.mocked(api.folderDispatch).mock.calls[0][1];
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send to ui-dev" })); });
  expect(vi.mocked(api.folderDispatch).mock.calls[1][1]).toEqual(first);
  expect(screen.getByRole("alert")).toHaveTextContent("offline");
});
it("removing a mention restores answer-only behavior", async () => {
  await openPicker();
  fireEvent.click(screen.getByRole("option", { name: /ui-dev/ }));
  fireEvent.click(screen.getByRole("button", { name: "Remove recipient" }));
  vi.mocked(api.fleetAsk).mockResolvedValue({ answer: "Ready", exchange: { q: "Status", a: "Ready", at: 1 }, sessions: [] });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Status" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Ask" })); });
  expect(api.fleetAsk).toHaveBeenCalledWith("Status", "Website");
  expect(api.folderDispatch).not.toHaveBeenCalled();
});
