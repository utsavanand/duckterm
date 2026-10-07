import { desktop, destinationRequest } from "./desktop";

// Local keys stay unchanged. Encode both remote identity components so neither
// can introduce URL delimiters, and equal session IDs on two hosts stay distinct.
const hex = (s: string) => [...new TextEncoder().encode(s)].map(b => b.toString(16).padStart(2, "0")).join("");
const unhex = (s: string) => new TextDecoder().decode(Uint8Array.from(s.match(/../g) ?? [], b => parseInt(b, 16)));
export function sessionRef(host: string, key: string): string {
  return host === "local" ? key : `~remote~${hex(host)}~${hex(key)}`;
}
export function splitSessionRef(ref: string): { host: string; key: string } {
  const match = /^~remote~((?:[a-f0-9]{2})+)~((?:[a-f0-9]{2})+)$/.exec(ref);
  return match ? { host: unhex(match[1]), key: unhex(match[2]) } : { host: "local", key: ref };
}
export function hostName(key: string): string {
  const { host } = splitSessionRef(key);
  return desktop()?.targets.find(t => t.id === host)?.name.replace(/^Remote — /, "") ?? (host === "local" ? "This Mac" : host);
}

export function qualifyResult(value: unknown, host: string): unknown {
  if (Array.isArray(value)) return value.map(v => qualifyResult(v, host));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, v]) => [key,
    (key === "session_key" || key === "parent_session_key") && typeof v === "string" && v
      ? sessionRef(host, v) : qualifyResult(v, host)]));
}

export async function hostFetch(host: string, path: string, init?: RequestInit): Promise<Response> {
  if (host === "local") return globalThis.fetch(path, init);
  const body = init?.body;
  let payload: Record<string, unknown> = body ? { body: String(body) } : {};
  if (body instanceof Blob) {
    if (body.size > 10_000_000) throw new Error("Image must be 10 MB or less");
    const bytes = new Uint8Array(await body.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    payload = { base64: btoa(binary), contentType: body.type };
  }
  const response = await destinationRequest<{ status: number; body: string; base64?: string; contentType?: string }>(host, "session-request", {
    path, method: init?.method ?? "GET", ...payload,
  });
  if (typeof response.base64 === "string") {
    const bytes = Uint8Array.from(atob(response.base64), c => c.charCodeAt(0));
    return new Response(bytes, { status: response.status, headers: { "Content-Type": response.contentType ?? "application/octet-stream" } });
  }
  let result = response.body;
  try { result = JSON.stringify(qualifyResult(JSON.parse(result), host)); } catch { /* text response */ }
  return new Response(result, { status: response.status });
}

export function sessionFetch(key: string, path: string, init?: RequestInit): Promise<Response> {
  return hostFetch(splitSessionRef(key).host, path, init);
}

// Shared by session components and api.ts, without patching browser globals.
export function routedFetch(path: string, init?: RequestInit): Promise<Response> {
  const match = /^\/sessions\/([^/?]+)(.*)$/.exec(path);
  if (!match) return globalThis.fetch(path, init);
  const { host, key } = splitSessionRef(decodeURIComponent(match[1]));
  return hostFetch(host, `/sessions/${encodeURIComponent(key)}${match[2]}`, init);
}

// Desktop grouping is presentation metadata. It must not change remote inbox
// permissions just because a row was dropped beside local agents.
const GROUPS = "duckterm.remotePresentationGroups";
export function remoteGroups(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(GROUPS) ?? "{}"); } catch { return {}; }
}
export function setRemoteGroup(key: string, group: string): void {
  localStorage.setItem(GROUPS, JSON.stringify({ ...remoteGroups(), [key]: group }));
  window.dispatchEvent(new Event("remote-sessions-refresh"));
}
export function clearRemoteGroups(host: string): void {
  const groups = Object.fromEntries(Object.entries(remoteGroups()).filter(([key]) => splitSessionRef(key).host !== host));
  localStorage.setItem(GROUPS, JSON.stringify(groups));
}
export function changeRemoteFolders(path: string, replacement: string): void {
  const groups = remoteGroups();
  for (const [key, group] of Object.entries(groups)) {
    if (group === path || group.startsWith(path + "/")) groups[key] = replacement ? replacement + group.slice(path.length) : "";
  }
  localStorage.setItem(GROUPS, JSON.stringify(groups));
  window.dispatchEvent(new Event("remote-sessions-refresh"));
}

export interface TerminalSocket {
  readyState: number;
  binaryType: string;
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null;
  send(data: string | Uint8Array): void;
  close(): void;
}

class NativeTerminal implements TerminalSocket {
  readyState = 0;
  binaryType = "arraybuffer";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: ArrayBuffer }) => void) | null = null;
  private id = crypto.randomUUID();
  private outgoing = Promise.resolve();
  private opened: Promise<unknown>;
  private listener = (event: Event) => {
    const data = (event as CustomEvent).detail;
    if (data.id !== this.id || this.readyState === 3) return;
    if (data.closed) { this.finish(); return; }
    if (data.opened && this.readyState === 0) { this.readyState = 1; this.onopen?.(); }
    if (typeof data.data === "string") {
      if (this.readyState === 0) { this.readyState = 1; this.onopen?.(); }
      const bytes = Uint8Array.from(atob(data.data), c => c.charCodeAt(0));
      this.onmessage?.({ data: bytes.buffer });
    }
  };
  constructor(private host: string, key: string) {
    window.addEventListener("remote-terminal", this.listener);
    this.opened = destinationRequest(host, "terminal-open", { id: this.id, key });
    void this.opened.catch(() => this.finish());
  }
  send(data: string | Uint8Array): void {
    if (this.readyState !== 1) return;
    const frames: (string | Uint8Array)[] = typeof data === "string" ? [data] : [];
    if (typeof data !== "string") for (let start = 0; start < data.length; start += 32768) frames.push(data.slice(start, start + 32768));
    for (const frame of frames) {
      const params = typeof frame === "string" ? { text: frame } : { data: btoa(Array.from(frame, b => String.fromCharCode(b)).join("")) };
      this.outgoing = this.outgoing.then(async () => {
        if (this.readyState === 1) await destinationRequest(this.host, "terminal-send", { id: this.id, ...params });
      }).catch(() => this.close());
    }
  }
  private finish(): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    window.removeEventListener("remote-terminal", this.listener);
    this.onclose?.();
  }
  close(): void {
    this.finish();
    // A cancelled opening must close after native registration, never before it.
    void this.opened.then(() => destinationRequest(this.host, "terminal-close", { id: this.id })).catch(() => undefined);
  }
}

export function terminalSocket(ref: string): TerminalSocket {
  const { host, key } = splitSessionRef(ref);
  if (host !== "local") return new NativeTerminal(host, key);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${protocol}://${location.host}/sessions/${encodeURIComponent(key)}/terminal`) as unknown as TerminalSocket;
}
