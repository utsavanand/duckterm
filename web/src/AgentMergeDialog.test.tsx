import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentMergeDialog, type AgentMergePreview, type AgentMergeRecord, type AgentMergeService } from "./AgentMergeDialog";

afterEach(cleanup);
const preview: AgentMergePreview = { source: { key: "source", name: "Source" }, target: { key: "target", name: "Destination", group: "Project", allowed: true, state: "busy", priorityDelivery: true }, context: "Saved context", notes: "Source notes", git: { allowed: false, reason: "These agents already share the same worktree." }, revision: "v1" };
const receipt: AgentMergeRecord = { id: "merge", source: "source", sourceName: "Source", target: "target", targetName: "Destination", context: "Saved context", notes: "Source notes", git: null, packet: "Reviewed packet", createdAt: 1, delivery: "pending next turn", statusAvailable: true };
function fixture() {
  const service: AgentMergeService = { targets: vi.fn().mockResolvedValue({ destinations: [preview.target, { ...preview.target, key: "other", name: "Other" }] }), preview: vi.fn().mockResolvedValue(preview), send: vi.fn().mockResolvedValue(receipt), history: vi.fn().mockResolvedValue({ merges: [] }) };
  const close = vi.fn();
  render(<AgentMergeDialog service={service} sourceName="Source" onClose={close} />);
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

it("ignores old destination responses and preserves owner edits when choosing another agent", async () => {
  const { service } = fixture();
  let resolveOld!: (value: AgentMergePreview) => void;
  vi.mocked(service.preview).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  await screen.findByRole("option", { name: /Destination/ });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "target" } });
  vi.mocked(service.preview).mockResolvedValue({ ...preview, target: { ...preview.target, key: "other", name: "Other" }, revision: "v2" });
  fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "other" } });
  await screen.findByDisplayValue("Saved context");
  fireEvent.change(screen.getByLabelText("Context to send"), { target: { value: "Edited" } });
  await act(async () => { resolveOld({ ...preview, context: "Stale result" }); });
  expect(screen.getByLabelText("Context to send")).toHaveValue("Edited");
  fireEvent.click(screen.getByRole("button", { name: "Review merge" }));
  fireEvent.click(screen.getByRole("button", { name: "Send merge request" }));
  await waitFor(() => expect(service.send).toHaveBeenCalledWith(expect.objectContaining({ target: "other", revision: "v2", context: "Edited" })));
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
