import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { sessionRef } from "./hostTransport";
import { SessionView } from "./types";
import { useSessionSelection } from "./useSessionSelection";

const remoteKey = sessionRef("qa-remote", "same");
const local: SessionView = { key: "same", label: "Local", state: "busy", lastEventType: "SessionStart", startedAt: 1, updatedAt: 1, eventCount: 1 };
const remote: SessionView = { ...local, key: remoteKey, host: "qa-remote", label: "Remote", group: "Projects" };
const mount = (rows: SessionView[], loadedHosts: string[]) => renderHook(
  props => useSessionSelection(props.rows, props.rows[0]?.key ?? null, props.loadedHosts),
  { initialProps: { rows, loadedHosts } },
);
beforeEach(() => {
  window.__rubbertermDesktop = { currentTarget: "local", targets: [{id:"local",name:"This Mac"},{id:"qa-remote",name:"Remote"}] };
});
afterEach(() => { cleanup(); localStorage.clear(); delete window.__rubbertermDesktop; vi.restoreAllMocks(); });

it("restores a remote choice across remount and local-first offline startup with colliding IDs", () => {
  const first = mount([local, remote], ["local", "qa-remote"]);
  act(() => first.result.current.selectSession(remoteKey));
  expect(JSON.parse(localStorage.getItem("rd.selectedSession")!)).toEqual({host:"qa-remote",sessionKey:"same"});
  first.unmount();
  const restored = mount([], []);
  expect(restored.result.current.selectedKey).toBe(remoteKey);
  restored.rerender({ rows:[local], loadedHosts:["local"] });
  expect(restored.result.current.selectedKey).toBe(remoteKey);
  // More local refreshes must not invalidate an offline remote selection.
  restored.rerender({ rows:[{...local,updatedAt:100}], loadedHosts:["local"] });
  expect(restored.result.current.selectedKey).toBe(remoteKey);
  const reveal = vi.fn(); window.addEventListener("reveal-sidebar-folder", reveal);
  restored.rerender({ rows:[local,remote], loadedHosts:["local","qa-remote"] });
  expect(restored.result.current.selectedKey).toBe(remoteKey);
  expect(reveal).not.toHaveBeenCalled();
  window.removeEventListener("reveal-sidebar-folder", reveal);
});

it("restores a nested local selection without revealing its collapsed folders", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"local",sessionKey:"same"}));
  const reveal = vi.fn(); window.addEventListener("reveal-sidebar-folder", reveal);
  try {
    const view = mount([], []);
    view.rerender({rows:[{...local,group:"Projects/Child"}],loadedHosts:["local"]});
    expect(view.result.current.selectedKey).toBe(local.key);
    expect(reveal).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("reveal-sidebar-folder", reveal);
  }
});

it("lets an explicit local choice cancel pending remote restoration", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"qa-remote",sessionKey:"same"}));
  const view = mount([local], ["local"]);
  act(() => view.result.current.selectSession(local.key));
  view.rerender({rows:[local,remote],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(local.key);
  expect(JSON.parse(localStorage.getItem("rd.selectedSession")!)).toEqual({host:"local",sessionKey:"same"});
});

it("falls back only once the selected host confirms the saved session is absent", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"qa-remote",sessionKey:"gone"}));
  const view = mount([local], ["local"]);
  expect(view.result.current.selectedKey).toBe(sessionRef("qa-remote","gone"));
  view.rerender({rows:[local],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(local.key);
});

it("waits for a saved local row even if a remote snapshot arrives first", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"local",sessionKey:"same"}));
  const view = mount([remote], ["qa-remote"]);
  expect(view.result.current.selectedKey).toBe(local.key);
  view.rerender({rows:[remote,local],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(local.key);
});

it("does not lose a just-launched choice to a previous snapshot and permits deleting it", () => {
  const view = mount([local], ["local","qa-remote"]);
  act(() => view.result.current.selectSession(remoteKey));
  expect(view.result.current.selectedKey).toBe(remoteKey);
  view.rerender({rows:[local,remote],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(remoteKey);
  act(() => view.result.current.selectSession(null));
  view.rerender({rows:[local],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(local.key);
});

it("falls back when the saved host was removed and ignores malformed storage", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"removed",sessionKey:"same"}));
  const view = mount([local], ["local"]);
  expect(view.result.current.selectedKey).toBe(local.key);
  view.unmount();
  localStorage.setItem("rd.selectedSession", "{broken");
  expect(mount([local],["local"]).result.current.selectedKey).toBe(local.key);
});

it("explicit native navigation overrides the saved selection", () => {
  localStorage.setItem("rd.selectedSession", JSON.stringify({host:"local",sessionKey:"same"}));
  window.__rubbertermDesktop!.selectedSession = remoteKey;
  const view = mount([local], ["local"]);
  expect(view.result.current.selectedKey).toBe(remoteKey);
  view.rerender({rows:[local,remote],loadedHosts:["local","qa-remote"]});
  expect(view.result.current.selectedKey).toBe(remoteKey);
});
