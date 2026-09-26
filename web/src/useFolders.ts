import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

// Empty folders have no session activity to trigger a refresh. Keep the saved
// folder catalog current independently, including edits in another window.
export function useFolders() {
  const [folders, setFolders] = useState<string[]>([]);
  const latestRequest = useRef(0);
  const mounted = useRef(false);
  const refreshFolders = useCallback(async () => {
    const request = ++latestRequest.current;
    try {
      const data = await api.folders();
      if (mounted.current && request === latestRequest.current) {
        setFolders((previous) =>
          previous.length === data.folders.length && previous.every((name, i) => name === data.folders[i])
            ? previous : data.folders,
        );
      }
    } catch { /* Keep saved folder rows visible during connection failures. */ }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refreshVisible = () => {
      if (!document.hidden) void refreshFolders();
    };
    const poll = async () => {
      if (!document.hidden) await refreshFolders();
      if (!stopped) timer = setTimeout(poll, 3000);
    };
    void poll();
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      stopped = true;
      mounted.current = false;
      clearTimeout(timer);
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [refreshFolders]);

  return { folders, refreshFolders };
}
