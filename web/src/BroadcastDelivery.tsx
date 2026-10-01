import { useEffect, useRef, useState } from "react";
import { api, BroadcastDeliveryStatus, BroadcastStatus, OracleExchange } from "./api";
import { html } from "./render";
import { Modal } from "./ui";

type Dispatch = NonNullable<OracleExchange["dispatch"]>;
const statusesOf = (result: BroadcastStatus) => Object.fromEntries(result.recipients.map(r => [r.message_id, r.status]));

export function BroadcastDelivery({ dispatch, active }: { dispatch: Dispatch; active: boolean }) {
  const [statuses, setStatuses] = useState<Record<string, BroadcastDeliveryStatus>>(dispatch.delivery_status ?? {});
  const [problem, setProblem] = useState("");
  const [ready, setReady] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const mounted = useRef(false), version = useRef(0), mutation = useRef(false);
  const key = dispatch.delivery_key!;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!active) return;
    let live = true, running = false, retired = false;
    let timer: ReturnType<typeof setTimeout>;
    const visible = () => live && document.visibilityState !== "hidden";
    async function refresh() {
      if (!visible() || running || retired) return;
      running = true;
      const generation = version.current;
      try {
        if (mutation.current) return;
        const result = await api.broadcastStatus(key);
        if (visible() && generation === version.current) {
          setStatuses(statusesOf(result)); setReady(true); setProblem("");
        }
      } catch (cause) {
        if (visible() && generation === version.current) {
          retired = (cause as { status?: number }).status === 404;
          setReady(false);
          setProblem(retired ? "Status history is no longer available. Saved messages and last known states are kept." : "Couldn’t refresh delivery. Showing last known states.");
        }
      } finally {
        running = false;
        if (visible() && !retired) timer = setTimeout(() => { void refresh(); }, 3000);
      }
    }
    const visibility = () => { clearTimeout(timer); if (visible()) void refresh(); };
    document.addEventListener("visibilitychange", visibility);
    void refresh();
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [key, active]);
  async function cancel() {
    if (mutation.current) return;
    mutation.current = true; version.current++; setCancelling(true); setCancelError("");
    try {
      const result = await api.cancelBroadcast(key);
      if (mounted.current) { setStatuses(statusesOf(result)); setReady(true); setProblem(""); setConfirm(false); }
    } catch (cause) {
      if (mounted.current) setCancelError(`Couldn’t confirm cancellation: ${(cause as Error).message}. Check delivery status before trying again.`);
    } finally {
      mutation.current = false;
      if (mounted.current) setCancelling(false);
    }
  }
  const open = dispatch.recipients.some(r => !["acknowledged", "cancelled"].includes(statuses[r.message_id]));
  return <div className="rd-broadcast-delivery">
    <div className="rd-broadcast-recipients">{dispatch.recipients.map(r => <div key={r.message_id}>
      <div className="rd-broadcast-recipient"><span>{r.name}</span><span className="rd-delivery-chip" data-status={statuses[r.message_id]}>{statuses[r.message_id] ?? (problem ? "Status unavailable" : "Checking delivery…")}</span></div>
      {r.answer && <div className="rd-folder-answer"><small>{r.name} · Reply</small><div dangerouslySetInnerHTML={{ __html: html(r.answer) }} /></div>}
    </div>)}</div>
    {problem && <p className="rd-broadcast-unavailable" role="status">{problem}</p>}
    <div className="rd-broadcast-footer"><span>{problem ? "Last known delivery status" : dispatch.priority ? "Supported agents are reminded until acknowledged or cancelled." : "Delivery and acknowledgment are separate."}</span>
      {open && <button type="button" className="rd-btn rd-btn-ghost rd-btn-sm" disabled={!ready || cancelling} onClick={() => { setCancelError(""); setConfirm(true); }}>Cancel broadcast</button>}
    </div>
    {confirm && <Modal title="Cancel this broadcast?" onClose={() => { if (!cancelling) setConfirm(false); }}><section role="dialog" aria-label="Cancel this broadcast?">
      <p>This cancels open recipient copies and stops further priority reminders. It cannot retract text an agent has already received. Acknowledged recipients stay acknowledged.</p>
      {cancelError && <p role="alert">{cancelError}</p>}
      <button className="rd-btn rd-btn-ghost" disabled={cancelling} onClick={() => setConfirm(false)}>Keep broadcast</button>
      <button className="rd-btn rd-btn-primary" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? "Cancelling…" : "Cancel broadcast"}</button>
    </section></Modal>}
  </div>;
}
