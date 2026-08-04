import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

function resolve(theme: Theme): "light" | "dark" {
  if (theme === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return theme;
}

// Theme toggle: light / dark / follow-system. Persisted in localStorage and
// applied as data-theme on <html> so the CSS variables flip.
export function useTheme(): {
  theme: Theme;
  resolved: "light" | "dark";
  cycle: () => void;
} {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("rd-theme") as Theme) ?? "system",
  );
  // The concrete light/dark the app is showing — drives the terminal palette,
  // which follows the toggle. Kept in state so a "system" change re-renders.
  const [resolved, setResolved] = useState<"light" | "dark">(() =>
    resolve(theme),
  );

  useEffect(() => {
    const r = resolve(theme);
    setResolved(r);
    document.documentElement.setAttribute("data-theme", r);
    localStorage.setItem("rd-theme", theme);
  }, [theme]);

  // Re-apply when the OS theme changes while on "system".
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const r = resolve("system");
      setResolved(r);
      document.documentElement.setAttribute("data-theme", r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const cycle = () =>
    setTheme((t) =>
      t === "light" ? "dark" : t === "dark" ? "system" : "light",
    );

  return { theme, resolved, cycle };
}
