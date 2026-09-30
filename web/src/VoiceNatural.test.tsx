import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, LocalVoiceStatus } from "./api";
import { NaturalVoicePanel, useVoice } from "./VoiceControl";
import { naturalSpeaker, Speaker, VoiceSession } from "./voice";

vi.mock("./relay", () => ({ useRelay: () => ({ notes: [], rules: [], open: 0, refresh: () => {}, loaded: true }) }));
vi.mock("./api", () => ({
  api: { voiceStatus: vi.fn(), voiceInstall: vi.fn(), voiceRemove: vi.fn(), voiceSay: vi.fn(), voiceWarm: vi.fn(), voiceStop: vi.fn() },
}));
afterEach(() => { cleanup(); localStorage.clear(); vi.resetAllMocks(); });

function fallbackSpeaker(log: string[]): Speaker {
  return { say: async (t, v) => { log.push(`mac:${v ?? "best"}:${t}`); }, chime: async () => {}, stop: () => log.push("mac-stop") };
}

it("speaks a chosen natural voice through Kokoro and falls back to the best macOS voice, saying why", async () => {
  const log: string[] = [];
  const reasons: (string | null)[] = [];
  let fail = false;
  const speaker = naturalSpeaker({
    fetchWav: async (text, voice) => { if (fail) throw new Error("no answer within 15 s"); log.push(`kokoro:${voice}:${text}`); return new Blob(); },
    play: async () => true,
    stopPlayback: () => log.push("audio-stop"),
    fallback: fallbackSpeaker(log),
    chosen: () => "kokoro:af_heart",
    onFallback: (r) => reasons.push(r),
  });
  await speaker.say("architect needs your input");
  fail = true;
  await speaker.say("main dev is complete");
  await speaker.say("preview", "Samantha"); // a macOS voice is never sent to Kokoro
  speaker.stop();
  expect(log).toEqual([
    "kokoro:af_heart:architect needs your input",
    "mac:best:main dev is complete",
    "mac:Samantha:preview",
    "audio-stop",
    "mac-stop",
  ]);
  expect(reasons).toEqual([null, "no answer within 15 s"]);
});

const panel = (status: LocalVoiceStatus | null, fallbackReason: string | null = null) => {
  const onInstall = vi.fn();
  const onRemove = vi.fn();
  const view = render(<NaturalVoicePanel status={status} fallbackReason={fallbackReason} onInstall={onInstall} onRemove={onRemove} />);
  return { onInstall, onRemove, view };
};

it("states the download size before anything downloads, then shows progress", () => {
  const { onInstall, view } = panel({ state: "absent", size: "about 310 MB" });
  expect(screen.getByText(/The download is about 310 MB/)).toBeVisible();
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
  expect(screen.getByRole("alert")).toHaveTextContent("The natural voice couldn't speak (the worker exited), so a macOS voice is speaking instead.");
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
  expect(voice.voices.slice(0, 2).map((v) => v.label)).toEqual(["Bella (natural, US)", "Heart (natural, US)"]);
  await waitFor(() => expect(api.voiceWarm).toHaveBeenCalled());
  act(() => voice.setLevel("off"));
  expect(api.voiceStop).toHaveBeenCalled();
});
