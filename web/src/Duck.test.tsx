import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Duck } from "./Duck";
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
