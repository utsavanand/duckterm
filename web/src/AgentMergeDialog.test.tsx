import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentMergeDialog, type AgentMergePreview, type AgentMergeRecord, type AgentMergeService } from "./AgentMergeDialog";

import { api, type CheckpointRecord } from "./api";
vi.mock("./api", async importOriginal => ({ ...await importOriginal<typeof import("./api")>(), api: { checkpointPlan: vi.fn(), checkpoint: vi.fn(), checkpointAttempt: vi.fn() } }));
beforeEach(() => { vi.resetAllMocks(); });
afterEach(cleanup);
const preview: AgentMergePreview = { source: { key: "source", name: "Source" }, target: { key: "target", name: "Destination", group: "Project", allowed: true, state: "busy", priorityDelivery: true }, context: "Saved context", notes: "Source notes", git: { allowed: false, reason: "These agents already share the same worktree." }, revision: "v1" };
const receipt: AgentMergeRecord = { id: "merge", source: "source", sourceName: "Source", target: "target", targetName: "Destination", context: "Saved context", notes: "Source notes", git: null, packet: "Reviewed packet", createdAt: 1, delivery: "pending next turn", statusAvailable: true };
function fixture() {
  const service: AgentMergeService = { targets: vi.fn().mockResolvedValue({ destinations: [preview.target, { ...preview.target, key: "other", name: "Other" }] }), preview: vi.fn().mockResolvedValue(preview), send: vi.fn().mockResolvedValue(receipt), history: vi.fn().mockResolvedValue({ merges: [] }) };
  const close = vi.fn();
  render(<AgentMergeDialog sourceKey="source" service={service} sourceName="Source" onClose={close} />);
  return { service, close };
}
async function choose() {
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  await screen.findByDisplayValue("Saved context");
}

it("reviews choices and retries an uncertain send without duplicate request identities", async () => {
  const { service } = fixture();
  vi.mocked(service.send).mockRejectedValueOnce(new Error("Connection lost"));
  await choose();
  expect(screen.getByRole("checkbox", { name: /Code from/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: " Owner edited context \n" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /^Notes/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(service.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await screen.findByRole("region", { name: "Saved merge request" });
  const calls = vi.mocked(service.send).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[0][0]).toEqual(calls[1][0]);
  expect(calls[0][0]).toMatchObject({ context: " Owner edited context \n", notes: null, code: false, target: "target", revision: "v1" });
});

it("offers unrelated destinations as well as the parent", async () => {
  fixture();
  await screen.findByRole("option", { name: /Destination/ });
  expect(screen.getByRole("option", { name: /Other/ })).toBeEnabled();
  expect(screen.getAllByRole("option")).toHaveLength(3);
});

it("rejects a preview naming a different destination without sending", async () => {
  const { service } = fixture();
  vi.mocked(service.preview).mockResolvedValue({ ...preview, target: { ...preview.target, key: "other" } });
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  await screen.findByText(/preview does not match the selected destination/);
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  expect(service.send).not.toHaveBeenCalled();
});

it("code delivery is explicitly a request and never claims completed integration", async () => {
  const { service } = fixture();
  const git = { allowed: true, commits: 2, sourceCommit: "abc", targetCommit: "def", sourceBranch: "feature", targetBranch: "main" };
  vi.mocked(service.preview).mockResolvedValue({ ...preview, git });
  vi.mocked(service.send).mockResolvedValue({ ...receipt, git, delivery: "acknowledged", reply: "Found a conflict" });
  await choose();
  expect(screen.getByRole("checkbox", { name: /Code from/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: /Code from/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(screen.getByText(/Only committed changes are included/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await screen.findByText("Found a conflict");
  expect(screen.getByText(/does not confirm that the code is merged/)).toBeVisible();
  expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ code: true }));
});

it("cancel and Escape do not send a merge", async () => {
  const { service, close } = fixture();
  await choose();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
  expect(service.send).not.toHaveBeenCalled();
});


it("refresh loads a newly available summary and notes when the fields were untouched", async () => {
  const { service } = fixture();
  vi.mocked(service.preview).mockResolvedValueOnce({ ...preview, context: "", notes: "" });
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  await screen.findByText(/No saved conversation summary/);
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await screen.findByDisplayValue("Saved context");
  expect(screen.getByLabelText("Notes to append")).toHaveValue("Source notes");
  expect(screen.getByRole("button", { name: "Review merge" })).toBeEnabled();
  expect(service.send).not.toHaveBeenCalled();
});

it("refresh preserves edited fields, including deliberate clearing, and explicit exclusions", async () => {
  const { service } = fixture();
  await choose();
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Notes to append"), { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /^Notes/ }));
  vi.mocked(service.preview).mockResolvedValue({ ...preview, context: "New summary", notes: "New notes", revision: "v2" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh preview" })).toBeEnabled());
  expect(screen.getByLabelText("Context to send")).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: /^Notes/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Notes/ }));
  expect(screen.getByLabelText("Notes to append")).toHaveValue("My draft");
  expect(service.send).not.toHaveBeenCalled();
});

it("refresh updates untouched notes independently of an edited context", async () => {
  const { service } = fixture();
  await choose();
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "Owner draft" } });
  vi.mocked(service.preview).mockResolvedValue({ ...preview, context: "New summary", notes: "New notes", revision: "v2" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await screen.findByDisplayValue("New notes");
  expect(screen.getByLabelText("Context to send")).toHaveValue("Owner draft");
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(service.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ context: "Owner draft", notes: "New notes", revision: "v2" })));
});

