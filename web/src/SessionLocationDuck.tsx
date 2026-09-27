import { Duck, DuckPose } from "./Duck";
import { hostName, splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";
import "./sessionLocation.css";

export function sessionLocation(session: SessionView) {
  const remote = (session.host ?? splitSessionRef(session.key).host) !== "local";
  return { remote, label: remote ? `Remote · ${session.hostLabel || hostName(session.key)}${session.hostOffline ? " · Disconnected" : ""}` : "This Mac" };
}

export function SessionLocationDuck({ session, pose, height = 32, focusable = true }: { session: SessionView; pose: DuckPose; height?: number; focusable?: boolean }) {
  const { remote, label: location } = sessionLocation(session);
  const scale = height / 32;
  const offline = remote && !!session.hostOffline;
  return <span className={`rd-session-location ${remote ? "remote" : "local"}${offline ? " offline" : ""}`} style={{ width: 34 * scale, height }} role="group" tabIndex={focusable ? 0 : undefined} aria-label={location} data-location={location}>
    {remote && <svg className="rd-location-cloud" style={{ width: 36 * scale, height }} viewBox="0 0 36 32" aria-hidden="true"><path d="M8 28C-1 28 0 17 7 16C5 7 18 4 22 12C31 8 37 18 31 22C36 29 24 30 20 28Z" fill="#d8e9f5" stroke="#8fb3cf" strokeWidth="1.2" />{offline && <path d="M5 30L32 6" stroke="#a86422" strokeWidth="2" />}</svg>}
    <span className="rd-location-mascot" style={{ left: (remote ? 6 : 3) * scale }}><Duck pose={pose} size={24 * scale} celebrating={session.celebration} /></span>
  </span>;
}
