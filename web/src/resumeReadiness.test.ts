import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { api } from "./api";
import { setRecoveryResumeAllowed } from "./resumeReadiness";
import { useResumeSession } from "./useResumeSession";
vi.mock("./api", () => ({ api: { resume: vi.fn() } }));
vi.mock("./ui", () => ({ useToast: () => vi.fn() }));

it("blocks even a captured Resume callback across card unmount until readiness is confirmed", async () => {
  const key = "qa-uncertain-undo";
  vi.mocked(api.resume).mockResolvedValue({ resumed: true, context: "native" });
  const first = renderHook(() => useResumeSession(key));
  const captured = first.result.current.resumeSession;
  act(() => setRecoveryResumeAllowed(key, false));
  expect(first.result.current.recoveryBlocked).toBe(true);
  first.unmount();
  const second = renderHook(() => useResumeSession(key));
  try {
    expect(second.result.current.recoveryBlocked).toBe(true);
    await act(async () => { await captured(); await second.result.current.resumeSession(); });
    expect(api.resume).not.toHaveBeenCalled();
    act(() => setRecoveryResumeAllowed(key, true));
    expect(second.result.current.recoveryBlocked).toBe(false);
    await act(async () => { await second.result.current.resumeSession(); });
    expect(api.resume).toHaveBeenCalledExactlyOnceWith(key);
  } finally { second.unmount(); setRecoveryResumeAllowed(key, true); }
});
