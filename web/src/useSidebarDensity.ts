import { useEffect, useState } from "react";

export type SidebarDensity = "compact" | "standard" | "relaxed";

export function useSidebarDensity() {
  const [density, setDensity] = useState<SidebarDensity>(() => {
    try {
      const saved = localStorage.getItem("rd.sidebarDensity");
      return saved === "compact" || saved === "relaxed" ? saved : "standard";
    } catch {
      return "standard";
    }
  });
  useEffect(() => {
    try { localStorage.setItem("rd.sidebarDensity", density); }
    catch { /* The selection still works when storage is unavailable. */ }
  }, [density]);
  return { density, setDensity };
}
