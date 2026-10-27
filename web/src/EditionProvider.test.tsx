import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { allowRemoteWorkspaces, desktop } from "./desktop";
import { EditionProvider, useEdition } from "./edition";
import { sessionRef } from "./hostTransport";
import { SessionView } from "./types";
import { useSessionSelection } from "./useSessionSelection";
import { CODE, FULL, LITE } from "./test/editions";

function Probe() {
  const { has, id } = useEdition();
  return <p>{id ?? "none"} oracle:{String(has("oracle"))} explorer:{String(has("explorer"))}</p>;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); allowRemoteWorkspaces(null); delete window.__rubbertermDesktop; localStorage.clear(); });

it("retries an unavailable description and enables features only once one arrives", async () => {
  const edition = vi.spyOn(api, "edition").mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValue({ id: "full", features: FULL });
  const errors: string[] = [];
  render(<EditionProvider onError={message => errors.push(message)}><Probe /></EditionProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("none oracle:false explorer:false")).toBeInTheDocument();
  expect(errors).toEqual(["Unavailable"]);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(edition).toHaveBeenCalledTimes(2);
  expect(screen.getByText("full oracle:true explorer:true")).toBeInTheDocument();
});

it("hides other computers until the edition includes remote workspaces", async () => {
  window.__rubbertermDesktop = { currentTarget: "dev", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }] };
  const changed = vi.fn();
  window.addEventListener("desktop-targets-changed", changed);
  vi.spyOn(api, "edition").mockResolvedValue({ id: "lite", features: LITE });
  render(<EditionProvider><Probe /></EditionProvider>);
  expect(desktop()?.targets.map(t => t.id)).toEqual(["local"]);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(desktop()).toMatchObject({ currentTarget: "local", targets: [{ id: "local" }] });
  allowRemoteWorkspaces(true);
  expect(desktop()?.targets.map(t => t.id)).toEqual(["local", "dev"]);
  expect(changed).toHaveBeenCalledTimes(2);
  window.removeEventListener("desktop-targets-changed", changed);
});

it("keeps a saved remote selection while its computer is hidden, and restores it when allowed", () => {
  window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }] };
  const remote = sessionRef("dev", "remote-one");
  localStorage.setItem("rd.selectedSession", JSON.stringify({ host: "dev", sessionKey: "remote-one" }));
  const local = { key: "local-one", state: "idle" } as SessionView;
  const view = renderHook(({ rows }) => useSessionSelection(rows, "local-one", ["local"]), { initialProps: { rows: [local] } });
  expect(view.result.current.selectedKey).toBe(remote); // edition still loading: wait
  act(() => allowRemoteWorkspaces(false));
  expect(view.result.current.selectedKey).toBe("local-one");
  expect(JSON.parse(localStorage.getItem("rd.selectedSession")!)).toEqual({ host: "dev", sessionKey: "remote-one" });
  act(() => allowRemoteWorkspaces(true));
  view.rerender({ rows: [local, { key: remote, state: "idle" } as SessionView] });
  expect(view.result.current.selectedKey).toBe(remote);
});

it.each([
  ["an unknown id", { id: "enterprise", features: ["oracle"] }],
  ["features that are not strings", { id: "full", features: [1] }],
  ["no features", { id: "full" }],
])("treats a description with %s as unavailable, not as a crash or Full", async (_name, body) => {
  vi.spyOn(api, "edition").mockResolvedValue(body as never);
  const errors: string[] = [];
  render(<EditionProvider onError={message => errors.push(message)}><Probe /></EditionProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("none oracle:false explorer:false")).toBeInTheDocument();
  expect(errors).toEqual(["Malformed edition description"]);
});

it("rereads the edition on focus, following a server restarted in another edition and dropping features on failure", async () => {
  const edition = vi.spyOn(api, "edition").mockResolvedValueOnce({ id: "full", features: FULL })
    .mockResolvedValueOnce({ id: "code", features: CODE }).mockRejectedValueOnce(new Error("Unavailable"));
  render(<EditionProvider><Probe /></EditionProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("full oracle:true explorer:true")).toBeInTheDocument();
  await act(async () => { window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("code oracle:false explorer:true")).toBeInTheDocument();
  await act(async () => { window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("none oracle:false explorer:false")).toBeInTheDocument();
  expect(edition).toHaveBeenCalledTimes(3);
});
