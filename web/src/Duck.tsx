import { SessionView } from "./types";

// The rubber duck session mascot, v4 (user-approved 2026-09-20): no water —
// the duck fills the frame over a soft ground shadow.
//   busy     — faces the viewer (¾ view, big spectacles, pupils on the
//              screen), leaning into the camera over a perspective laptop,
//              wings hammering both sides, screen lines pulsing
//   waiting  — level head, eye cast down at a big wristwatch held up on the
//              wing (sweeping red second hand), glancing every ~3s
//   idle     — lazy drift
//   sleeping — eyes closed, head tucked, z's flowing (stopped/terminated/archived)
// Pure inline SVG + CSS keyframes (theme.css); honors prefers-reduced-motion.

export type DuckPose = "busy" | "waiting" | "idle" | "sleeping";

export function poseFor(state: string): DuckPose {
  if (state === "busy") return "busy";
  if (state === "waiting") return "waiting";
  if (state === "idle") return "idle";
  return "sleeping"; // stopped / terminated / archived
}

// The "Right now" phrase for the selected session's right panel.
export function duckPhrase(s: SessionView, state: string): string {
  const ago = agoShort(s.updatedAt);
  switch (state) {
    case "busy":
      return s.lastTool
        ? `Right now: on the laptop, typing away — running ${s.lastTool}.`
        : "Right now: on the laptop, typing away.";
    case "waiting":
      return `Right now: checking its watch, waiting on you — asked ${ago}.`;
    case "idle":
      return `Right now: just floating — finished its last turn ${ago}.`;
    case "stopped":
      return "Right now: fast asleep — stopped, resumable anytime.";
    case "terminated":
      return "Right now: fast asleep — the session ended.";
    case "archived":
      return "Right now: fast asleep — archived.";
    default:
      return `Right now: ${state}.`;
  }
}

function agoShort(ts: number): string {
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60_000));
  if (mins < 1) return "moments ago";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

const SHADOW = (cx: number) => (
  <ellipse className="duck-shadow" cx={cx} cy="56" rx="20" ry="2.6" fill="#000" />
);

