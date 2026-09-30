import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, LocalVoiceStatus } from "./api";
import { NaturalVoicePanel, useVoice } from "./VoiceControl";
import { naturalSpeaker, VoiceSession } from "./voice";

vi.mock("./relay", () => ({ useRelay: () => ({ notes: [], rules: [], open: 0, refresh: () => {}, loaded: true }) }));
vi.mock("./api", () => ({
  api: { voiceStatus: vi.fn(), voiceInstall: vi.fn(), voiceRemove: vi.fn(), voiceSay: vi.fn(), voiceWarm: vi.fn(), voiceStop: vi.fn() },
}));
afterEach(() => { cleanup(); localStorage.clear(); vi.resetAllMocks(); });

it("speaks only through the natural voice; when it fails, Oracle chimes and says why, never a macOS voice", async () => {
  const log: string[] = [];
  const reasons: (string | null)[] = [];
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const speechSpy = vi.fn();
  vi.stubGlobal("speechSynthesis", { speak: speechSpy, cancel: () => {} });
  let fail = false;
  let chosen: string | null = "kokoro:af_heart";
  const speaker = naturalSpeaker({
    fetchWav: async (text, voice) => { if (fail) throw new Error("no answer within 15 s"); log.push(`kokoro:${voice}:${text}`); return new Blob(); },
    play: async () => true,
    stopPlayback: () => log.push("audio-stop"),
    chime: async () => { log.push("<chime>"); },
    chosen: () => chosen,
    onFallback: (r) => reasons.push(r),
  });
  await speaker.say("architect needs your input");
  await speaker.say("preview line", "kokoro:bf_emma");
  fail = true;
  expect(await speaker.say("main dev is complete")).toBe(true);
  chosen = null; // nothing installed
  await speaker.say("three sessions need you");
  speaker.stop();
  expect(log).toEqual([
    "kokoro:af_heart:architect needs your input",
    "kokoro:bf_emma:preview line",
    "<chime>",
    "<chime>",
    "audio-stop",
  ]);
  expect(reasons).toEqual([null, null, "no answer within 15 s", "no natural voice is installed"]);
  expect(speechSpy).not.toHaveBeenCalled();
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("no answer within 15 s"));
  vi.unstubAllGlobals();
});

const panel = (status: LocalVoiceStatus | null, fallbackReason: string | null = null) => {
  const onInstall = vi.fn();
  const onRemove = vi.fn();
  const view = render(<NaturalVoicePanel status={status} fallbackReason={fallbackReason} onInstall={onInstall} onRemove={onRemove} />);
  return { onInstall, onRemove, view };
};

it("states the download size before anything downloads, then shows progress", () => {
  const { onInstall, view } = panel({ state: "absent", size: "about 310 MB" });
  expect(screen.getByText(/Kokoro, an open-source voice that runs on this Mac/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Download natural voices (about 310 MB)" }));
  expect(onInstall).toHaveBeenCalledTimes(1);
  view.rerender(<NaturalVoicePanel status={{ state: "installing", step: "Downloading the voice model", done: 0.62, size: "about 310 MB" }} fallbackReason={null} onInstall={onInstall} onRemove={() => {}} />);
  expect(screen.getByRole("status")).toHaveTextContent("Downloading the voice model… 62%");
});

it("explains unsupported machines, failed downloads, and a fallback", () => {
  panel({ state: "unsupported", reason: "Natural voices need macOS 13 or later." });
  expect(screen.getByText("Natural voices need macOS 13 or later.")).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  const { onInstall } = panel({ state: "failed", reason: "Model download failed: offline", size: "about 310 MB" }, "the worker exited");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(onInstall).toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent("The voice couldn't speak (the worker exited), so Oracle chimed instead.");
});

const idle: VoiceSession[] = [{ key: "a", label: "architect", state: "idle" }];

it("defaults to a natural voice once installed, warms it, and stops it when voice turns off", async () => {
  vi.mocked(api.voiceStatus).mockResolvedValue({ state: "ready", voices: [{ id: "af_bella", label: "Bella", accent: "US" }, { id: "af_heart", label: "Heart", accent: "US" }] });
  vi.mocked(api.voiceWarm).mockResolvedValue(undefined);
  vi.mocked(api.voiceStop).mockResolvedValue(undefined);
  let voice!: ReturnType<typeof useVoice>;
  function Probe() { voice = useVoice(idle); return null; }
  render(<Probe />);
  await waitFor(() => expect(voice.selectedVoice).toBe("kokoro:af_heart"));
  expect(voice.voices.map((v) => v.label)).toEqual(["Bella (US)", "Heart (US)"]);
  await waitFor(() => expect(api.voiceWarm).toHaveBeenCalled());
  act(() => voice.setLevel("off"));
  expect(api.voiceStop).toHaveBeenCalled();
});
