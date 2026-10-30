import { createPortal } from "react-dom";
import { MemoryView } from "./MemoryView";
import { sessionRef, splitSessionRef } from "./hostTransport";
import { Modal } from "./ui";

export interface SourceMemoryTarget { source: string; name: string; revision?: string | null }
/** Keep the host and source identity even when its sidebar row has been retired. */
export function SourceMemory({ origin, target, onClose }: { origin: string; target: SourceMemoryTarget; onClose: () => void }) {
  const key = sessionRef(splitSessionRef(origin).host, target.source);
  return createPortal(<div onKeyDown={event => {
    if (event.key === "Escape") { event.stopPropagation(); onClose(); }
    if (event.key === "Tab") {
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled),select:not(:disabled),summary,pre[tabindex]")];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}><Modal title="Source memory" onClose={onClose} width={900}>
    <div role="dialog" aria-modal="true" aria-label="Source memory">
      <p>{target.revision ? "Opened the saved revision referenced by this merge. Other saved revisions remain available below." : "This merge did not reference a saved revision. Available source memory is shown below."}</p>
      <MemoryView key={`${key}:${target.revision ?? ""}`} sessionKey={key} name={target.name} initialRevision={target.revision ?? ""} readOnly />
      <button autoFocus className="rd-btn rd-btn-ghost" onClick={onClose}>Back</button>
    </div>
  </Modal></div>, document.body);
}

export function mergeClosure(closed?: boolean, state?: string | null): string {
  if (closed) return "Source closed. Its summaries and evidence remain readable.";
  if (state === "draft_not_empty") return "Waiting to close: the source has a typed draft. It has been preserved.";
  if (state === "memory_unavailable") return "Source closure is pending because its memory link could not be verified.";
  if (state === "terminal_unavailable" || state === "terminal_not_owned") return "Source closure is pending: DuckTerm could not verify its terminal can be stopped.";
  if (state === "source_deleted") return "The source has been deleted; its memory is unavailable.";
  return "Waiting to close the source after its turn finishes and its input is empty. Its history is preserved.";
}
