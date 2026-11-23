import { TokenRow } from "./analyticsData";

// API list prices in dollars per million tokens, what an enterprise API
// account is charged. Owner's choice (2026-11-20): estimate spend at these
// rates even for subscription use, to see roughly what building costs.
// Claude rates are Anthropic's published first-party prices (cached
// 2026-10-06). OpenAI publishes no rates we know for the Codex models seen
// here (gpt-6-astra, codex-auto-review); they use GPT-5 API rates, an estimate.
// Cache writes cost 1.25x input (5-minute cache); cache reads use the listed
// rate where Anthropic gives one, otherwise 0.1x input.
interface Price { input: number; output: number; cacheRead: number }
const PRICES: [prefix: string, price: Price][] = [
  ["claude-fable-5-1", { input: 10, output: 50, cacheRead: 0.25 }],
  ["claude-mythos-5-1", { input: 10, output: 50, cacheRead: 0.25 }],
  ["claude-fable-5", { input: 10, output: 50, cacheRead: 1 }],
  ["claude-opus-5-5", { input: 4, output: 20, cacheRead: 0.2 }],
  ["claude-opus-5", { input: 5, output: 25, cacheRead: 0.5 }],
  ["claude-opus-4", { input: 5, output: 25, cacheRead: 0.5 }],
  ["claude-sonnet-5", { input: 2, output: 10, cacheRead: 0.2 }],
  ["claude-sonnet-4", { input: 3, output: 15, cacheRead: 0.3 }],
  ["claude-haiku-5-5", { input: 0.1, output: 0.5, cacheRead: 0.01 }],
  ["claude-haiku-4", { input: 1, output: 5, cacheRead: 0.1 }],
];
const OPENAI: Price = { input: 1.25, output: 10, cacheRead: 0.125 };
// A model we cannot place is priced as its harness's usual model.
const BY_AGENT: Record<string, Price> = { "claude-code": PRICES[3][1], codex: OPENAI };

export function priceOf(model: string | null, agent: string): Price {
  const name = model?.toLowerCase() ?? "";
  const known = PRICES.find(([prefix]) => name.startsWith(prefix));
  if (known) return known[1];
  if (/^(gpt|codex|o\d)/.test(name)) return OPENAI;
  return BY_AGENT[agent] ?? PRICES[3][1];
}

export function dollars(row: TokenRow): number {
  const p = priceOf(row.model, row.agent);
  return (row.input * p.input + row.cache_write * p.input * 1.25 + row.cache_read * p.cacheRead + row.output * p.output) / 1e6;
}

export function money(value: number): string {
  return value >= 1000 ? `$${Math.round(value).toLocaleString("en-US")}` : `$${value.toFixed(2)}`;
}
