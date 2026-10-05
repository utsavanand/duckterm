import { useEffect, useMemo, useReducer, useState } from "react";
import { applyEvent } from "./sessions";
import { desktop } from "./desktop";
import { hostFetch, remoteGroups } from "./hostTransport";
import {
  PersistedSession,
  DucktermEvent,
  sessionKeyOf,
  SessionView,
  viewFromPersisted,
} from "./types";

type InitFrame = { type: "init"; events: DucktermEvent[] };

export type Action =
  | { kind: "remote-snapshot"; host: string; label: string; sessions: PersistedSession[]; groups: Record<string, string> }
  | { kind: "remote-offline"; host: string }
  | { kind: "remote-hosts"; hosts: string[] }
  | { kind: "seed"; sessions: PersistedSession[] }
  | { kind: "event"; event: DucktermEvent; replay?: boolean; receivedAt?: number }
  | { kind: "remove"; keys: string[] }
  | { kind: "patch"; key: string; fields: Partial<SessionView> };

// The reducer tracks deleted keys so a still-firing watched session (whose hooks
// keep streaming events) can't resurrect a row the user just deleted — mirrors
// the server's tombstone. A fresh SessionStart lifts the tombstone.
export interface State {
  sessions: Map<string, SessionView>;
  tombstoned: Set<string>;
}

function isInit(data: unknown): data is InitFrame {
  return (
    typeof data === "object" &&
    data !== null &&
    (data as InitFrame).type === "init"
  );
}

