import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, Artifact, ArtifactContent } from "./api";
import { ArtifactsView, previewDocument } from "./ArtifactsView";
vi.mock("./api", () => ({ api: { artifacts: vi.fn(), artifact: vi.fn(), removeArtifact: vi.fn() } }));
const first: Artifact = { id: "a", session_key: "session", title: "First report", source_path: "/tmp/first.md", media_type: "text/markdown", size: 8, sha256: "first", created_at: 1, updated_at: 1 };
const second: Artifact = { ...first, id: "b", title: "Second report", sha256: "second", source_path: "/tmp/second.md" };
const content = (artifact: Artifact): ArtifactContent => ({ ...artifact, content_base64: btoa(`# ${artifact.title}`) });
beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:artifact"), revokeObjectURL: vi.fn() }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.unstubAllGlobals(); });
it("ignores an older preview response after another artifact is selected", async () => {
  vi.mocked(api.artifacts).mockResolvedValue({ artifacts: [first, second] });
  let finish!: (value: { artifact: ArtifactContent }) => void;
  vi.mocked(api.artifact).mockImplementation((_, id) => id === "a" ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ artifact: content(second) }));
  render(<ArtifactsView sessionKey="session" sessionName="Agent" />);
  fireEvent.click(await screen.findByRole("button", { name: /Second report/ }));
  await screen.findByTitle("Preview of Second report");
  await act(async () => { finish({ artifact: content(first) }); });
  expect(screen.queryByTitle("Preview of First report")).not.toBeInTheDocument();
  expect(screen.getByTitle("Preview of Second report")).toHaveAttribute("sandbox", "");
});
it("shows failed removal without losing the saved artifact, then removes it after success", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(api.artifacts).mockResolvedValue({ artifacts: [first] });
  vi.mocked(api.artifact).mockResolvedValue({ artifact: content(first) });
  vi.mocked(api.removeArtifact).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ removed: true });
  render(<ArtifactsView sessionKey="session" sessionName="Agent" />);
  await screen.findByTitle("Preview of First report");
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  expect(screen.getByTitle("Preview of First report")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await screen.findByText("Your outputs will appear here");
  expect(api.removeArtifact).toHaveBeenLastCalledWith("session", "a");
});
it("keeps scripts, navigation and hostile markup out of both preview formats", () => {
  const source = `<html><head><meta http-equiv="refresh" content="0;url=https://example.invalid"><style>h1{color:red}</style></head><body><h1>Mockup</h1><script>parent.hacked=1</script><iframe src="/sessions"></iframe><a href="javascript:alert(1)">Link</a><img src=x onerror="alert(1)"></body></html>`;
  for (const markdown of [true, false]) {
    const doc = previewDocument(source, markdown);
    expect(doc).not.toMatch(/<script|<iframe|http-equiv="refresh"|onerror|href=/);
    expect(doc).toContain("default-src 'none'");
    expect(doc).toContain("Mockup");
  }
});
