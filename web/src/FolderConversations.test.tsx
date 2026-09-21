import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { api } from "./api";
import { FolderConversations } from "./FolderConversations";

vi.mock("./api", () => ({ api: { folderConversations: vi.fn() } }));
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const message = {
  id: "q1", sender: "a", recipient: "b", sender_name: "API agent", recipient_name: "UI agent",
  question: "What fields?", status: "accepted" as const, answer: null, created_at: 1000, expires_at: 2000, answered_at: null,
};

it("distinguishes failure from empty history and supports retry", async () => {
  vi.mocked(api.folderConversations).mockRejectedValueOnce(new Error("offline"));
  vi.mocked(api.folderConversations).mockResolvedValue({ messages: [], next_cursor: null });
  render(<FolderConversations folder="work" onClose={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  expect(screen.queryByText(/No conversations yet/)).toBeNull();
  fireEvent.click(screen.getByText("Retry"));
  expect(await screen.findByText(/No conversations yet/)).toBeVisible();
});

it("loads older exchanges without duplicating the latest page", async () => {
  vi.mocked(api.folderConversations).mockImplementation(async (_folder, cursor) => cursor === undefined
    ? { messages: [message], next_cursor: 10 }
    : { messages: [{ ...message, id: "q2", question: "Earlier question" }], next_cursor: null });
  render(<FolderConversations folder="work" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByText("Load older conversations"));
  expect(await screen.findByText("Earlier question", { selector: ".rd-conversation-preview" })).toBeVisible();
  expect(document.querySelectorAll(".rd-conversation")).toHaveLength(2);
  expect(api.folderConversations).toHaveBeenCalledWith("work", 10);
});

it("ignores a late response after switching folders", async () => {
  let resolve!: (value: Awaited<ReturnType<typeof api.folderConversations>>) => void;
  vi.mocked(api.folderConversations).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
  vi.mocked(api.folderConversations).mockResolvedValue({ messages: [], next_cursor: null });
  const view = render(<FolderConversations key="a" folder="a" onClose={vi.fn()} />);
  view.rerender(<FolderConversations key="b" folder="b" onClose={vi.fn()} />);
  await screen.findByText(/No conversations yet/);
  resolve({ messages: [message], next_cursor: null });
  await waitFor(() => expect(screen.queryByText("API agent")).toBeNull());
});
