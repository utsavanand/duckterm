import { SessionView } from "./types";

// The rubber duck session mascot — the classic yellow bathtub duck, floating
// on water, with one pose per live state (user-approved designs, see
// scratch preview 2026-09-20):
//   busy     — hunched over a laptop on a floating plank, wing typing
//   waiting  — wings crossed, unimpressed brow, impatient shuffle
//   idle     — lazy drift
//   sleeping — eyes closed, head tucked, z's flowing (stopped/terminated/archived)
// Pure inline SVG + CSS keyframes (theme.css): no image assets, no bundle
// weight, honors prefers-reduced-motion.

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
      return `Right now: wings crossed, waiting on you — asked ${ago}.`;
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

const WATER = (
  <g className="duck-water">
    <path
      d="M0 48 Q8 45.5 16 48 T32 48 T48 48 T66 48 L66 60 L0 60 Z"
      fill="#3B82C4"
      opacity=".35"
    />
    <path
      d="M0 50 Q10 47.5 20 50 T40 50 T60 50 T74 50 L74 60 L0 60 Z"
      fill="#2C6EA8"
      opacity=".3"
    />
  </g>
);

export function Duck({ pose, size = 24 }: { pose: DuckPose; size?: number }) {
  return (
    <svg
      className={`rd-duck rd-duck-${pose}`}
      viewBox="0 0 64 60"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      {pose === "busy" && (
        <>
          <g className="duck-pose-work">
            <path
              d="M10 40 Q6 32 12 29 Q13 35 17 37 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="26"
              cy="43"
              rx="16"
              ry="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <g transform="rotate(14 38 28)">
              <circle
                cx="38"
                cy="27"
                r="10"
                fill="#FFD32B"
                stroke="#E0AD00"
                strokeWidth="1.2"
              />
              <path
                d="M46 27 Q54 26.5 53.5 29 Q53 31.5 46 30.5 Q44.7 28.7 46 27 Z"
                fill="#FF8A00"
                stroke="#D96F00"
                strokeWidth=".8"
              />
              <circle cx="41.5" cy="26" r="1.9" fill="#1a1a1a" />
              <circle cx="42" cy="25.4" r="0.55" fill="#fff" />
            </g>
            <g className="duck-wing-typing">
              <path
                d="M30 38 Q40 36 44 42 Q36 46 30 42 Z"
                fill="#F2BE0A"
                stroke="#E0AD00"
                strokeWidth=".8"
              />
            </g>
          </g>
          <rect x="42" y="44" width="20" height="3" rx="1.5" fill="#8B5A2B" />
          <rect
            x="46"
            y="32"
            width="13"
            height="10"
            rx="1"
            fill="#2b3344"
            stroke="#0d0f14"
            strokeWidth=".8"
          />
          <rect
            className="duck-screenline"
            x="48"
            y="34.5"
            width="9"
            height="1.5"
            rx=".75"
            fill="#22a06b"
          />
          <rect
            className="duck-screenline duck-screenline-2"
            x="48"
            y="37"
            width="6.5"
            height="1.5"
            rx=".75"
            fill="#22a06b"
          />
          <rect x="44.5" y="42" width="16" height="2.4" rx="1.2" fill="#454f63" />
          {WATER}
        </>
      )}
      {pose === "waiting" && (
        <>
          <g className="duck-pose-wait">
            <path
              d="M12 40 Q8 32 14 29 Q15 35 19 37 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="29"
              cy="43"
              rx="16.5"
              ry="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <circle
              cx="40"
              cy="25"
              r="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path
              d="M49 24 Q57 23.5 56.5 26 Q56 28.5 49 27.5 Q47.5 25.7 49 24 Z"
              fill="#FF8A00"
              stroke="#D96F00"
              strokeWidth=".8"
            />
            <circle cx="43.5" cy="22.5" r="1.9" fill="#1a1a1a" />
            <line
              x1="40.8"
              y1="19.4"
              x2="46.4"
              y2="18.6"
              stroke="#B8860B"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
            <path
              d="M22 41 Q31 36 39 43 Q31 47 22 41 Z"
              fill="#F2BE0A"
              stroke="#E0AD00"
              strokeWidth=".9"
            />
            <path
              d="M37 41 Q28 37 21 44 Q29 47.5 37 41 Z"
              fill="#EFB000"
              stroke="#D99C00"
              strokeWidth=".9"
            />
          </g>
          {WATER}
        </>
      )}
      {pose === "idle" && (
        <>
          <g className="duck-pose-idle">
            <path
              d="M12 40 Q8 32 14 29 Q15 35 19 37 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="29"
              cy="43"
              rx="16.5"
              ry="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path d="M22 42 Q29 36 36 42 Q29 47.5 22 42 Z" fill="#F2BE0A" opacity=".7" />
            <circle
              cx="40"
              cy="25"
              r="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path
              d="M49 24 Q57 23.5 56.5 26 Q56 28.5 49 27.5 Q47.5 25.7 49 24 Z"
              fill="#FF8A00"
              stroke="#D96F00"
              strokeWidth=".8"
            />
            <circle cx="43.5" cy="22" r="1.9" fill="#1a1a1a" />
            <circle cx="44.2" cy="21.3" r="0.6" fill="#fff" />
          </g>
          {WATER}
        </>
      )}
      {pose === "sleeping" && (
        <>
          <g className="duck-pose-sleep">
            <path
              d="M12 42 Q8 34 14 31 Q15 37 19 39 Z"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1"
            />
            <ellipse
              cx="30"
              cy="44"
              rx="17"
              ry="10.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path d="M23 43 Q30 37.5 37 43 Q30 48.5 23 43 Z" fill="#F2BE0A" opacity=".7" />
            <circle
              cx="41"
              cy="33"
              r="9.5"
              fill="#FFD32B"
              stroke="#E0AD00"
              strokeWidth="1.2"
            />
            <path
              d="M49 33.5 Q56 33.5 55.5 35.5 Q55 37.5 49 36.5 Q47.8 35 49 33.5 Z"
              fill="#FF8A00"
              stroke="#D96F00"
              strokeWidth=".8"
            />
            <path
              d="M42 30.5 Q44.2 32.3 46.4 30.5"
              fill="none"
              stroke="#1a1a1a"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </g>
          <text className="duck-z duck-z1" x="48" y="18" fontSize="10" fontWeight="800" fill="#a4a9b3">
            z
          </text>
          <text className="duck-z duck-z2" x="54" y="12" fontSize="12" fontWeight="800" fill="#8b93a1">
            z
          </text>
          {WATER}
        </>
      )}
    </svg>
  );
}