function mergeDefined(base: SessionView, over: SessionView): SessionView {
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) {
    if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

function withoutDeletedParent(session: SessionView, tombstoned: Set<string>): SessionView {
  return session.parentKey && tombstoned.has(session.parentKey)
    ? { ...session, parentKey: null } : session;
}

// Exported for unit tests — this is the pure heart of the event stream (seed,
// event-merge, remove/tombstone, optimistic patch), independent of React.
export function reduce(state: State, action: Action): State {
  if (action.kind === "remote-hosts") {
    return { ...state, sessions: new Map([...state.sessions].filter(([, s]) => !s.host || action.hosts.includes(s.host))) };
  }
  if (action.kind === "remote-offline") {
    return { ...state, sessions: new Map([...state.sessions].map(([key, s]) => [key, s.host === action.host ? { ...s, hostOffline: true } : s])) };
  }
  if (action.kind === "remote-snapshot") {
    const next = new Map([...state.sessions].filter(([, s]) => s.host !== action.host));
    for (const row of action.sessions) {
      if (state.tombstoned.has(row.session_key)) continue;
      next.set(row.session_key, withoutDeletedParent({ ...viewFromPersisted(row), host: action.host, hostLabel: action.label,
        hostOffline: false, group: action.groups[row.session_key] ?? row.grp ?? undefined }, state.tombstoned));
    }
    return { ...state, sessions: next };
  }
  if (action.kind === "seed") {
    // A seed only lists live sessions; anything we'd tombstoned that the server
    // confirms exists again can drop its tombstone.
    const tombstoned = new Set(state.tombstoned);
    for (const s of action.sessions) tombstoned.delete(s.session_key);
    const next = new Map(state.sessions);
    // Pins are server-owned metadata, including deletion in another window.
    for (const [key, session] of next) next.set(key, { ...session, pinned: false });
    for (const s of action.sessions) {
      const persisted = viewFromPersisted(s);
      const live = next.get(s.session_key);
      // Persisted is the base (it carries identity/metadata a live event can't:
      // repoName, worktreePath, notes, intention). Then layer the live view's
      // DEFINED fields on top so dynamic state (state, eventCount) wins
      // without undefined live fields clobbering persisted ones.
      const merged = live ? mergeDefined(persisted, live) : persisted;
      // A saved rename (sessions.name) is authoritative over whatever label
      // the live view derived from replayed events — without this, the stale
      // launch-time name resurfaced on every restart.
      if (s.name) merged.label = persisted.label;
      // Identity flags come from the DB, full stop. A replayed HOOK event
      // (no launched marker) writes a DEFINED false into the live view, and
      // a defined field wins the merge — flipping an owned session to
      // "watched" and hiding its terminal until a full reload.
      merged.launched = persisted.launched;
      merged.ptyOwned = persisted.ptyOwned;
      merged.pinned = persisted.pinned;
      // Active lineage is DB-owned. Historical fork events retain provenance,
      // but cannot override a parent that was deleted while this page was away.
      merged.parentKey = persisted.parentKey;
      next.set(s.session_key, withoutDeletedParent(merged, tombstoned));
    }
    return { sessions: next, tombstoned };
  }
  if (action.kind === "remove") {
    const next = new Map(state.sessions);
    const tombstoned = new Set(state.tombstoned);
    for (const key of action.keys) {
      next.delete(key);
      tombstoned.add(key);
    }
    for (const [key, session] of next) {
      next.set(key, withoutDeletedParent(session, tombstoned));
    }
    return { sessions: next, tombstoned };
  }
  // Optimistic local update for a metadata change (e.g. moving to a folder) that
  // a PATCH made server-side but doesn't emit over SSE.
  if (action.kind === "patch") {
    const prev = state.sessions.get(action.key);
    if (!prev) return state;
    const next = new Map(state.sessions);
    next.set(action.key, { ...prev, ...action.fields });
    return { ...state, sessions: next };
  }
  // A live event for a tombstoned (deleted) session must not resurrect it —
  // unless it's a SessionStart, which means the key is genuinely a new session.
  const key = sessionKeyOf(action.event);
  let tombstoned = state.tombstoned;
  if (key && tombstoned.has(key)) {
    if (action.event.event_type !== "SessionStart") return state;
    tombstoned = new Set(tombstoned);
    tombstoned.delete(key);
  }
  const sessions = applyEvent(state.sessions, action.event);
  const previous = key ? state.sessions.get(key) : undefined;
  const current = key ? sessions.get(key) : undefined;
  if (previous && current) {
    // Stop completes a turn immediately even though the displayed busy label
    // has a settling grace. Celebrate the witnessed turn transition once,
    // never historical SSE replay, seeds, or the later grace-period expiry.
    const activity = (session: SessionView) => session.state === "busy" && session.idleSince !== undefined ? "idle" : session.state;
    const before = activity(previous);
    const after = activity(current);
    if (!action.replay && before === "busy" && (after === "idle" || after === "waiting") && action.event._ts >= previous.updatedAt) {
      current.celebration = { kind: after === "idle" ? "done" : "ready", startedAt: action.receivedAt ?? action.event._ts };
    } else if (after === "busy" || action.replay) {
      current.celebration = undefined;
    }
  }
  if (key && current) sessions.set(key, withoutDeletedParent(current, tombstoned));
  return { sessions, tombstoned };
}

export function useEventStream(): {
  sessions: SessionView[];
  connected: boolean;
  loadedHosts: string[];
  recentEvents: DucktermEvent[];
  removeSessions: (keys: string[]) => void;
  patchSession: (key: string, fields: Partial<SessionView>) => void;
} {
  const [state, dispatch] = useReducer(reduce, {
    sessions: new Map<string, SessionView>(),
    tombstoned: new Set<string>(),
  });
  const [connected, setConnected] = useState(false);
  const [loadedHosts, setLoadedHosts] = useState<string[]>([]);
  // Rolling buffer of the newest events, newest first (live activity feed).
  const [recentEvents, setRecentEvents] = useState<DucktermEvent[]>([]);

  useEffect(() => {
    let dispose = () => {};
    const connectHosts = () => {
      dispose();
      let stopped = false;
      const hosts = desktop()?.targets.filter(t => t.id !== "local") ?? [];
      dispatch({ kind: "remote-hosts", hosts: hosts.map(h => h.id) });
      const timers = new Map<string, ReturnType<typeof setTimeout>>();
      const busy = new Set<string>();
      const refresh = (host: typeof hosts[number]) => {
        if (stopped || busy.has(host.id)) return;
        clearTimeout(timers.get(host.id));
        busy.add(host.id);
        void hostFetch(host.id, "/sessions").then(async response => {
          if (!response.ok) throw new Error("Remote unavailable");
          const data = await response.json() as { sessions: PersistedSession[]; collaboration_enabled?: boolean };
          if (!stopped) {
            dispatch({ kind: "remote-snapshot", host: host.id, label: host.name.replace(/^Remote — /, ""), sessions: data.sessions, groups: data.collaboration_enabled ? {} : remoteGroups() });
            setLoadedHosts(previous => previous.includes(host.id) ? previous : [...previous, host.id]);
          }
        }).catch(() => { if (!stopped) dispatch({ kind: "remote-offline", host: host.id }); })
          .finally(() => {
            busy.delete(host.id);
            if (!stopped) timers.set(host.id, setTimeout(() => refresh(host), 2500));
          });
      };
      const refreshAll = () => hosts.forEach(refresh);
      refreshAll();
      window.addEventListener("remote-sessions-refresh", refreshAll);
      dispose = () => {
        stopped = true;
        timers.forEach(clearTimeout);
        window.removeEventListener("remote-sessions-refresh", refreshAll);
      };
    };
    connectHosts();
    window.addEventListener("desktop-targets-changed", connectHosts);
    return () => { dispose(); window.removeEventListener("desktop-targets-changed", connectHosts); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const seed = () =>
      fetch("/sessions")
        .then((r) => { if (!r.ok) throw new Error("Local sessions unavailable"); return r.json(); })
        .then((data: { sessions: PersistedSession[] }) => {
          if (!cancelled) {
            dispatch({ kind: "seed", sessions: data.sessions });
            setLoadedHosts(previous => previous.includes("local") ? previous : [...previous, "local"]);
          }
        })
        .catch(() => undefined);
    seed();
    // Light periodic re-seed: context_tokens (and other server-computed
    // fields) change as the agent works but emit no SSE event of their own.
    const t = setInterval(seed, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    const source = new EventSource("/stream");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (msg) => {
      const data: unknown = JSON.parse(msg.data);
      if (isInit(data)) {
        data.events.forEach((event) => dispatch({ kind: "event", event, replay: true }));
        setRecentEvents((prev) =>
          [...data.events].reverse().concat(prev).slice(0, 100),
        );
      } else {
        const event = data as DucktermEvent;
        dispatch({ kind: "event", event, receivedAt: Date.now() });
        setRecentEvents((prev) => [event, ...prev].slice(0, 100));
        // Sub-agents are embedded in the /sessions payload, not folded in by
        // applyEvent (which only tracks SessionStart). A sub-agent lifecycle
        // event is infrequent, so just re-seed sessions to refresh the tree.
        if (
          event.event_type === "SubagentStart" ||
          event.event_type === "SubagentStop"
        ) {
          fetch("/sessions")
            .then((r) => r.json())
            .then((d: { sessions: PersistedSession[] }) =>
              dispatch({ kind: "seed", sessions: d.sessions }),
            )
            .catch(() => undefined);
        }
      }
    };
    return () => source.close();
  }, []);

  // Stable order: newest session first by START time, which never changes —
  // so cards don't reshuffle (and buttons don't move) as events stream in.
  const list = useMemo(() => [...state.sessions.values()].sort(
    (a, b) => b.startedAt - a.startedAt || a.key.localeCompare(b.key),
  ), [state.sessions]);
  const removeSessions = (keys: string[]) => dispatch({ kind: "remove", keys });
  const patchSession = (key: string, fields: Partial<SessionView>) =>
    dispatch({ kind: "patch", key, fields });
  return {
    sessions: list,
    connected,
    loadedHosts,
    recentEvents,
    removeSessions,
    patchSession,
  };
}
