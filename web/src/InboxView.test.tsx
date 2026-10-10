import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InboxView } from "./InboxView";
import { api, InboxMessage, InboxPage } from "./api";
import { SessionView } from "./types";

vi.mock("./api", () => ({ api: { inbox: vi.fn(), folderInbox: vi.fn(), introduceCollaboration: vi.fn(), collaborationInstructions: vi.fn() } }));
const session = { key: "b", label: "Billing" } as SessionView;
const message: InboxMessage = {
  id: "q-1", sender: "a", recipient: "b", sender_name: "Client implementation",
  question: "What token refresh contract should I use?",
  status: "answered", answer: "Retry once.\nPreserve the request ID.",
  created_at: 1000000, expires_at: 1300000, answered_at: 1100000,
};

beforeEach(() => {
  // jsdom has dialog elements but no native modal methods; browser tests cover focus/Escape.
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); });

describe("session inbox", () => {
  it("can show a pasteable introduction without sending terminal input", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    vi.mocked(api.collaborationInstructions).mockResolvedValue({ prompt: "Read collaboration.md" });
    render(<InboxView session={{ ...session, runtime: "codex", state: "busy" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Session tools & help" }));
    fireEvent.click(screen.getByText("Show introduction to paste"));
    expect(await screen.findByLabelText("Introduction to paste into the agent")).toHaveValue("Read collaboration.md");
    expect(api.introduceCollaboration).not.toHaveBeenCalled();
  });
  it("sends an introduction only on request and distinguishes sent from read", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [], next_cursor: null });
    vi.mocked(api.introduceCollaboration).mockResolvedValue({ sent: true });
    render(<InboxView session={{ ...session, runtime: "codex", state: "idle" }} />);
    await screen.findByText("No messages yet");
    fireEvent.click(screen.getByRole("button", { name: "Session tools & help" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Session tools & help" }));
    expect(screen.getByRole("button", { name: "Introduce session collaboration" })).toBeDisabled();
    view.rerender(<InboxView session={{ ...session, runtime: "codex", state: "idle" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Introduce session collaboration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Terminal unavailable");
  });
  it("shows who sent a question, its status, and the complete answer", async () => {
    vi.mocked(api.inbox).mockResolvedValue({ messages: [message], next_cursor: null });
    render(<InboxView session={session} />);
    const sender = await screen.findByText("Client implementation");
    expect(screen.getByText("Answered", { selector: ".rd-inbox-status" })).toBeVisible();
    fireEvent.click(sender.closest("summary")!);
    expect(screen.getByText("From session: a")).toBeVisible();
    expect(screen.getByText(/Retry once/).textContent).toBe(message.answer);
    expect(api.inbox).toHaveBeenCalledWith("b", undefined, "all");
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
    expect(api.inbox).toHaveBeenCalledWith("b", 12, "all");
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

it("labels authenticated owner notices without mistaking a peer named You for the owner", async () => {
  vi.mocked(api.inbox).mockResolvedValue({ messages: [
    { ...message, id: "owner", kind: "broadcast", sender_kind: "owner", requires_reply: false, status: "queued", answer: null, delivery: { outcome: "notified" } },
    { ...message, id: "peer", sender_name: "You", sender_kind: "session" },
  ], next_cursor: null });
  render(<InboxView session={session} />);
  expect(await screen.findByText("Owner")).toBeVisible();
  expect(screen.getAllByText("Owner")).toHaveLength(1);
  expect(screen.getByText("Unread")).toBeVisible();
  fireEvent.click(screen.getByText("Owner").closest("summary")!);
  expect(screen.getByText("Notice shown · Not yet read")).toBeVisible();
  expect(screen.getByText("No reply required")).toBeVisible();
});


it("filters only reply-required work and searches loaded replies without treating user text as HTML", async () => {
  vi.mocked(api.inbox).mockResolvedValue({ messages: [
    message,
    { ...message, id: "pending", sender_name: "Pending sender", status: "queued", answer: null },
    { ...message, id: "notice", sender_name: "Notice sender", status: "queued", kind: "broadcast", requires_reply: false, answer: null },
    { ...message, id: "accepted", sender_name: "Accepted sender", status: "accepted", answer: null },
    { ...message, id: "expired", sender_name: "Expired sender", status: "expired", answer: null },
    { ...message, id: "literal", sender_name: "Literal sender", question: '<img src=x onerror="alert(1)">', answer: null },
  ], next_cursor: null });
  const { container } = render(<InboxView session={session} />);
  await screen.findByText("Client implementation");
  fireEvent.click(screen.getByRole("button", { name: "Awaiting reply 2" }));
  expect(screen.getByText("Pending sender")).toBeVisible();
  expect(screen.getByText("Accepted sender")).toBeVisible();
  expect(screen.queryByText("Notice sender")).toBeNull();
  expect(screen.queryByText("Expired sender")).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "All 6" }));
  await screen.findByRole("button", { name: "All 6" });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "PRESERVE the request" } });
  expect(screen.getByText("Client implementation")).toBeVisible();
  expect(screen.queryByText("Pending sender")).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "no match" } });
  expect(screen.getByText("No messages match this view")).toBeVisible();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "<img" } });
  expect(screen.getByText("Literal sender")).toBeVisible();
  expect(container.querySelector("img")).toBeNull();
});

