import type { CheckpointRecord, CheckpointSelection } from "./api";
import { requestCheckpoint } from "./checkpointRequests";

export const CHECKPOINT_SELECT = "duckterm-checkpoint-select";
export interface CheckpointChoiceRequest {
  key: string;
  name: string;
  resolve: (selection: CheckpointSelection | null) => void;
}
const choosing = new Map<string, Promise<CheckpointRecord | null>>();

// The app-level dialog survives the menu closing and keeps its original host/key.
export function chooseCheckpoint(key: string, name: string): Promise<CheckpointRecord | null> {
  const existing = choosing.get(key);
  if (existing) return existing;
  const selected = new Promise<CheckpointSelection | null>(resolve => {
    window.dispatchEvent(new CustomEvent<CheckpointChoiceRequest>(CHECKPOINT_SELECT, { detail: { key, name, resolve } }));
  });
  const work = selected.then(selection => selection ? requestCheckpoint(key, selection) : null)
    .finally(() => choosing.delete(key));
  choosing.set(key, work);
  return work;
}
