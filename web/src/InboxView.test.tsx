import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InboxView } from "./InboxView";
import { api, InboxMessage, InboxPage } from "./api";
import { SessionView } from "./types";

vi.mock("./api", () => ({ api: { inbox: vi.fn(), introduceCollaboration: vi.fn(), collaborationInstructions: vi.fn() } }));
const session = { key: "b", label: "Billing" } as SessionView;
const message: InboxMessage = {
  id: "q-1", sender: "a", recipient: "b", sender_name: "Client implementation",
  question: "What token refresh contract should I use?",
  status: "answered", answer: "Retry once.\nPreserve the request ID.",
  created_at: 1000000, expires_at: 1300000, answered_at: 1100000,
};

afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe("session inbox", () => {
  it("can show a pasteable introduction without sending terminal input", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    vi.mocked(api.collaborationInstructions).mockResolvedValue({ prompt: "Read collaboration.md" });
    render(<InboxView session={{ ...session, runtime: "codex", state: "busy" }} />);
    fireEvent.click(screen.getByText("Show introduction to paste"));
    expect(await screen.findByLabelText("Introduction to paste into the agent")).toHaveValue("Read collaboration.md");
    expect(api.introduceCollaboration).not.toHaveBeenCalled();
  });
  it("sends an introduction only on request and distinguishes sent from read", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    vi.mocked(api.introduceCollaboration).mockResolvedValue({ sent: true });
    render(<InboxView session={{ ...session, runtime: "codex", state: "idle" }} />);
    await screen.findByText("No messages yet");
    expect(api.introduceCollaboration).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Introduce session collaboration" }));
    expect(await screen.findByText(/does not confirm the agent has read/)).toBeVisible();
    expect(api.introduceCollaboration).toHaveBeenCalledWith("b");
    expect(screen.getByRole("button", { name: "Introduction sent" })).toBeDisabled();
  });

  it("disables introduction while busy and shows delivery errors", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    vi.mocked(api.introduceCollaboration).mockRejectedValue(new Error("Terminal unavailable"));
    const view = render(<InboxView session={{ ...session, runtime: "codex", state: "busy" }} />);
    expect(screen.getByRole("button", { name: "Introduce session collaboration" })).toBeDisabled();
    view.rerender(<InboxView session={{ ...session, runtime: "codex", state: "idle" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Introduce session collaboration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Terminal unavailable");
  });
  it("shows who sent a question, its status, and the complete answer", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [message], next_cursor: null });
    render(<InboxView session={session} />);
    const sender = await screen.findByText("Client implementation");
    expect(screen.getByText("Answered")).toBeVisible();
    fireEvent.click(sender.closest("summary")!);
    expect(screen.getByText("From session: a")).toBeVisible();
    expect(screen.getByText(/Retry once/).textContent).toBe(message.answer);
    expect(api.inbox).toHaveBeenCalledWith("b", undefined);
  });

  it("distinguishes an empty inbox from an API failure and supports retry", async () => {
    vi.mocked(api.inbox).mockRejectedValueOnce(new Error("offline"));
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    render(<InboxView session={session} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(screen.queryByText("No messages yet")).toBeNull();
    fireEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText("No messages yet")).toBeVisible();
  });

  it("loads older messages without losing the latest page", async () => {
    vi.mocked(api.inbox).mockImplementation(async (_key, before) => before === undefined
      ? { messages: [message], next_cursor: 12 }
      : { messages: [{ ...message, id: "q-older", sender_name: "Older sender" }], next_cursor: null });
    render(<InboxView session={session} />);
    fireEvent.click(await screen.findByText("Load older messages"));
    expect(await screen.findByText("Older sender")).toBeVisible();
    expect(screen.getByText("Client implementation")).toBeVisible();
    expect(api.inbox).toHaveBeenCalledWith("b", 12);
  });

  it("does not leak a late response into the next selected session", async () => {
    let resolve!: (value: InboxPage) => void;
    vi.mocked(api.inbox).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    const view = render(<InboxView key="b" session={session} />);
    view.rerender(<InboxView key="c" session={{ ...session, key: "c" }} />);
    await screen.findByText("No messages yet");
    resolve({ messages: [message], next_cursor: null });
    await waitFor(() => expect(screen.queryByText("Client implementation")).toBeNull());
  });
});
