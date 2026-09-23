import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Duck } from "./Duck";
afterEach(() => { cleanup(); vi.useRealTimers(); });
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
