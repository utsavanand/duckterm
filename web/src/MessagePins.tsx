import { useEffect, useRef, useState } from "react";
import { authHeaders } from "./api";
import { useToast } from "./ui";

export type MessageBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; name: string; input?: unknown }
  | { type: "tool_result"; text: string };
export interface Message {
  id: number;
  message_key?: string;
  role: "user" | "assistant";
  blocks: MessageBlock[];
}
export interface MessagePin {
  message_key: string;
  message: Message;
  created_at: number;
}
export interface PinTarget { pin: MessagePin; request: number }

export function pinExcerpt(message: Message): string {
  return message.blocks.map((b) => b.type === "tool_use" ? b.name : b.text)
    .join(" ").replace(/\s+/g, " ").trim().slice(0, 120) || "Saved message";
}

export function useMessagePins(sessionKey: string | null) {
  const toast = useToast();
  const current = useRef(sessionKey);
  current.current = sessionKey;
  const generation = useRef(0);
  const [state, setState] = useState<{ key: string | null; pins: MessagePin[] }>({ key: null, pins: [] });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const busy = useRef(false);
  useEffect(() => {
    let live = true;
    generation.current++;
    busy.current = !!sessionKey;
    setPending(!!sessionKey);
    setError("");
    setState({ key: sessionKey, pins: [] });
    if (!sessionKey) return;
    fetch(`/sessions/${sessionKey}/pins`, { headers: authHeaders() })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.error ?? "Could not load pins");
        if (live) setState({ key: sessionKey, pins: d.pins });
      }).catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) { busy.current = false; setPending(false); } });
    return () => { live = false; };
  }, [sessionKey]);
  const pins = state.key === sessionKey ? state.pins : [];
  async function toggle(message: Message) {
    if (!sessionKey || !message.message_key || busy.current || error) return;
    const key = message.message_key;
    const remove = pins.some((p) => p.message_key === key);
    const started = generation.current;
    busy.current = true;
    setPending(true);
    try {
      const r = await fetch(`/sessions/${sessionKey}/pins${remove ? `/${key}` : ""}`, {
        method: remove ? "DELETE" : "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        ...(remove ? {} : { body: JSON.stringify({ message_key: key }) }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error ?? "Could not update pin");
      if (current.current === sessionKey && generation.current === started) {
        setState((s) => ({ key: sessionKey, pins: remove ? s.pins.filter((p) => p.message_key !== key)
          : [...s.pins.filter((p) => p.message_key !== key), data.pin] }));
      }
    } catch (e) {
      if (current.current === sessionKey && generation.current === started)
        toast(`Pin update failed: ${(e as Error).message}`, "err");
    } finally {
      if (current.current === sessionKey && generation.current === started) {
        busy.current = false;
        setPending(false);
      }
    }
  }
  return { pins, pending, error, toggle };
}

export function MessagePinStrip({ pins, error, onOpen }: {
  pins: MessagePin[]; error: string; onOpen: (pin: MessagePin) => void;
}) {
  return <div className="rd-pin-strip" aria-label="Pinned messages">
    <span className="rd-pin-label">Pinned {pins.length}</span>
    <div className="rd-pin-chips">
      {pins.map((pin) => <button key={pin.message_key} className="rd-pin-chip"
        title={pinExcerpt(pin.message).split(/\s+/).slice(0, 6).join(" ")}
        aria-label={`Open pinned message: ${pinExcerpt(pin.message)}`}
        onClick={() => onOpen(pin)}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M16 3H8l1 7-4 4v2h6v6l2-6h6v-2l-4-4 1-7Z" />
        </svg>
      </button>)}
      {error ? <span role="alert">Could not load pins. Reopen this session to retry.</span>
        : !pins.length && <span className="rd-pin-empty">Pin a message in Messages to keep it here.</span>}
    </div>
  </div>;
}
