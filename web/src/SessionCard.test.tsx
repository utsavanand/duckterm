import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SessionCard } from "./SessionCard";
import { api } from "./api";
import type { SessionView } from "./types";

vi.mock("./api", () => ({ api: { saveNotes: vi.fn().mockResolvedValue({ updated: true }) } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it("appends merged notes to an open draft and guards the save against later replacements", async () => {
  const session = { key: "one", label: "Agent", state: "busy", runtime: "generic", notes: "Original" } as SessionView;
  const view = render(<SessionCard session={session} now={1} notesOpen />);
  fireEvent.change(screen.getByLabelText("Session notes"), { target: { value: "My edited draft" } });
  view.rerender(<SessionCard session={{ ...session, notes: "Original\n\nMerged notes" }} now={2} notesOpen />);
  expect(screen.getByLabelText("Session notes")).toHaveValue("My edited draft\n\nMerged notes");
  fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
  await waitFor(() => expect(api.saveNotes).toHaveBeenCalledWith("one", "My edited draft\n\nMerged notes", "Original\n\nMerged notes"));
});
