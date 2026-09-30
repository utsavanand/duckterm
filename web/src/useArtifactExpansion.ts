import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

// Keep the preview mounted: moving an iframe into a portal reloads its document.
export function useArtifactExpansion(identity: string | undefined) {
  const [expanded, setExpanded] = useState(false);
  const viewer = useRef<HTMLDivElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const entry = useRef<{ token: string; state: unknown } | null>(null);
  const expand = useCallback((button: HTMLElement) => {
    if (expanded) return;
    opener.current = button;
    const token = crypto.randomUUID();
    entry.current = { token, state: history.state };
    history.pushState({ ...history.state, artifactExpansion: token }, "");
    setExpanded(true);
  }, [expanded]);
  const close = useCallback(() => {
    if (entry.current && history.state?.artifactExpansion === entry.current.token) history.back();
    else setExpanded(false);
  }, []);
  useEffect(() => {
    const pop = () => setExpanded(false);
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    // A removed artifact or a different session must not retain a modal history entry.
    setExpanded(false);
    return () => {
      if (entry.current && history.state?.artifactExpansion === entry.current.token) history.replaceState(entry.current.state, "");
      entry.current = null;
    };
  }, [identity]);
  useLayoutEffect(() => {
    if (!expanded) { opener.current?.focus({ preventScroll: true }); return; }
    if (!viewer.current) return;
    const siblings: { element: HTMLElement; inert: boolean }[] = [];
    let node: HTMLElement = viewer.current;
    while (node.parentElement) {
      for (const sibling of node.parentElement.children) {
        if (sibling !== node && sibling instanceof HTMLElement) {
          siblings.push({ element: sibling, inert: sibling.inert });
          sibling.inert = true;
        }
      }
      node = node.parentElement;
    }
    back.current?.focus();
    return () => {
      siblings.forEach(({ element, inert }) => { element.inert = inert; });
    };
  }, [expanded]);
  return { expanded, viewer, back, expand, close };
}
