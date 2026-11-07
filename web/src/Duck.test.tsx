import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Duck, DuckPose } from "./Duck";
import { StaticEdition } from "./edition";
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
it("expires a celebration after four seconds and never replays an expired one on mount", () => {
  vi.useFakeTimers(); vi.setSystemTime(10000);
  const celebration = { kind: "done" as const, startedAt: 10000 };
  const view = render(<Duck pose="idle" celebrating={celebration} />);
  expect(screen.getByRole("img", { name: "Turn complete" })).toBeVisible();
  act(() => { vi.advanceTimersByTime(4000); });
  expect(screen.queryByRole("img", { name: "Turn complete" })).toBeNull();
  view.unmount();
  render(<Duck pose="idle" celebrating={celebration} />);
  expect(screen.queryByRole("img", { name: "Turn complete" })).toBeNull();
});

it("expires even when the one-shot callback observes the clock just before the deadline", () => {
  vi.useFakeTimers(); vi.setSystemTime(10000);
  const clock = vi.spyOn(Date, "now").mockReturnValue(10000);
  render(<Duck pose="idle" celebrating={{ kind: "done", startedAt: 10000 }} />);
  clock.mockReturnValue(13999);
  act(() => { vi.advanceTimersByTime(4000); });
  expect(screen.queryByRole("img", { name: "Turn complete" })).toBeNull();
});


it.each<DuckPose>(["idle", "busy", "waiting", "waving"])("tints the %s pose with accessible context and clears after compaction", pose => {
  const view = render(<StaticEdition features={["context"]}><Duck pose={pose} session={{ contextTokens: 950, contextWindowTokens: 1000 }} /></StaticEdition>);
  expect(screen.getByRole("img", { name: /Context 95% used, nearly full/ })).toBeInTheDocument();
  expect(view.container.querySelector<HTMLElement>(".rd-duck-wrap")!.style.getPropertyValue("--duck-body")).toBe("#d94343");
  if (pose === "waiting") expect(view.container.querySelector(".duck-sec")).toHaveAttribute("stroke", "#d92d20");
  view.rerender(<StaticEdition features={["context"]}><Duck pose={pose} session={{ contextTokens: 100, contextWindowTokens: 1000 }} /></StaticEdition>);
  expect(screen.queryByRole("img")).toBeNull();
  expect(view.container.querySelector<HTMLElement>(".rd-duck-wrap")!.style.getPropertyValue("--duck-body")).toBe("");
});

it("keeps sleeping ducks and editions without context untinted", () => {
  const session = { contextTokens: 950, contextWindowTokens: 1000 };
  const view = render(<StaticEdition features={["context"]}><Duck pose="sleeping" session={session} /></StaticEdition>);
  expect(view.container.querySelector<HTMLElement>(".rd-duck-wrap")!.style.getPropertyValue("--duck-body")).toBe("");
  view.rerender(<StaticEdition features={[]}><Duck pose="busy" session={session} /></StaticEdition>);
  expect(view.container.querySelector<HTMLElement>(".rd-duck-wrap")!.style.getPropertyValue("--duck-body")).toBe("");
  expect(screen.queryByRole("img")).toBeNull();
});