it("keeps an explicit exclusion when refresh first discovers context", async () => {
  const { service } = fixture();
  vi.mocked(service.preview).mockResolvedValueOnce({ ...preview, context: "" });
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  await screen.findByDisplayValue("Source notes");
  const choice = screen.getByRole("checkbox", { name: /^Conversation context/ });
  fireEvent.click(choice); fireEvent.click(choice);
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh preview" })).toBeEnabled());
  expect(screen.getByRole("checkbox", { name: /^Conversation context/ })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Conversation context/ }));
  expect(screen.getByLabelText("Context to send")).toHaveValue("Saved context");
});


const checkpointPlan = { available_records: 8, processed_records: 0, pending_records: 8, default_records: 20, selected_records: 8, remaining_records: 0, order: "oldest_unprocessed_first" as const, selection_plan: { source: "frozen" } };
const checkpointResult: CheckpointRecord = { id: "cp", label: "manual", created_at: 1, saved: true, summary: "Response display text is not canonical merge context", summary_update: { state: "updated" }, record: { prompts: [], files: [], tools: [], event_count: 0 } };
async function emptySource() {
  const f = fixture();
  vi.mocked(f.service.preview).mockResolvedValue({ ...preview, context: "", notes: "" });
  vi.mocked(api.checkpointPlan).mockResolvedValue(checkpointPlan);
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  await screen.findByText(/No saved conversation summary/);
  return f;
}
it("explains empty checked sections and permits manually reviewed context without a checkpoint", async () => {
  const { service } = await emptySource();
  expect(screen.getByText(/Choose context, notes, or available code/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Conversation context/ }));
  expect(screen.getByLabelText("Context to send")).toHaveAccessibleDescription(/Write context to send/);
  fireEvent.click(screen.getByRole("checkbox", { name: /^Notes/ }));
  expect(screen.getByLabelText("Notes to append")).toHaveAccessibleDescription(/Write notes to append/);
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "My context" } });
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Notes/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(service.send).not.toHaveBeenCalled();
  expect(api.checkpoint).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ context: "My context", notes: null })));
});
it("canceling the existing checkpoint selector restores the merge draft without work", async () => {
  const { service, close } = await emptySource();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Conversation context/ }));
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "Draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate summary…" }));
  await screen.findByRole("button", { name: "Checkpoint 8 records" });
  expect(screen.queryByRole("dialog", { name: "Merge with agent" })).toBeNull();
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Checkpoint" }), { key: "Escape" });
  expect(screen.getByLabelText("Context to send")).toHaveValue("Draft");
  expect(close).not.toHaveBeenCalled(); expect(service.send).not.toHaveBeenCalled(); expect(api.checkpoint).not.toHaveBeenCalled();
});
it("generates only after count confirmation, reloads canonical context, and still requires review/send", async () => {
  const { service } = await emptySource();
  let finish!: (result: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Generate summary…" }));
  const button = await screen.findByRole("button", { name: "Checkpoint 8 records" });
  expect(api.checkpoint).not.toHaveBeenCalled();
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith("source", "manual", expect.any(String), { plan: checkpointPlan.selection_plan, records: 20 }));
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Generate summary…" })).toBeDisabled();
  vi.mocked(service.preview).mockResolvedValue({ ...preview, context: "Canonical new summary", notes: "", revision: "after-checkpoint" });
  await act(async () => { finish(checkpointResult); });
  await screen.findByDisplayValue("Canonical new summary");
  expect(screen.queryByDisplayValue(checkpointResult.summary!)).toBeNull();
  expect(service.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(service.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ revision: "after-checkpoint", context: "Canonical new summary", notes: null })));
});
it("keeps context typed while a checkpoint runs and identifies a failed summary", async () => {
  const { service } = await emptySource();
  let finish!: (result: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Generate summary…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Checkpoint 8 records" }));
  await waitFor(() => expect(api.checkpoint).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("checkbox", { name: /^Conversation context/ }));
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "New owner draft" } });
  await act(async () => { finish({ ...checkpointResult, summary_update: { state: "failed", reason: "provider_failed" } }); });
  await screen.findByText(/Summary update failed ·/);
  expect(screen.getByLabelText("Context to send")).toHaveValue("New owner draft");
  expect(service.send).not.toHaveBeenCalled();
});

