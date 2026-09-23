// Thin wrapper over the Duckterm server. Every POST action the backend
// exposes lives here so components never hand-roll fetches.

// The per-install secret, injected into index.html by the server. Sent on every
// state-changing request so the server can tell the real dashboard from a
// cross-origin forgery. Read once at module load.
const TOKEN =
  document
    .querySelector('meta[name="duckterm-token"]')
    ?.getAttribute("content") ?? "";

export function authHeaders(extra?: Record<string, string>): HeadersInit {
  return { "X-Duckterm-Token": TOKEN, ...extra };
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body ?? {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (data as { error?: string }).error ?? `${res.status} ${res.statusText}`,
    );
  }
  return data as T;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface LaunchRequest {
  command: string;
  runtime?: "generic" | "claude-code" | "codex";
  repo_path?: string;
  cwd?: string;
  branch?: string;
  base?: string;
  prompt?: string;
  session_key?: string;
  name?: string;
  notes?: string;
  terminal?: string;
  in_terminal?: boolean;
  zsh_theme?: string;
}

export interface Connector {
  managed?: boolean;
  name: string;
  title: string;
  description: string;
  credential: string | null; // "gh-cli" | "stored" | "railway-cli" | null
  installed: Record<string, boolean>; // per harness
  enabled: boolean;
  ready: boolean;
  detail: string | null;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_git: boolean;
}
export interface BrowseResult {
  path: string;
  parent: string | null;
  is_git: boolean;
  entries: BrowseEntry[];
}

export interface InboxMessage {
  id: string;
  sender: string;
  recipient: string;
  recipient_name?: string;
  sender_name: string;
  question: string;
  kind?: "question" | "broadcast";
  sender_kind?: "session" | "owner";
  requires_reply?: boolean;
  delivery?: { outcome?: string; last_read_at?: number };
  status: "read" | "queued" | "accepted" | "answered" | "declined" | "expired" | "cancelled";
  answer: string | null;
  created_at: number;
  expires_at: number;
  answered_at: number | null;
}

export interface SessionCard {
  session_id: string;
  api_name: string;
  name: string;
  purpose: string;
  activity: string;
  state: string;
  folder: string;
  root: string;
  cwd: string | null;
  next_actions: string[];
  deliverables: string[];
  updated_at: number;
}

export interface InboxPage {
  card?: SessionCard | null;
  messages: InboxMessage[];
  next_cursor: number | null;
}

export interface BroadcastTarget {
  session_id: string;
  name: string;
  state: string;
  eligible: boolean;
  reason: string | null;
}
export interface BroadcastResult {
  queued: number;
  skipped: number;
  results: (BroadcastTarget & { status: "queued" | "skipped" })[];
}

export interface BackupState {
  destination: string | null;
  job: {
    id: string;
    status: "running" | "succeeded" | "failed" | "interrupted";
    destination: string;
    started_at: number;
    finished_at: number | null;
    archive_path: string | null;
    result: string | null;
    error: string | null;
  } | null;
}

