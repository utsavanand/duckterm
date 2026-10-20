import type { MemoryPagination } from "./memoryViewApi";

export function validMemoryPage(page: MemoryPagination | undefined) {
  return !!page && typeof page.snapshot === "string" && !!page.snapshot
    && [page.total, page.offset, page.returned].every(n => Number.isSafeInteger(n) && n >= 0)
    && page.offset + page.returned <= page.total
    && (page.next_cursor === null || typeof page.next_cursor === "string" && !!page.next_cursor);
}
export function MemoryPages({ page, previous, onPrevious, onNext, noun }: { page: MemoryPagination; previous: boolean; onPrevious: () => void; onNext: (cursor: string) => void; noun: string }) {
  if (!validMemoryPage(page)) return <p role="alert">Saved-memory page information is unavailable.</p>;
  return <div className="rd-memory-pager"><p className="hint">{page.total ? `${page.offset + 1}–${page.offset + page.returned}` : "0"} of {page.total} {noun}</p><div>
    <button className="rd-btn rd-btn-sm" disabled={!previous} onClick={onPrevious}>Previous</button>
    <button className="rd-btn rd-btn-sm" disabled={!page.next_cursor} onClick={() => page.next_cursor && onNext(page.next_cursor)}>Next page</button>
  </div></div>;
}
