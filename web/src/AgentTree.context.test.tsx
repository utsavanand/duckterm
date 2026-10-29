import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentTree } from "./AgentTree";
import { EMPTY_FILTERS } from "./sidebarFilterState";
import { edition, FULL, LITE } from "./test/editions";
import { viewFromPersisted } from "./types";

afterEach(cleanup);
it.each([
  { capacity: 128_000, tokens: 110_000, features: FULL, level: "high" },
  { capacity: 128_000, tokens: 75_000, features: FULL, level: "warm" },
  { capacity: undefined, tokens: 110_000, features: FULL, level: null },
  { capacity: 128_000, tokens: 110_000, features: LITE, level: null },
])("uses reported capacity in existing sidebar warnings ($level, $capacity)", ({ capacity, tokens, features, level }) => {
  const session = viewFromPersisted({ session_key: "probe", name: "Context probe", state: "busy", event_count: 1,
    started_at: 1, updated_at: 2, runtime: "codex", model: "unlisted-codex-model", context_tokens: tokens, context_window_tokens: capacity });
  const { container } = render(<AgentTree sessions={[session]} now={3} folders={[]} selectedKey={null}
    filterControls={{ filters: EMPTY_FILTERS, toggle: vi.fn(), clear: vi.fn(), saveError: false, expanded: false, toggleExpanded: vi.fn() }}
    onOpen={vi.fn()} onFoldersChanged={vi.fn()} onSessionMoved={vi.fn()} onOpenGrid={vi.fn()} onNewSessionIn={vi.fn()} folderThemes={{}} />,
    { wrapper: edition(features) });
  const row = container.querySelector('[data-session-key="probe"]');
  if (level) {
    expect(row).toHaveClass(`ctx-${level}`);
    expect(row?.querySelector(".rd-ctx-chip")).toHaveClass(level);
  } else {
    expect(row).not.toHaveClass("ctx-high");
    expect(row).not.toHaveClass("ctx-warm");
    expect(row?.querySelector(".rd-ctx-chip")).toBeNull();
  }
});
