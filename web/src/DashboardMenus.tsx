import { ComponentProps, useMemo } from "react";
import { createPortal } from "react-dom";
import { HeaderMenus } from "./HeaderMenus";
import { useVoice, VoiceFallbackPill, VoiceMenu, VoicePausedPill, VoiceToast } from "./VoiceControl";
import { effectiveState } from "./sessions";
import { SessionView } from "./types";
import { COMPLETION_SETTLE_MS } from "./voice";
import { useNow } from "./useNow";

type Props = Omit<ComponentProps<typeof HeaderMenus>, "voiceLevel" | "onVoiceLevel" | "voice"> & { sessions: SessionView[] };
export function DashboardMenus({ sessions, ...props }: Props) {
  // Voice's 90-second completion grace and its own five-second scheduler must
  // keep working in the background, without re-rendering the terminal tree.
  const now = useNow(1000, true, false);
  const voiceSessions = useMemo(() => sessions.map(s => ({
    key: s.key, label: s.label, group: s.group,
    state: effectiveState(s, now, COMPLETION_SETTLE_MS),
    waitingSince: s.waitingSince, waitingCause: s.waitingCause,
  })).filter(s => s.state !== "archived"), [sessions, now]);
  const voice = useVoice(voiceSessions);
  return <>
    <VoiceMenu level={voice.level} ready={voice.ready} onLevel={voice.setLevel} />
    {voice.fallbackReason && <VoiceFallbackPill reason={voice.fallbackReason} />}
    <HeaderMenus {...props} voiceLevel={voice.level} onVoiceLevel={voice.setLevel} voice={{
      voices: voice.voices, selected: voice.selectedVoice, ready: voice.ready,
      onSelect: voice.setVoice, onPreview: voice.preview, local: voice.local.status,
      fallbackReason: voice.fallbackReason, onInstall: voice.local.install, onRemove: voice.local.remove,
    }} />
    {createPortal(<>
      {voice.paused && !voice.spoken && <VoicePausedPill />}
      {voice.spoken && <VoiceToast text={voice.spoken} level={voice.level} onLevel={voice.setLevel} onMute={voice.mute} onDismiss={voice.dismiss} />}
    </>, document.body)}
  </>;
}