it.each([false, true])("finds older open work using the server view (folder=%s) and resets its cursor", async (folder) => {
  const totals = { all: 104, pending: 2, answered: 102 };
  const old = { ...message, id: "old-open", sender_name: "Older open request", status: "accepted" as const, answer: null };
  const fetchPage = vi.fn(async (_key: string, before?: number, view?: string) => view === "pending"
    ? { messages: [old], next_cursor: null, counts: totals }
    : { messages: [{ ...message, id: before ? "page-two" : "newest" }], next_cursor: before ? null : 75, counts: totals });
  vi.mocked(api.inbox).mockImplementation(fetchPage);
  vi.mocked(api.folderInbox).mockImplementation(fetchPage);
  render(folder ? <InboxView folder="work" /> : <InboxView session={session} />);
  fireEvent.click(await screen.findByText("Load older messages"));
  await screen.findByText("2 of 2 loaded messages");
  fireEvent.click(screen.getByRole("button", { name: "Awaiting reply 2" }));
  expect(await screen.findByText("Older open request")).toBeVisible();
  expect(fetchPage).toHaveBeenLastCalledWith(folder ? "work" : "b", undefined, "pending");
  expect(screen.getByRole("button", { name: "All 104" })).toBeVisible();
  expect(screen.queryByText("Load older messages")).toBeNull();
});

it("keeps read priority notices awaiting reply without claiming they were unread", async () => {
  vi.mocked(api.inbox).mockResolvedValue({ messages: [
    { ...message, id: "priority", kind: "broadcast", requires_reply: true, status: "read", answer: null, delivery: { last_read_at: 1000001 } },
    { ...message, id: "notice", sender_name: "Regular notice", kind: "broadcast", requires_reply: false, status: "read", answer: null },
  ], next_cursor: null });
  render(<InboxView session={session} />);
  fireEvent.click(await screen.findByRole("button", { name: "Awaiting reply 1" }));
  await screen.findByRole("button", { name: "Awaiting reply 1" });
  expect(screen.getByText("Awaiting reply", { selector: ".rd-inbox-status" })).toBeVisible();
  expect(screen.queryByText("Regular notice")).toBeNull();
  fireEvent.click(screen.getByText("Client implementation").closest("summary")!);
  expect(screen.getByText("Read by session")).toBeVisible();
  expect(screen.queryByText("Unread")).toBeNull();
});

it("ignores an in-flight older view response when changing filters", async () => {
  let resolve!: (value: InboxPage) => void;
  vi.mocked(api.inbox).mockResolvedValueOnce({ messages: [message], next_cursor: 10, counts: { all: 51, pending: 1, answered: 50 } });
  render(<InboxView session={session} />);
  await screen.findByText("Load older messages");
  vi.mocked(api.inbox).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByText("Load older messages"));
  const old = { ...message, id: "old-open", sender_name: "Older open request", status: "queued" as const, answer: null };
  vi.mocked(api.inbox).mockResolvedValue({ messages: [old], next_cursor: null });
  fireEvent.click(screen.getByRole("button", { name: "Awaiting reply 1" }));
  await screen.findByText("Older open request");
  resolve({ messages: [{ ...message, sender_name: "Late stale history" }], next_cursor: null });
  await waitFor(() => expect(screen.queryByText("Late stale history")).toBeNull());
  expect(screen.getByText("Older open request")).toBeVisible();
});
