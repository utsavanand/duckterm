import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentsMdModal } from "./AgentsMdModal";
import { sessionFetch } from "./hostTransport";
import { edition, CODE, FULL, LITE } from "./test/editions";

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

it.each([
  ["Lite", LITE, false], ["Code", CODE, true], ["Full", FULL, true],
  ["Code without context", CODE.filter(feature => feature !== "context"), true],
  ["Full without suggestions", FULL.filter(feature => feature !== "rule_suggestions"), false],
] as const)("%s gates generated suggestions separately from manual editing and context", async (_name, features, enabled) => {
  vi.mocked(sessionFetch).mockResolvedValueOnce(loaded()).mockResolvedValue(Response.json({ suggestions: [], corrections_seen: 0 }));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />, { wrapper: edition(features) }); });
  expect(screen.getByPlaceholderText(/New rule/)).toBeEnabled();
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  const suggest = screen.queryByRole("button", { name: "Suggest from corrections" });
  if (enabled) {
    expect(suggest).toBeEnabled();
    await act(async () => { fireEvent.click(suggest!); });
    expect(sessionFetch).toHaveBeenCalledWith("", "/agents-md/suggest", expect.objectContaining({ method: "POST", body: JSON.stringify({ dir: "/project" }) }));
  } else {
    expect(suggest).toBeNull();
    expect(sessionFetch).toHaveBeenCalledExactlyOnceWith("", "/agents-md?dir=%2Fproject");
  }
});

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
