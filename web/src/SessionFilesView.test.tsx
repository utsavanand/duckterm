import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { SessionFilesView } from "./SessionFilesView";
import { sessionFiles, FileRequestError, type DirectoryPage, type FileEntry, type RegisteredFile } from "./sessionFiles";
import type { SessionView } from "./types";
vi.mock("./api", () => ({ api: { artifacts: vi.fn(), artifact: vi.fn(), annotations: vi.fn(), fileFeedback: vi.fn(), updateArtifact: vi.fn() }, authHeaders: () => ({}) }));
vi.mock("./sessionFiles", async original => ({ ...await original<typeof import("./sessionFiles")>(), sessionFiles: { list: vi.fn(), content: vi.fn() } }));
const session = { key: "local", host: "local", cwd: "/work", label: "Agent" } as SessionView;
const file: FileEntry = { name: "notes.txt", path: "notes.txt", type: "file", size: 7, modified_at: 1000, browsable: true, content_limit: 5242880, artifact: null };
const saved: RegisteredFile = { id: "saved", session_key: "local", title: "Report", source_path: "/elsewhere/report.txt", media_type: "text/plain", size: 8, sha256: "saved-sha", created_at: 1000, updated_at: 1000, path: null, source_state: "outside_root", content_only: true };
const page = (entries: FileEntry[] = [], extra: Partial<DirectoryPage> = {}): DirectoryPage => ({ available: true, root: "/work", path: ".", entries, next_cursor: null, truncated: false, limit_reached: false, ignore_status: "applied", artifacts: [], ...extra });
beforeEach(() => {
  vi.mocked(api.annotations).mockResolvedValue({ annotations: [] });
  vi.mocked(sessionFiles.list).mockResolvedValue(page([file]));
  vi.mocked(sessionFiles.content).mockResolvedValue({ available: true, root: "/work", ...file, name: file.name, size: 7, media_type: "text/plain", content_base64: btoa("current"), sha256: "current-sha" });
  vi.mocked(api.artifacts).mockResolvedValue({ artifacts: [saved] });
  vi.mocked(api.artifact).mockResolvedValue({ artifact: { ...saved, content_base64: btoa("snapshot") } });
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:preview"), revokeObjectURL: vi.fn() }));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });
it("loads once under StrictMode and only expands requested directories", async () => {
  vi.mocked(sessionFiles.list).mockResolvedValueOnce(page([{ ...file, name: "docs", path: "docs", type: "directory" }])).mockResolvedValue(page([file], { path: "docs" }));
  render(<StrictMode><SessionFilesView session={session} /></StrictMode>);
  fireEvent.click(await screen.findByRole("button", { name: /docs/ }));
  await screen.findByRole("button", { name: /notes.txt/ });
  expect(sessionFiles.list).toHaveBeenCalledTimes(2);
  expect(vi.mocked(sessionFiles.list).mock.calls.map(c => c[1])).toEqual([".", "docs"]);
});
it("keeps load more on an empty ignored page and drops old entries after a cursor conflict", async () => {
  vi.mocked(sessionFiles.list).mockResolvedValueOnce(page([], { next_cursor: "first" })).mockResolvedValueOnce(page([file], { next_cursor: "second" })).mockRejectedValueOnce(new FileRequestError("Directory changed", 409));
  render(<SessionFilesView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: /Load more/ }));
  await screen.findByRole("button", { name: /notes.txt/ });
  fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
  await screen.findByText("Directory changed");
  expect(screen.queryByRole("button", { name: /notes.txt/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Load more/ })).not.toBeInTheDocument();
});
it("never browses a raw remote key and routes saved snapshots with host identity", async () => {
  render(<SessionFilesView session={{ ...session, host: "server" }} mode="artifacts" />);
  await screen.findByRole("button", { name: "Text Report" });
  expect(sessionFiles.list).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "Text Report" }));
  await screen.findByText("snapshot");
  expect(sessionFiles.content).not.toHaveBeenCalled();
  expect(vi.mocked(api.artifact).mock.calls[0][0]).toMatch(/^~remote~/);
});
it("keeps current content separate from its saved version, and does not reload on session status updates", async () => {
  vi.mocked(sessionFiles.list).mockResolvedValue(page([{ ...file, artifact: { ...saved, path: file.path, source_state: "present" } }]));
  const view = render(<SessionFilesView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: /notes.txt/ }));
  await screen.findByText("current");
  view.rerender(<SessionFilesView session={{ ...session, label: "Still working" }} />);
  expect(sessionFiles.content).toHaveBeenCalledTimes(1);
  expect(api.artifact).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "View saved version" }));
  await screen.findByText("snapshot");
  expect(api.artifact).toHaveBeenCalledWith("local", "saved");
  expect(URL.revokeObjectURL).toHaveBeenCalled();
});
it.each([{ type: "symlink" as const, browsable: false, message: /Symbolic link — not followed/ }, { type: "file" as const, browsable: false, message: /exceeds the 5 MiB/ }])("does not fetch targets that cannot be read: $type", async ({ type, browsable, message }) => {
  vi.mocked(sessionFiles.list).mockResolvedValue(page([{ ...file, type, browsable }]));
  render(<SessionFilesView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: /notes.txt/ }));
  await screen.findByText(message);
  expect(sessionFiles.content).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: "Download" })).not.toBeInTheDocument();
});
it("discards a late local response when switching to a remote session", async () => {
  let resolve!: (p: DirectoryPage) => void;
  vi.mocked(sessionFiles.list).mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<SessionFilesView session={session} />);
  await waitFor(() => expect(sessionFiles.list).toHaveBeenCalledTimes(1));
  view.rerender(<SessionFilesView session={{ ...session, host: "remote" }} />);
  await act(async () => resolve(page([file])));
  expect(screen.queryByRole("button", { name: /notes.txt/ })).not.toBeInTheDocument();
  expect(vi.mocked(sessionFiles.list).mock.calls[0][3]?.aborted).toBe(true);
});
it("does not call outside-root files missing, or fall back to an unfiltered listing", async () => {
  vi.mocked(sessionFiles.list).mockRejectedValue(new FileRequestError("Ignore rules unavailable", 503));
  render(<SessionFilesView session={session} mode="artifacts" />);
  fireEvent.click(await screen.findByRole("button", { name: "Text Report" }));
  await screen.findByText("snapshot");
  expect(screen.getByText(/Original file availability is unknown/)).toBeInTheDocument();
  expect(screen.queryByText(/original file is missing/)).not.toBeInTheDocument();
  expect(sessionFiles.list).toHaveBeenCalledTimes(1);
});
it("shows a scan ceiling as incomplete and preserves the saved copy's outside-root wording", async () => {
  vi.mocked(sessionFiles.list).mockResolvedValue(page([file], { limit_reached: true, artifacts: [saved] }));
  const view = render(<SessionFilesView session={session} />);
  await screen.findByText(/Listing incomplete: 10,000 entries/);
  view.rerender(<SessionFilesView session={session} mode="artifacts" />);
  fireEvent.click(await screen.findByRole("button", { name: "Text Report" }));
  await screen.findByText("snapshot");
  expect(screen.getByText(/original path is outside this worktree/)).toBeInTheDocument();
  expect(screen.queryByText(/original file is missing/)).not.toBeInTheDocument();
});
it("clears selection and revokes its download when the recorded root changes", async () => {
  const view = render(<SessionFilesView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: /notes.txt/ }));
  await screen.findByText("current");
  vi.mocked(sessionFiles.list).mockResolvedValue(page([], { root: "/other" }));
  view.rerender(<SessionFilesView session={{ ...session, cwd: "/other" }} />);
  await screen.findByText("/other");
  expect(screen.queryByText("current")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Download" })).not.toBeInTheDocument();
  expect(URL.revokeObjectURL).toHaveBeenCalled();
});
it("browses and reads files without any registered-artifact request when the edition has no artifacts", async () => {
  const registered = { ...saved, path: "notes.txt", source_state: "present" as const };
  vi.mocked(sessionFiles.list).mockResolvedValue(page([{ ...file, artifact: registered }], { artifacts: [registered] }));
  render(<SessionFilesView session={session} artifacts={false} />);
  fireEvent.click(await screen.findByRole("button", { name: /notes.txt/ }));
  await screen.findByText("current");
  expect(screen.queryByRole("group", { name: "Files filter" })).not.toBeInTheDocument();
  expect(screen.queryByText("Registered artifact")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "View saved version" })).not.toBeInTheDocument();
  expect(api.artifacts).not.toHaveBeenCalled();
  expect(api.artifact).not.toHaveBeenCalled();
});
it("does not fall back to saved copies without artifacts when the directory is unavailable", async () => {
  vi.mocked(sessionFiles.list).mockResolvedValue({ available: false, reason: "missing" });
  render(<SessionFilesView session={session} artifacts={false} />);
  await screen.findByText("No directory is shown.");
  expect(screen.queryByRole("button", { name: "View saved copies" })).not.toBeInTheDocument();
  render(<SessionFilesView session={{ ...session, host: "server" }} artifacts={false} />);
  await screen.findByText("Remote — not browsable here");
  expect(api.artifacts).not.toHaveBeenCalled();
});
it("combines format, label, search and recent ordering in one artifact list", async () => {
  const rows: RegisteredFile[] = [
    { ...saved, id: "md", title: "Plan", kind: "spec", media_type: "text/markdown", updated_at: 10 },
    { ...saved, id: "html", title: "Preview", kind: "preview", media_type: "text/html", updated_at: 20 },
    { ...saved, id: "react", title: "Interactive", kind: "preview", media_type: "text/html", source_path: "/app.react.html", updated_at: 30 },
  ];
  vi.mocked(sessionFiles.list).mockResolvedValue(page([], { artifacts: rows }));
  render(<SessionFilesView session={session} mode="artifacts" />);
  await screen.findByRole("button", { name: "React app Interactive" });
  const list = document.querySelector(".rd-files-scroll")!;
  expect([...list.querySelectorAll("button")].map(b => b.getAttribute("aria-label"))).toEqual(["React app Interactive", "HTML Preview", "Markdown Plan"]);
  fireEvent.change(screen.getByLabelText("Artifact format"), { target: { value: "react" } });
  expect(screen.queryByRole("button", { name: "HTML Preview" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Artifact label"), { target: { value: "spec" } });
  expect(screen.getByText("No matching artifacts.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Artifact format"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("Search artifacts"), { target: { value: "SPEC" } });
  expect(screen.getByRole("button", { name: "Markdown Plan" })).toBeInTheDocument();
});
it("sends file feedback with the displayed root/hash and shows versioned history", async () => {
  vi.mocked(api.fileFeedback).mockResolvedValue(undefined);
  vi.mocked(api.annotations).mockResolvedValue({ annotations: [{ id: "old", quote: JSON.stringify({ type: "file_feedback", root: "/work", path: "notes.txt", sha256: "old", selected_text: "Previous text" }), note: "Earlier note", created_at: 1000 }] });
  render(<SessionFilesView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: /notes.txt/ }));
  await screen.findByText("current");
  fireEvent.click(screen.getByRole("button", { name: "Leave feedback" }));
  fireEvent.change(screen.getByLabelText("Feedback to agent"), { target: { value: "Fix this" } });
  fireEvent.click(screen.getByRole("button", { name: "Send ⌘↵" }));
  await screen.findByText("Sent to the agent");
  expect(api.fileFeedback).toHaveBeenCalledWith("local", expect.objectContaining({ path: "notes.txt", root: "/work", sha256: "current-sha" }), "", "Fix this");
  expect(screen.getByText("Earlier versions (1)")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show quote" })).not.toBeInTheDocument();
});