it("recovers a lost checkpoint response without issuing another checkpoint", async () => {
  const { service } = await emptySource();
  vi.mocked(api.checkpoint).mockImplementation(async (_key, _label, requestId) => {
    vi.mocked(api.checkpointAttempt).mockResolvedValue({ request_id: requestId!, state: "complete", result: { ...checkpointResult, request_id: requestId } });
    vi.mocked(service.preview).mockResolvedValue({ ...preview, context: "Recovered canonical summary", notes: "", revision: "recovered" });
    throw new Error("Response lost");
  });
  fireEvent.click(screen.getByRole("button", { name: "Generate summary…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Checkpoint 8 records" }));
  await screen.findByDisplayValue("Recovered canonical summary");
  expect(api.checkpoint).toHaveBeenCalledOnce();
  expect(api.checkpointAttempt).toHaveBeenCalled();
  expect(service.send).not.toHaveBeenCalled();
});

it("a failed refresh prevents stale revision sends and retains drafts for retry", async () => {
  const { service } = fixture(); await choose();
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "My draft survives" } });
  vi.mocked(service.preview).mockRejectedValueOnce(new Error("Offline"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await screen.findByText(/Could not prepare merge: Offline/);
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  expect(service.send).not.toHaveBeenCalled();
  vi.mocked(service.preview).mockResolvedValue({ ...preview, revision: "fresh" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await screen.findByDisplayValue("My draft survives");
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ revision: "fresh", context: "My draft survives" })));
});


it("changing destinations keeps edits but requires the new destination preview before sending", async () => {
  const { service } = fixture();
  await choose();
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "Owner draft" } });
  let finish!: (value: AgentMergePreview) => void;
  vi.mocked(service.preview).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "other" } });
  expect(screen.getByRole("button", { name: "Review merge" })).toBeDisabled();
  expect(service.send).not.toHaveBeenCalled();
  await act(async () => { finish({ ...preview, target: { ...preview.target, key: "other", name: "Other" }, revision: "other-v1" }); });
  expect(screen.getByLabelText("Context to send")).toHaveValue("Owner draft");
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  expect(screen.getByText("Other", { selector: "strong" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ target: "other", revision: "other-v1", context: "Owner draft" })));
});
