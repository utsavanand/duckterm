import { recoveryBlocksResume, useRecoveryResumeBlocked } from "./resumeReadiness";
import { useState } from "react";
import { api } from "./api";
import { useToast } from "./ui";

// Row recovery and the card use the same response/error handling. The server
// may refuse an ambiguous native conversation; surface that reason verbatim.
const pending = new Set<string>();
export function useResumeSession(key: string) {
  const [resuming, setResuming] = useState(false);
  const toast = useToast();
  const recoveryBlocked = useRecoveryResumeBlocked(key);
  async function resumeSession() {
    if (pending.has(key) || recoveryBlocksResume(key)) return;
    pending.add(key);
    setResuming(true);
    try {
      const result = await api.resume(key);
      const label = result.context === "native" ? "Resumed — conversation carried"
        : result.context === "brief" ? "Resumed fresh — seeded with notes from the old session"
          : result.context === "none" ? "Resumed fresh — previous conversation couldn't be restored" : "Resumed";
      toast(result.resumed ? label : "Couldn't open a terminal to resume", result.resumed ? undefined : "err");
    } catch (error) {
      toast(`Resume failed: ${(error as Error).message}`, "err");
    } finally {
      pending.delete(key);
      setResuming(false);
    }
  }
  return { resuming, recoveryBlocked, resumeSession };
}
