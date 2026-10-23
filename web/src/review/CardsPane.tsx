import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReviewCard, type ReviewCards, type ReviewHeader } from "../api";
import { BehaviourCard, IntentCard, QuestionsCard, ReleaseCard, RiskCard, WalkthroughCard, type CardActions } from "./CardBodies";
import { severity, verdict, type BehaviourData, type IntentData, type QuestionsData, type ReleaseData, type RiskData, type WalkthroughData } from "./cardModel";
import { shortSha } from "./diffModel";

const POLL_MS = 3000;

export function CardsPane({ header, active, openFile, ask }: { header: ReviewHeader; active: boolean; openFile: (path: string, hunk?: number) => void; ask: (text: string) => void }) {
  const { repo, ref } = header;
  const [cards, setCards] = useState<ReviewCards | null>(null);
  const [error, setError] = useState("");
  const [current, setCurrent] = useState(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async () => {
    try {
      const result = await api.reviewCards(repo, ref);
      if (alive.current) { setCards(result); setError(""); }
    } catch (cause) {
      if (alive.current) setError((cause as Error).message);
    }
  }, [repo, ref]);
  useEffect(() => { void load(); }, [load]);
  // Cards render as they arrive: poll while any is still being written.
  const busy = !!cards && (cards.running || cards.cards.some(card => card.status === "pending" || card.status === "stale"));
  useEffect(() => {
    if (!active || !busy) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, busy, load]);

  async function regenerate() {
    try { setCards(await api.regenerateCards(repo, ref)); setError(""); }
    catch (cause) { setError((cause as Error).message); }
  }
  async function send(card: string, items: string[]) {
    if (!header.session) throw new Error("No authoring session to send to");
    await api.reviewSend(repo, ref, { card, items, session: header.session.key });
    await load();
  }
  const actions: CardActions = { openFile, ask, send, header };
  const list = cards?.cards ?? [];
  const card = list[current] ?? null;
  function step(delta: number) { setCurrent(index => (index + delta + list.length) % Math.max(list.length, 1)); }

  return <div className="rd-cards" onKeyDown={event => {
    if (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
  }}>
    <div className="rd-cards-toolbar">
      <span className="rd-card-meta">{cards?.head_sha ? `Cards for ${shortSha(cards.head_sha)}` : "Cards"}{cards?.stale ? ` · head moved to ${shortSha(header.head_sha)}, rewriting` : busy ? " · writing" : ""}</span>
      <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={() => void regenerate()}>Regenerate</button>
    </div>
    {error && <p className="rd-prs-note" role="alert">{error}</p>}
    {!cards && !error && <p className="rd-prs-empty" role="status">Loading cards…</p>}
    {cards && <div className="rd-card-strip" role="tablist" aria-label="Review cards">
      {list.map((item, index) => <button key={item.id} role="tab" aria-selected={index === current} aria-controls="review-card" className={`rd-card-tab is-${severity(item)}${item.status === "later" ? " later" : ""}`} onClick={() => setCurrent(index)}>
        <span className="rd-card-tab-number">{item.number}</span>
        <span className="rd-card-tab-title">{item.title}</span>
        <span className="rd-card-tab-verdict">{verdict(item)}</span>
      </button>)}
    </div>}
    {card && <article id="review-card" role="tabpanel" className="rd-card" aria-label={`${card.number} ${card.title}`}>
      <header className="rd-card-head">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label="Previous card" onClick={() => step(-1)}>‹</button>
        <div><h3>{card.number} · {card.title}</h3><p className={`rd-card-verdict-line is-${severity(card)}`}>{verdict(card)}</p></div>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-label="Next card" onClick={() => step(1)}>›</button>
      </header>
      <CardBody card={card} actions={actions} />
    </article>}
  </div>;
}

function CardBody({ card, actions }: { card: ReviewCard; actions: CardActions }) {
  if (card.status === "pending") return <p className="rd-prs-empty" role="status">Writing this card from the diff…</p>;
  if (card.status === "stale") return <p className="rd-prs-empty" role="status">The branch moved; this card is being rewritten for the new head.</p>;
  if (card.status === "later") return <p className="rd-prs-empty">{card.reason}</p>;
  if (card.status !== "ready" || !card.data) return <p className="rd-prs-note">Omitted: {card.reason ?? "no result"}</p>;
  const data = card.data as unknown;
  switch (card.id) {
    case "intent": return <IntentCard data={data as IntentData} actions={actions} />;
    case "walkthrough": return <WalkthroughCard data={data as WalkthroughData} actions={actions} />;
    case "risk": return <RiskCard data={data as RiskData} actions={actions} />;
    case "behaviour": return <BehaviourCard data={data as BehaviourData} actions={actions} />;
    case "release": return <ReleaseCard data={data as ReleaseData} actions={actions} />;
    case "questions": return <QuestionsCard card={card} data={data as QuestionsData} actions={actions} />;
    default: return <pre className="rd-card-raw">{JSON.stringify(card.data, null, 1)}</pre>;
  }
}