export function Duck({ pose, size = 24 }: { pose: DuckPose; size?: number }) {
  return (
    <svg
      className={`rd-duck rd-duck-${pose}`}
      viewBox="2 4 60 56"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      {pose === "busy" && (
        <>
          {SHADOW(32)}
          <g className="duck-p-work">
            <ellipse
              cx="32"
              cy="42"
              rx="18"
              ry="13"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <g transform="rotate(5 32 21)">
              <circle
                cx="32"
                cy="20"
                r="13"
                fill="#FFD32B"
                stroke="#E0AD00"
                strokeWidth="1.2"
              />
              <path
                d="M26 27.5 Q32 32 38 27.5 Q35.5 24.5 32 24.5 Q28.5 24.5 26 27.5 Z"
                fill="#FF8A00"
                stroke="#D96F00"
                strokeWidth=".8"
              />
              <circle
                cx="25.5"
                cy="17.5"
                r="5.4"
                fill="#cfe3ff"
                fillOpacity=".3"
                stroke="#2b3344"
                strokeWidth="1.9"
              />
              <circle
                cx="38.5"
                cy="17.5"
                r="5.4"
                fill="#cfe3ff"
                fillOpacity=".3"
                stroke="#2b3344"
                strokeWidth="1.9"
              />
              <line x1="30.7" y1="17.5" x2="33.3" y2="17.5" stroke="#2b3344" strokeWidth="1.9" />
              <circle cx="25.5" cy="19.4" r="2" fill="#1a1a1a" />
              <circle cx="38.5" cy="19.4" r="2" fill="#1a1a1a" />
            </g>
            <g className="duck-wing-l">
              <ellipse
                cx="11.5"
                cy="46"
                rx="5.5"
                ry="3.8"
                fill="#F2BE0A"
                stroke="#E0AD00"
                strokeWidth=".9"
              />
            </g>
            <g className="duck-wing-r">
              <ellipse
                cx="52.5"
                cy="46"
                rx="5.5"
                ry="3.8"
                fill="#F2BE0A"
                stroke="#E0AD00"
                strokeWidth=".9"
              />
            </g>
          </g>
          {/* perspective laptop: trapezoid lid, wider at the bottom */}
          <path d="M19 34 L45 34 L48 52 L16 52 Z" fill="#2b3344" stroke="#0d0f14" strokeWidth="1" />
          <rect
            className="duck-gl"
            x="22"
            y="39"
            width="20"
            height="2.2"
            rx="1.1"
            fill="#22a06b"
            opacity=".85"
          />
          <rect
            className="duck-gl duck-gl2"
            x="23"
            y="44"
            width="14"
            height="2.2"
            rx="1.1"
            fill="#22a06b"
            opacity=".85"
          />
          <path d="M13 52 L51 52 L53 56 L11 56 Z" fill="#454f63" />
        </>
      )}
      {pose === "waiting" && (
        <>
          {SHADOW(30)}
          <g className="duck-p-watch">
            <path
              d="M10 42 Q6 34 12 31 Q13 37 17 39 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="28"
              cy="45"
              rx="17"
              ry="11.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <g transform="rotate(6 40 24)">
              <circle
                cx="40"
                cy="23"
                r="11.5"
                fill="#FFD32B"
                stroke="#E0AD00"
                strokeWidth="1.2"
              />
              <path
                d="M49.5 23.5 Q58 23 57.5 25.8 Q57 28.5 49.5 27.3 Q48 25.4 49.5 23.5 Z"
                fill="#FF8A00"
                stroke="#D96F00"
                strokeWidth=".8"
              />
              <circle cx="42.5" cy="22.5" r="2.6" fill="#fff" stroke="#E0AD00" strokeWidth=".5" />
              <circle cx="41.8" cy="23.6" r="1.7" fill="#1a1a1a" />
              <path
                d="M39.5 17.8 Q42.5 16.6 45.3 17.9"
                fill="none"
                stroke="#B8860B"
                strokeWidth="1.3"
                strokeLinecap="round"
              />
            </g>
            <path
              d="M20 47 Q26 40 32 37"
              fill="none"
              stroke="#F2BE0A"
              strokeWidth="6"
              strokeLinecap="round"
            />
            <circle cx="30" cy="34.5" r="7.4" fill="#f5f5f6" stroke="#454f63" strokeWidth="2" />
            <line
              x1="30"
              y1="34.5"
              x2="30"
              y2="30.2"
              stroke="#1a1a1a"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
            <line
              x1="30"
              y1="34.5"
              x2="33.2"
              y2="35.6"
              stroke="#1a1a1a"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
            <line
              className="duck-sec"
              x1="30"
              y1="34.5"
              x2="30"
              y2="29.3"
              stroke="#d92d20"
              strokeWidth=".8"
            />
          </g>
        </>
      )}
      {pose === "idle" && (
        <>
          {SHADOW(31)}
          <g className="duck-p-idle">
            <path
              d="M12 42 Q8 34 14 31 Q15 37 19 39 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="29"
              cy="45"
              rx="17"
              ry="11.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path d="M22 44 Q29 38 36 44 Q29 50 22 44 Z" fill="#F2BE0A" opacity=".7" />
            <circle
              cx="41"
              cy="25"
              r="11.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path
              d="M50.5 24.5 Q59 24 58.5 26.8 Q58 29.5 50.5 28.3 Q49 26.4 50.5 24.5 Z"
              fill="#FF8A00"
              stroke="#D96F00"
              strokeWidth=".8"
            />
            <circle cx="44.5" cy="22" r="2" fill="#1a1a1a" />
            <circle cx="45.2" cy="21.2" r="0.65" fill="#fff" />
          </g>
        </>
      )}
      {pose === "sleeping" && (
        <>
          {SHADOW(31)}
          <g className="duck-p-sleep">
            <path
              d="M12 44 Q8 36 14 33 Q15 39 19 41 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="30"
              cy="46"
              rx="18"
              ry="11.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path d="M23 45 Q30 39 37 45 Q30 51 23 45 Z" fill="#F2BE0A" opacity=".7" />
            <circle
              cx="42"
              cy="34"
              r="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path
              d="M51 34.5 Q58.5 34.5 58 36.5 Q57.5 38.5 51 37.5 Q49.7 36 51 34.5 Z"
              fill="#FF8A00"
              stroke="#D96F00"
              strokeWidth=".8"
            />
            <path
              d="M43 31 Q45.4 33 47.8 31"
              fill="none"
              stroke="#1a1a1a"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </g>
          <text className="duck-z duck-z1" x="50" y="22" fontSize="11" fontWeight="800" fill="#a4a9b3">
            z
          </text>
          <text className="duck-z duck-z2" x="56" y="15" fontSize="13" fontWeight="800" fill="#8b93a1">
            z
          </text>
        </>
      )}
    </svg>
  );
}
