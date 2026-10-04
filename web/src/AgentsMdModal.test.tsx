import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentsMdModal } from "./AgentsMdModal";
import { sessionFetch } from "./hostTransport";

const toast = vi.hoisted(() => vi.fn());
vi.mock("./hostTransport", () => ({ sessionFetch: vi.fn() }));
vi.mock("./ui", async (original) => ({
  ...await original<typeof import("./ui")>(),
  useToast: () => toast,
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}
const loaded = () => Response.json({ rules: [], managed: true });

it("waits for existing rules before adding by click or Enter, then saves the added rule", async () => {
  const load = deferred();
  vi.mocked(sessionFetch).mockReturnValueOnce(load.promise).mockResolvedValue(Response.json({ ok: true }));
  const close = vi.fn();
  render(<AgentsMdModal dir="/project" onClose={close} />);
  const draft = screen.getByPlaceholderText(/New rule/);
  // A fast user can type before the initial GET resolves.
  fireEvent.change(draft, { target: { value: "Be brief." } });
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  fireEvent.keyDown(draft, { key: "Enter" });
  expect(document.querySelector("textarea.rd-rule-edit")).toBeNull();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await act(async () => { load.resolve(loaded()); });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(screen.getByDisplayValue("Be brief.")).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
  const posted = JSON.parse(vi.mocked(sessionFetch).mock.calls[1][2]!.body as string);
  expect(posted.rules).toEqual([expect.objectContaining({ text: "Be brief.", status: "active" })]);
  expect(close).toHaveBeenCalledOnce();
});

it.each(["http", "network"])("keeps saving disabled after a %s load failure", async (kind) => {
  if (kind === "http") vi.mocked(sessionFetch).mockResolvedValue(Response.json({ error: "Denied" }, { status: 403 }));
  else vi.mocked(sessionFetch).mockRejectedValue(new Error("Offline"));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />); });
  fireEvent.change(screen.getByPlaceholderText(/New rule/), { target: { value: "Be brief." } });
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(toast).toHaveBeenCalledWith(expect.stringContaining("Load failed"), "err");
  expect(sessionFetch).toHaveBeenCalledTimes(1);
});

it("ignores a stale response from the previous folder", async () => {
  const first = deferred();
  vi.mocked(sessionFetch).mockReturnValueOnce(first.promise).mockResolvedValueOnce(loaded()).mockResolvedValue(Response.json({ ok: true }));
  const close = vi.fn();
  const view = render(<AgentsMdModal dir="/old" onClose={close} />);
  await act(async () => { view.rerender(<AgentsMdModal dir="/new" onClose={close} />); });
  fireEvent.change(screen.getByPlaceholderText(/New rule/), { target: { value: "Keep this." } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await act(async () => { first.resolve(loaded()); });
  expect(screen.getByDisplayValue("Keep this.")).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
  const posted = JSON.parse(vi.mocked(sessionFetch).mock.calls[2][2]!.body as string);
  expect(posted.dir).toBe("/new");
  expect(posted.rules).toEqual([expect.objectContaining({ text: "Keep this." })]);
});