export const api = {
  backupStatus: async (): Promise<BackupState> => {
    const res = await fetch("/backup", { cache: "no-store", headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Could not load backup status");
    return data;
  },
  startBackup: (destination: string) => post<BackupState>("/backup", { destination }),
  broadcastTargets: async (folder: string): Promise<{ targets: BroadcastTarget[] }> => {
    const res = await fetch(`/folders/${encodeURIComponent(folder)}/broadcast`, { cache: "no-store", headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Could not load recipients");
    return data;
  },
  broadcast: (folder: string, text: string, request_key: string) =>
    post<BroadcastResult>(`/folders/${encodeURIComponent(folder)}/broadcast`, { text, request_key }),
  collaborationInstructions: (key: string) => post<{ prompt: string }>(`/sessions/${encodeURIComponent(key)}/collaboration/instructions`),
  introduceCollaboration: (key: string) => post<{ sent: boolean }>(`/sessions/${encodeURIComponent(key)}/collaboration/introduce`),
  inbox: async (key: string, before?: number): Promise<InboxPage> => {
    const suffix = before === undefined ? "" : `?before=${before}`;
    const res = await fetch(`/sessions/${encodeURIComponent(key)}/inbox${suffix}`, {
      cache: "no-store",
      headers: authHeaders(),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Could not load inbox");
    return data as InboxPage;
  },
  folderInbox: async (folder: string, before?: number): Promise<InboxPage> => {
    const query = new URLSearchParams({ folder });
    if (before !== undefined) query.set("before", String(before));
    const res = await fetch(`/folder-interactions?${query}`, { cache: "no-store", headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Could not load folder interactions");
    return data as InboxPage;
  },
  launch: (req: LaunchRequest) =>
    post<{ session_key: string; opened_in_terminal?: boolean }>(
      "/sessions/launch",
      req,
    ),
  browse: (path?: string) =>
    get<BrowseResult>(
      `/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`,
    ),
  branches: (path: string) =>
    get<{ branches: string[] }>(`/branches?path=${encodeURIComponent(path)}`),
  zshThemes: () => get<{ themes: string[] }>("/zsh-themes"),
  connectors: () => get<{ connectors: Connector[] }>("/connectors"),
  enableConnector: (name: string, token?: string, secret?: string) =>
    post<Connector>(`/connectors/${name}/enable`, {
      ...(token ? { token } : {}),
      ...(secret ? { secret } : {}),
    }),
  disableConnector: (name: string) =>
    post<Connector>(`/connectors/${name}/disable`),
  fleetAsk: (question: string, history: { q: string; a: string }[]) =>
    post<{ answer: string; sessions: string[] }>("/fleet/ask", {
      question,
      history,
    }),
  promote: (key: string, opts: { branch?: string; base?: string }) =>
    post<{ worktree: string; branch: string }>(
      `/sessions/${key}/promote`,
      opts,
    ),
  updateSession: (
    key: string,
    meta: { name?: string; notes?: string; group?: string },
  ) =>
    fetch(`/sessions/${key}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(meta),
    }).then((r) => r.json()),
  // Type a follow-up straight to a live agent's stdin (the terminal path,
  // but from the Messages view — no tab switch to answer or steer).
  sendInput: (key: string, text: string) =>
    post<{ written: boolean }>(`/sessions/${key}/input`, { text }),
  // Move a session into a folder group; "" ungroups it.
  setGroup: (key: string, group: string) =>
    fetch(`/sessions/${key}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ group }),
    }).then(async (r) => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? "Could not move session");
      return data as { updated: boolean };
    }),
  // Installable harnesses (suites of skills/hooks/sub-agents, e.g. uv-suite).
  harnesses: () =>
    get<{
      harnesses: {
        name: string;
        path: string;
        description?: string;
        has_manifest?: boolean;
        compatible?: string[];
        error?: string;
      }[];
    }>("/harnesses"),
  harnessContents: (name: string) =>
    get<{
      compatible: string[];
      contents: { kind: string; name: string; description: string }[];
    }>(`/harnesses/${encodeURIComponent(name)}/contents`),
  registerHarness: (path: string) =>
    post<{ name: string; description: string; path: string }>(
      "/harnesses/register",
      { path },
    ),
  deregisterHarness: (name: string) =>
    fetch(`/harnesses/${encodeURIComponent(name)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }).then((r) => r.json() as Promise<{ removed: boolean }>),
  // Left-panel folders (persist even when empty).
  folders: () => get<{ folders: string[] }>("/folders"),
  createFolder: (name: string) =>
    post<{ created: string }>("/folders", { name }),
  // Re-parent a folder ("" = top level); subfolders + sessions follow.
  moveFolder: (name: string, parent: string) =>
    fetch(`/folders/${encodeURIComponent(name)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ parent }),
    }).then(async (r) => {
      const d = (await r.json()) as { to?: string; error?: string };
      if (!r.ok) throw new Error(d.error ?? `${r.status}`);
      return d as { moved: string; to: string };
    }),
  // Rename the leaf (parent unchanged); subfolders + sessions follow.
  renameFolder: (path: string, name: string) =>
    fetch(`/folders/${encodeURIComponent(path)}`, {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    }).then(async (r) => {
      const d = (await r.json()) as { to?: string; error?: string };
      if (!r.ok) throw new Error(d.error ?? `${r.status}`);
      return d as { moved: string; to: string };
    }),
  deleteFolder: (name: string) =>
    fetch(`/folders/${encodeURIComponent(name)}`, {
      method: "DELETE",
      headers: authHeaders(),
    }).then((r) => r.json() as Promise<{ deleted: string }>),
  getSession: (key: string) =>
    get<{ notes?: string | null; name?: string | null }>(`/sessions/${key}`),
  fork: (
    key: string,
    opts: {
      command?: string;
      branch?: string;
      in_terminal?: boolean;
      carry_context?: boolean;
    },
  ) =>
    post<{
      session_key: string;
      branch?: string;
      carried_context?: boolean;
    }>(`/sessions/${key}/fork`, opts),
  forkConversation: (key: string) =>
    post<{
      session_key: string;
      command: string;
      cwd: string;
      carried_conversation?: boolean;
      note?: string | null;
    }>(
      `/sessions/${key}/fork-conversation`,
      { in_terminal: false },
    ),
  stop: (key: string) => post<{ stopped: boolean }>(`/sessions/${key}/stop`),
  resume: (key: string) =>
    post<{
      resumed: boolean;
      carried_conversation?: boolean;
      // native: the harness resumed its own conversation; brief: fresh
      // conversation seeded with reconstructed notes; none: fresh, no context.
      context?: "native" | "brief" | "none";
    }>(`/sessions/${key}/resume`),
  archive: (key: string) =>
    post<{ archived: boolean }>(`/sessions/${key}/archive`),
  remove: (key: string, force = false) =>
    fetch(`/sessions/${key}`, {
      method: "DELETE",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ force }),
    }).then(async (r) => ({
      status: r.status,
      ...((await r.json()) as {
        deleted?: boolean;
        unmerged_commits?: number;
        branch?: string | null;
      }),
    })),
  clearTerminated: () =>
    post<{ cleared: number }>("/sessions/clear-terminated"),
  checkpoint: (key: string, label: string) =>
    post<{ id: string; label: string; summary: string }>(
      `/sessions/${key}/checkpoint`,
      { label },
    ),
  checkpoints: (key: string) =>
    get<{ checkpoints: CheckpointRecord[] }>(`/sessions/${key}/checkpoints`),
  spotlight: (key: string) =>
    post<{ synced_files: string[] }>(`/sessions/${key}/spotlight`),
  sessionEvents: (key: string) =>
    get<{ events: RawEvent[] }>(`/sessions/${key}/events`),
};

interface RawEvent {
  _id: string;
  _ts: number;
  event_type?: string;
  session_key?: string;
  session_id?: string;
  uvs_session_id?: string;
  tool_name?: string;
}

export interface CheckpointRecord {
  id: string;
  label: string;
  summary: string;
  created_at: number;
  record: {
    intention?: string;
    prompts: string[];
    files: { path: string; edits: number }[];
    tools: { tool: string; count: number }[];
    event_count: number;
    git?: boolean;
    repo?: string;
    branch?: string;
  };
}

export type { RawEvent };
