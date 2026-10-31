import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SourceMemory, mergeClosure } from "./SourceMemory";
import { sessionRef } from "./hostTransport";
import { MemoryView } from "./MemoryView";
vi.mock("./MemoryView", () => ({ MemoryView: vi.fn(() => <button>Read evidence</button>) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it("keeps the source host and exact revision and supports keyboard dismissal", () => {
  const close = vi.fn();
  render(<SourceMemory origin={sessionRef("other-mac", "parent")} target={{ source: "child", name: "Child", revision: "older" }} onClose={close} />);
  expect(vi.mocked(MemoryView).mock.calls[0][0]).toMatchObject({ sessionKey: sessionRef("other-mac", "child"), initialRevision: "older", readOnly: true });
  const back = screen.getByRole("button", { name: "Back" });
  back.focus(); fireEvent.keyDown(back, { key: "Tab" });
  expect(screen.getByRole("button", { name: "✕" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
it("does not call a queued closure completed", () => {
  expect(mergeClosure(false, "pending")).toContain("Waiting to close");
  expect(mergeClosure(false, "draft_not_empty")).toContain("typed draft");
  expect(mergeClosure(true, "closed")).toContain("Source closed");
});
