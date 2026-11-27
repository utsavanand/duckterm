import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import "./ChatProgress.css";

export interface ChatTiming {
  state: "running" | "finished" | "failed";
  elapsed_ms: number;
  stages: { stage: string; started_ms: number }[];
}
const LABELS: Record<string, string> = {
  starting: "Starting…", routing: "Understanding your request…", agents: "Checking your agents…",
  memories: "Reading saved memories and recent activity…", answer: "Preparing your answer…",
  installation: "Reading your installation details…", guide: "Reading the support guide…",
  diff: "Reading the diff…", files: "Reading requested files…", sending: "Sending your message…",
  rule: "Preparing your rule…",
};
export function validTiming(value: unknown): value is ChatTiming {
  if (!value || typeof value !== "object") return false;
  const p = value as ChatTiming;
  return ["running", "finished", "failed"].includes(p.state) && Number.isFinite(p.elapsed_ms) && p.elapsed_ms >= 0
    && Array.isArray(p.stages) && p.stages.length <= 64 && p.stages.every(s => s && typeof s.stage === "string" && s.stage in LABELS && Number.isFinite(s.started_ms) && s.started_ms >= 0);
}

/** The final response carries all stages, including ones shorter than a poll. */
export function useChatProgress() {
  const [value, setValue] = useState<ChatTiming | undefined>();
  const [startedAt, setStartedAt] = useState<number>();
  const [saved, setSaved] = useState(false);
  const current = useRef<{ id: string; stop: () => void }>();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; current.current?.stop(); current.current = undefined; }; }, []);
  const run = useCallback(async <T,>(operation: (id: string) => Promise<T>, initial = "starting"): Promise<T> => {
    current.current?.stop();
    const id = crypto.randomUUID().replace(/-/g, "");
    const start = performance.now();
    let latest: ChatTiming = { state: "running", elapsed_ms: 0, stages: [{ stage: initial, started_ms: 0 }] };
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const stop = () => { stopped = true; clearTimeout(timer); controller.abort(); };
    current.current = { id, stop };
    setStartedAt(start); setValue(latest); setSaved(false);
    const apply = (next: ChatTiming) => { latest = next; if (mounted.current && current.current?.id === id) setValue(next); };
    async function poll() {
      try {
        const next = await api.chatProgress(id, controller.signal);
        if (!stopped && validTiming(next)) apply({ ...next, state: "running" });
      } catch { /* A missing receipt or lost poll must never retry the question. */ }
      finally { if (!stopped) timer = setTimeout(() => void poll(), 1000); }
    }
    timer = setTimeout(() => void poll(), 500);
    try {
      const result = await operation(id);
      const receipt = result && typeof result === "object" && "chat_progress" in result ? result.chat_progress : undefined;
      if (validTiming(receipt)) latest = receipt;
      const record = result && typeof result === "object" ? result : {};
      const stored = "exchange" in record && record.exchange && typeof record.exchange === "object"
        || "progress" in record;
      if (mounted.current && current.current?.id === id) setSaved(Boolean(stored));
      apply({ ...latest, state: "finished", elapsed_ms: Math.max(latest.elapsed_ms, performance.now() - start) });
      return result;
    } catch (error) {
      apply({ ...latest, state: "failed", elapsed_ms: performance.now() - start });
      throw error;
    } finally { stop(); }
  }, []);
  return { value, startedAt, run, saved };
}

export function ChatProgress({ value, startedAt, complete = "Answered" }: { value?: ChatTiming; startedAt?: number; complete?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (value?.state !== "running") return;
    const timer = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(timer);
  }, [value?.state]);
  if (!value || !validTiming(value)) return null;
  const running = value.state === "running";
  const elapsed = running && startedAt !== undefined ? Math.max(value.elapsed_ms, performance.now() - startedAt) : value.elapsed_ms;
  const current = value.stages.at(-1);
  const label = running ? LABELS[current?.stage ?? "starting"] : value.state === "failed" ? "Stopped" : complete;
  return <div className="rd-chat-progress">
    <div className="rd-chat-progress-line">
      {running && <span className="rd-spinner" aria-hidden="true" />}
      <span role="status" aria-live="polite">{label}</span>
      <span className="rd-chat-progress-clock" aria-live="off" aria-label="Elapsed time">{Math.floor(elapsed / 1000)}s</span>
    </div>
    {value.stages.length > 0 && <details aria-live="off"><summary>Activity</summary><ol>{value.stages.map((s, i) => {
      const ended = i + 1 < value.stages.length || value.state === "finished";
      const end = value.stages[i + 1]?.started_ms ?? elapsed;
      return <li key={`${i}-${s.stage}`}><span>{ended ? "✓ " : ""}{LABELS[s.stage]}</span><span>{Math.floor(Math.max(0, end - s.started_ms) / 1000)}s</span></li>;
    })}</ol></details>}
  </div>;
}

/** Inbox delivery is not an answer. Derive its clock from durable message times. */
export function AgentReplyProgress({ at, status, answeredAt, name }: { at: number; status: string; answeredAt?: number | null; name?: string }) {
  const waiting = ["queued", "accepted", "read"].includes(status);
  const [, tick] = useState(0);
  useEffect(() => { if (!waiting) return; const timer = setInterval(() => tick(n => n + 1), 1000); return () => clearInterval(timer); }, [waiting]);
  const elapsed = waiting ? Date.now() - at : answeredAt ? answeredAt - at : null;
  const label = waiting ? `Waiting for ${name || "the agent"} to reply…` : status === "answered" ? "Answered" : "Reply " + status;
  return <div className="rd-chat-progress-line"><span role="status">{label}</span>{elapsed !== null && <span className="rd-chat-progress-clock" aria-live="off" aria-label="Elapsed time">{Math.floor(Math.max(0, elapsed) / 1000)}s</span>}</div>;
}

export function SendingProgress() {
  const [startedAt] = useState(() => performance.now());
  return <ChatProgress value={{ state: "running", elapsed_ms: 0, stages: [{stage: "sending", started_ms: 0}] }} startedAt={startedAt} />;
}
