"""The sole progress-summary writer for explicit checkpoints and handoff preparation.

Capture on the DB owner thread, do provider/file I/O off it, then compare-and-swap
under one write lock. A canceled HTTP waiter cannot cancel a shared refresh.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core import memory_folding, progress, work_pointers
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import (
    checkpoint_marker,
    conversation,
    current_revision,
    event_source,
    fingerprint,
    transaction,
)
from duckterm.runtimes.native_history import MessageSource, NativeSnapshot

if TYPE_CHECKING:
    from duckterm.server import Server

POLICY = "maintained-progress-v6/summary-validator-v1"
MAX_REQUIRED_CHARS = 12000
# Reserve room for the summary and instructions inside the 24 KiB seed limit.
MAX_REQUIRED_BYTES = 20000


def policy_key() -> str:
    return fingerprint(
        {
            "version": POLICY,
            "enabled": os.environ.get("DUCKTERM_SUMMARIZER", "auto"),
            "command": os.environ.get("DUCKTERM_SUMMARIZER_CMD", ""),
            "url": os.environ.get("DUCKTERM_SUMMARIZER_URL", ""),
        }
    )


def file_version(path: str) -> list[int] | None:
    """Cheap synchronous final fence; ctime catches same-size/mtime rewrites."""
    try:
        stat = Path(path).stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except OSError:
        return None


def transcript_fence(source: MessageSource) -> dict[str, Any] | None:
    # Locator work belongs off-loop. Final stop checks stat only the captured path.
    from duckterm.harnesses import runtime_for

    if isinstance(source, NativeSnapshot):
        return {
            "path": source.path,
            "version": file_version(source.path),
            "source_kind": "native-api-cache",
            "scope": source.scope(),
        }
    if source is None or not source[2]:
        return None
    runtime = runtime_for(source[0], "")
    path = runtime.locate_transcript(cwd=Path(source[1]), session_id=source[2])
    if path is None:
        return None
    return {"path": str(path), "version": file_version(str(path))}


def required_context(server: Server, key: str, row: dict[str, Any]) -> dict[str, Any]:
    """Current permitted facts; never persist another memory copy of these texts."""
    conn = server.history._conn
    member = conn.execute(
        "SELECT root,folder,purpose FROM session_api_members WHERE session_key=?", (key,)
    ).fetchone()
    scope = dict(member) if member else {}
    # A moved card whose enrollment has not caught up has no peer-source grant.
    permitted = bool(scope.get("root") and scope.get("folder") == (row.get("grp") or ""))
    pointers = (
        work_pointers.capture(conn, key, scope)
        if permitted
        else {"tasks": [], "mail": [], "work_references": {"available": False}}
    )
    return {
        "name": row.get("name"),
        "purpose": scope.get("purpose"),
        "intention": row.get("intention"),
        "notes": row.get("notes"),
        "scope": scope,
        **pointers,
    }


@dataclass(frozen=True)
class SummaryUpdate:
    """Result of the shared update attempt; no extra summary writer or store."""

    revision: dict[str, Any] | None
    state: str
    reason: str | None = None
    diagnostic: str | None = None
    review: dict[str, Any] | None = None
    span_coverage: dict[str, Any] | None = None

    def metadata(self, attempted_at: int) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            **({"diagnostic": self.diagnostic} if self.diagnostic else {}),
            **({"review": self.review} if self.review is not None else {}),
            **({"span_coverage": self.span_coverage} if self.span_coverage else {}),
            "attempted_at": attempted_at,
            "revision_id": (
                self.revision["id"] if self.revision and self.state != "failed" else None
            ),
        }

    @classmethod
    def failed(
        cls,
        reason: str | None,
        *,
        diagnostic: str | None = None,
        review: dict[str, Any] | None = None,
    ) -> SummaryUpdate:
        allowed = {
            "disabled",
            "no_provider",
            "provider_timeout",
            "provider_failed",
            "invalid_summary",
            "invalid_context",
            "summary_unverified",
            "source_changed",
            "source_unavailable",
            "no_transcript",
            "update_failed",
        }
        return cls(
            None, "failed", reason if reason in allowed else "update_failed", diagnostic, review
        )

    @classmethod
    def from_revision(cls, value: dict[str, Any] | None, *, reused: bool = False) -> SummaryUpdate:
        if value is None:
            return cls.failed("source_changed")
        validation = value.get("summary_validation")
        continuity = value.get("continuity", {})
        if continuity.get("verified") and (
            continuity.get("gaps")
            or continuity.get("pending_updates")
            or ("update_complete" not in continuity and continuity.get("remaining_records"))
        ):
            return cls(value, "partial", "summary_coverage_partial")
        review = value.get("summary_review") or progress.summary_review(validation)
        stored_verdict = progress.summary_review(validation)
        if stored_verdict["state"] == "accepted":
            return cls(value, "reused" if reused else "updated")
        if review["state"] == "accepted":
            # A passing review does not override a failed source/coverage fence.
            diagnostic = "summary_not_ready" + (
                ":" + ",".join(stored_verdict["reason_codes"])
                if stored_verdict["reason_codes"]
                else ""
            )
            return cls(value, "failed", "summary_unverified", diagnostic, review)
        return cls(
            value, "failed", "summary_unverified", progress.review_diagnostic(review), review
        )


class ProgressCoordinator:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.pending: dict[str, asyncio.Task[SummaryUpdate]] = {}
        self.active: dict[str, asyncio.Task[SummaryUpdate]] = {}
        self.span_refreshes: dict[str, asyncio.Task[SummaryUpdate]] = {}
        self.span_refresh_inputs: dict[str, str] = {}
        self.span_waiters: dict[str, dict[object, Callable[[], bool] | None]] = {}
        self.span_status: dict[str, dict[str, int]] = {}
        self.span_progress: dict[str, dict[str, Any]] = {}
        self.span_observers: dict[str, dict[object, Callable[[dict[str, Any]], None]]] = {}

    def report_span_progress(self, key: str, **fields: Any) -> None:
        value = {**self.span_progress.get(key, {}), **fields}
        self.span_progress[key] = value
        for observer in tuple(self.span_observers.get(key, {}).values()):
            observer(dict(value))

    async def close(self) -> None:
        tasks = (
            set(self.span_refreshes.values())
            | set(self.active.values())
            | set(self.pending.values())
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def facts(self, key: str) -> dict[str, Any] | None:
        row = self.server.history.session(key)
        if row is None:
            return None
        required = required_context(self.server, key, row)
        return {
            "conversation": conversation(self.server.history, key, row),
            "events": event_source(self.server.history._conn, key, work_only=True),
            "required": required,
            "mail_boundary": work_pointers.mail_boundary(self.server.history._conn, key, required),
        }

    async def capture(self, key: str) -> dict[str, Any] | None:
        facts = self.facts(key)
        if facts is None:
            return None
        message_source = self.server._message_source(key)
        supervisor = self.server.orchestrator.get(key)

        def read() -> tuple[list[dict[str, str]], dict[str, Any] | None, bool]:
            before = transcript_fence(message_source)
            transcript = self.server._progress_transcript(message_source, supervisor)
            after = transcript_fence(message_source)
            return transcript, after, before == after

        transcript, fence, stable = await asyncio.to_thread(read)
        if not stable:
            return None
        if facts != self.facts(key):
            return None
        full = "\n".join(f"{r['role']}: {r['text']}" for r in transcript)
        required = json.dumps(facts["required"], ensure_ascii=False, sort_keys=True)
        source = {
            "events": facts["events"],
            "required_hash": fingerprint(facts["required"]),
            "mail_hash": facts["mail_boundary"]["hash"],
            "scope_hash": fingerprint(facts["required"]["scope"]),
            "transcript_hash": fingerprint(transcript),
            "transcript_chars": len(full),
            "transcript_used_chars": min(len(full), progress._MAX_TRANSCRIPT_CHARS),
            "transcript_kind": (
                "terminal" if any(r["role"] == "terminal" for r in transcript) else "native"
            ),
        }
        policy = policy_key()
        return {
            "source": source,
            "conversation": facts["conversation"],
            "policy": policy,
            "transcript": transcript,
            "required": required,
            "facts": facts,
            "captured_at": int(time.time() * 1000),
            "fence": fence,
        }

    async def refresh(
        self, key: str, captured: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        return (await self.refresh_result(key, captured)).revision

    async def refresh_result(
        self,
        key: str,
        captured: dict[str, Any] | None = None,
        *,
        continue_if: Callable[[], bool] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> SummaryUpdate:
        from duckterm import memory_continuity

        captured = captured or await self.capture(key)
        if captured is None:
            return SummaryUpdate.failed("source_changed", diagnostic="capture:unstable")
        try:
            if "checkpoint_selection" not in captured:
                captured = await memory_continuity.capture(self.server, key, captured)
        except APIError as exc:
            return SummaryUpdate.failed(
                "source_changed" if exc.status == 409 else "source_unavailable",
                diagnostic=(
                    "capture:memory_conflict" if exc.status == 409 else "capture:memory_unavailable"
                ),
            )
        if "checkpoint_selection" not in captured and self.facts(key) != captured["facts"]:
            return SummaryUpdate.failed("source_changed", diagnostic="capture:facts_changed")
        if "memory_sources" in captured:
            from duckterm.core import memory_refresh

            return await memory_refresh.refresh(
                self, key, captured, continue_if=continue_if, on_progress=on_progress
            )
        row = self.server.history.session(key)
        if row is None:
            return SummaryUpdate.failed("source_changed", diagnostic="capture:session_removed")
        prior_id = current_revision(row)
        prior = self.server.digests.revision(key, prior_id)
        identity = {k: captured[k] for k in ("source", "conversation", "policy")}
        if (
            prior
            and prior.get("summary_validation", {}).get("ready") is True
            and not prior.get("continuity", {}).get("remaining_records")
            and all(prior.get(k) == v for k, v in identity.items())
        ):
            return SummaryUpdate.from_revision(prior, reused=True)
        input_key = fingerprint({**identity, "prior_revision": prior_id, "session": key})
        existing = self.server.digests.find_revision(key, input_key)
        if (
            existing
            and existing.get("summary_validation", {}).get("ready") is True
            and not existing.get("continuity", {}).get("remaining_records")
        ):
            return SummaryUpdate.from_revision(existing, reused=True)
        task = self.pending.get(input_key)
        if task is None:
            active = self.active.get(key)
            if captured.get("memory_sources") is not None and active and not active.done():
                # A different manual/automatic trigger waits for the current update,
                # then captures fresh input. It cannot start another provider batch.
                await asyncio.shield(active)
                return await self.refresh_result(key)
            task = asyncio.create_task(self._generate(key, captured, prior, input_key))
            self.pending[input_key] = task
            self.active[key] = task

            def finished(done: asyncio.Task[SummaryUpdate]) -> None:
                self.pending.pop(input_key, None)
                if self.active.get(key) is done:
                    self.active.pop(key, None)
                if not done.cancelled():
                    done.exception()  # consume errors even if all HTTP waiters canceled

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def write_span(
        self, key: str, captured: dict[str, Any], lower: dict[str, Any]
    ) -> tuple[SummaryUpdate, dict[str, Any]]:
        """Stage a leaf through the sole writer, without activating it for handoff.

        The caller supplies an explicit original-record boundary and frozen capture.
        Reuse committed subranges on retry; no current-summary pointer is changed.
        The range coordinator publishes only after every required span completes.
        """
        from duckterm import memory_spans

        planned = memory_spans.plan(captured, lower)
        if captured.get("memory_gaps"):
            return SummaryUpdate.failed("source_unavailable"), planned
        if not planned["selected"]:
            return (
                SummaryUpdate.failed(
                    "invalid_context" if planned["oversized"] else "no_transcript",
                    diagnostic=(
                        "oversized_record" if planned["oversized"] else "no_new_span_records"
                    ),
                ),
                planned,
            )
        if "checkpoint_selection" in captured:
            from duckterm import memory_checkpoint

            if reason := memory_checkpoint.changed(self.server, key, captured):
                return (
                    SummaryUpdate.failed("source_changed", diagnostic="snapshot:" + reason),
                    planned,
                )
        elif self.facts(key) != captured["facts"]:
            return SummaryUpdate.failed("source_changed", diagnostic="span:facts_changed"), planned
        input_key = planned["input_key"]
        existing = self.server.digests.find_revision(key, input_key)
        if existing is not None:
            return SummaryUpdate.from_revision(existing, reused=True), planned
        saved = memory_folding.rows(self.server.digests, key, captured["source"]["scope_hash"])
        if any(memory_spans.overlaps(planned, revision) for revision in saved):
            return SummaryUpdate.failed("invalid_context", diagnostic="overlapping_span"), planned
        # Join the existing single-writer guard rather than introducing a queue.
        active = self.active.get(key)
        if active and not active.done():
            await asyncio.shield(active)
            return await self.write_span(key, captured, lower)
        row = self.server.history.session(key)
        prior = self.server.digests.revision(key, current_revision(row)) if row else None
        task = asyncio.create_task(
            self._generate(key, captured, prior, input_key, span_plan=planned)
        )
        self.active[key] = task
        try:
            return await asyncio.shield(task), planned
        except APIError as exc:
            return (
                SummaryUpdate.failed(
                    "source_changed" if exc.status == 409 else "source_unavailable",
                    diagnostic=(
                        "span:memory_conflict" if exc.status == 409 else "span:memory_unavailable"
                    ),
                ),
                planned,
            )
        finally:

            def finished(done: asyncio.Task[SummaryUpdate]) -> None:
                if self.active.get(key) is done:
                    self.active.pop(key, None)
                if not done.cancelled():
                    done.exception()

            if task.done():
                finished(task)
            else:
                task.add_done_callback(finished)

    async def _validation_capture(
        self, key: str, captured: dict[str, Any]
    ) -> dict[str, Any] | None:
        if "checkpoint_selection" in captured:
            from duckterm import memory_checkpoint

            if memory_checkpoint.changed(self.server, key, captured):
                return None
            return captured
        return await self.capture(key)

    async def _validation_memory(self, key: str, captured: dict[str, Any]) -> dict[str, Any]:
        if "checkpoint_selection" in captured:
            return captured
        from duckterm import memory_continuity

        return await memory_continuity.capture(self.server, key, captured)

    async def _draft(
        self, key: str, captured: dict[str, Any], maintained: dict[str, Any] | None, prompt: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | SummaryUpdate:
        from duckterm import memory_continuity, memory_summary
        from duckterm.server import summarize

        original = prompt
        for attempt in range(2 if maintained is not None else 1):
            if maintained and maintained.get("span") == "incremental":
                self.report_span_progress(key, stage="repairing" if attempt else "writing")
            reply = await asyncio.to_thread(
                summarize,
                prompt,
                **(
                    {"claude_model": captured["checkpoint_settings"]["writer_model"]}
                    if "checkpoint_settings" in captured
                    else {}
                ),
            )
            if failure := getattr(reply, "failure_reason", None):
                return SummaryUpdate.failed(failure)
            digest = progress.parse(reply.text)
            reason, diagnostic = "invalid_summary", progress.parse_diagnostic(reply.text)
            if digest and digest["summary"]:
                try:
                    context = (
                        memory_continuity.context(reply.text, maintained)
                        if maintained is not None
                        else None
                    )
                    return digest, context
                except memory_summary.InvalidContext as exc:
                    reason, diagnostic = "invalid_context", exc.reason
                except (ValueError, APIError):
                    reason, diagnostic = "invalid_context", "invalid_context_object"
            if maintained is None or attempt == 1:
                return SummaryUpdate.failed(
                    reason, diagnostic=diagnostic if maintained is not None else None
                )
            # Retry only a rejected draft, once, against exactly the same work.
            # Quota/auth/deadline errors return above without another provider call.
            current = await self._validation_capture(key, captured)
            if current is None:
                return SummaryUpdate.failed(
                    "source_changed", diagnostic="draft_retry:capture_unstable"
                )
            try:
                current = await self._validation_memory(key, current)
            except APIError:
                return SummaryUpdate.failed(
                    "source_changed", diagnostic="draft_retry:memory_conflict"
                )
            if any(current[k] != captured[k] for k in ("source", "conversation", "policy")):
                return SummaryUpdate.failed(
                    "source_changed", diagnostic="draft_retry:source_changed"
                )
            prompt = memory_continuity.repair_prompt(original, reply.text, diagnostic)
            if maintained and maintained.get("span") == "incremental":
                from duckterm import memory_spans

                if len(prompt.encode()) > memory_spans.MAX_INPUT_BYTES:
                    prompt = memory_continuity.repair_prompt(original, "", diagnostic)
                if len(prompt.encode()) > memory_spans.MAX_INPUT_BYTES:
                    return SummaryUpdate.failed("invalid_context", diagnostic=diagnostic)
        raise AssertionError("draft loop exhausted without a result")

    async def _generate(
        self,
        key: str,
        captured: dict[str, Any],
        prior: dict[str, Any] | None,
        input_key: str,
        *,
        span_plan: dict[str, Any] | None = None,
    ) -> SummaryUpdate:
        # Resolve on the server module to retain one injectable provider boundary.
        from duckterm import memory_continuity, memory_spans
        from duckterm.server import summarize

        if not captured["transcript"]:
            return SummaryUpdate.failed("no_transcript")
        row = self.server.history.session(key)
        if row is None:
            return SummaryUpdate.failed("source_changed", diagnostic="generation:session_removed")
        prior_cache = {}
        if (
            prior
            and prior.get("conversation") == captured["conversation"]
            and prior.get("source", {}).get("scope_hash") == captured["source"]["scope_hash"]
        ):
            prior_cache = json.loads(row.get("progress") or "{}")
        existing = [] if span_plan is not None else self.server.digests.items(key)[-200:]
        required = captured["required"]
        too_large = (
            len(required) > MAX_REQUIRED_CHARS or len(required.encode()) > MAX_REQUIRED_BYTES
        )
        bounded_required = (
            required[:MAX_REQUIRED_CHARS].encode()[:MAX_REQUIRED_BYTES].decode(errors="ignore")
        )
        try:
            maintained = (
                span_plan
                if span_plan is not None
                else (
                    memory_continuity.plan(captured, prior)
                    if "memory_sources" in captured
                    else None
                )
            )
        except (ValueError, APIError):
            return SummaryUpdate.failed("invalid_context", diagnostic="invalid_required_records")
        if (
            maintained is not None
            and not maintained["selected"]
            and maintained["prior_context"]
            and prior is not None
            and prior.get("source") == captured["source"]
            and not any(
                maintained["processed"].get(r["source"], {}).get(r["record"]) != r["hash"]
                for r in maintained["updates"]
            )
        ):
            # Nothing eligible fits this bounded update. Keep explicit gaps and
            # avoid paying to restate the same summary on every eligible turn.
            return SummaryUpdate.from_revision(prior, reused=True)
        if maintained is not None:
            # Original current-work records already occupy the bounded update.
            # Do not copy their full JSON into a second, smaller input budget.
            bounded_required = json.dumps(maintained["required_work"], ensure_ascii=False)
        prompt = (
            memory_spans.prompt(span_plan)
            if span_plan is not None
            else (
                memory_continuity.prompt(maintained)
                if maintained is not None
                else progress.build_prompt(captured["transcript"], prior_cache, bounded_required)
            )
        )
        draft_started = time.monotonic()
        drafted = await self._draft(key, captured, maintained, prompt)
        draft_ms = round((time.monotonic() - draft_started) * 1000)
        if isinstance(drafted, SummaryUpdate):
            return drafted
        digest, context = drafted
        policy_check = await self._validation_capture(key, captured)
        if policy_check is None or policy_check["policy"] != captured["policy"]:
            return SummaryUpdate.failed(
                "source_changed", diagnostic="review:policy_or_capture_changed"
            )
        validation_prompt = progress.validate_prompt(
            (
                {
                    **digest,
                    "summary": json.dumps({"summary": digest["summary"], "context": context}),
                }
                if context is not None
                else digest
            ),
            existing,
            source=prompt,
            required=bounded_required,
        )
        if span_plan is not None and len(validation_prompt.encode()) > memory_spans.MAX_INPUT_BYTES:
            return SummaryUpdate.failed("invalid_context", diagnostic="review_input_too_large")
        if span_plan is not None:
            self.report_span_progress(key, stage="reviewing")
        review_started = time.monotonic()
        verdict_reply = await asyncio.to_thread(
            summarize,
            validation_prompt,
            **(
                {"claude_model": captured["checkpoint_settings"]["reviewer_model"]}
                if "checkpoint_settings" in captured
                else {}
            ),
        )
        review_ms = round((time.monotonic() - review_started) * 1000)
        if failure_reason := getattr(verdict_reply, "failure_reason", None):
            return SummaryUpdate.failed(failure_reason)
        verdicts = progress.parse_verdicts(verdict_reply.text)
        review = progress.review_outcome(verdict_reply.text, verdicts)
        reasons = []
        if review["state"] != "accepted":
            reasons.append("summary_unverified")
        if too_large and maintained is None:
            reasons.append("required_context_too_large")
        source = captured["source"]
        if source["transcript_kind"] != "native":
            reasons.append("source_missing")
        if maintained is None and source["transcript_chars"] > source["transcript_used_chars"]:
            # A tail alone cannot certify a legacy conversation's older constraints.
            reasons.append("legacy_baseline_unreviewed")
        if (
            maintained is None
            and prior
            and prior.get("review", {}).get("kind") == "owner"
            and prior.get("conversation") == captured["conversation"]
        ):
            # Exact-boundary approval is never extended by an ordinary digest.
            reasons.append("legacy_baseline_unreviewed")
        baseline = None
        if prior and maintained is None:
            baseline = prior["id"] if prior.get("memory") else prior.get("memory_baseline_ref")
        if baseline and maintained is None:
            reasons.append("whole_history_preparation_required")
        # Re-read actual bytes, not mtime/size, after both provider calls.
        refreshed = await self._validation_capture(key, captured)
        if refreshed is None or refreshed["conversation"] != captured["conversation"]:
            return SummaryUpdate.failed(
                "source_changed", diagnostic="validation:conversation_or_capture_changed"
            )
        refreshed = await self._validation_memory(key, refreshed)
        if refreshed["policy"] != captured["policy"]:
            return SummaryUpdate.failed("source_changed", diagnostic="validation:policy_changed")
        if refreshed["source"] != captured["source"]:
            reasons.append("source_changed")
        continuity = None
        retained: dict[str, Any] = {}
        if maintained is not None and context is not None:
            # Failures do not advance coverage or replace the last good revision.
            if reasons:
                return SummaryUpdate.failed(
                    "source_changed" if "source_changed" in reasons else "summary_unverified",
                    diagnostic=(
                        progress.review_diagnostic(review)
                        if "summary_unverified" in reasons
                        else "validation:source_changed"
                    ),
                    review=review,
                )
            continuity = (
                memory_spans.continuity(span_plan, context)
                if span_plan is not None
                else memory_continuity.result(maintained, context, captured["memory_gaps"])
            )
            retained = await memory_continuity.retain(key, captured, maintained)
            final = await self._validation_capture(key, captured)
            if final is None:
                return SummaryUpdate.failed(
                    "source_changed", diagnostic="retention:capture_unstable"
                )
            final = await self._validation_memory(key, final)
            if any(final[k] != captured[k] for k in ("source", "conversation", "policy")):
                return SummaryUpdate.failed("source_changed", diagnostic="retention:source_changed")
            if not continuity["update_complete"]:
                reasons.append("summary_coverage_partial")
        accepted = verdicts["accept"] if verdicts else []
        candidates = progress.candidate_items(digest)
        accepted = [item for item in accepted if item in candidates]
        value = {
            **{k: captured[k] for k in ("source", "conversation", "policy")},
            "source_at": captured["captured_at"],
            **(
                {"checkpoint_settings": captured["checkpoint_settings"]}
                if "checkpoint_settings" in captured
                else {}
            ),
            "input_key": input_key,
            "prior_revision": prior["id"] if prior else None,
            "summary": digest["summary"],
            **({"continuity": continuity, **retained} if continuity is not None else {}),
            **({"memory_baseline_ref": baseline} if baseline else {}),
            "summary_validation": {"ready": not reasons, "reason_codes": reasons},
            "summary_review": review,
            "generation_timing_ms": {"draft_including_repairs": draft_ms, "review": review_ms},
            **(memory_spans.metadata(span_plan) if span_plan is not None else {}),
        }
        if span_plan is not None:
            self.report_span_progress(key, stage="checking")
        folding = await memory_folding.prepare(self, key, captured, value)
        if folding is not None:
            # Optional model work ran after the summary's fence. Revalidate it
            # before any parent links or the incoming summary can be committed.
            current = await self._validation_capture(key, captured)
            if current is None:
                return SummaryUpdate.failed("source_changed", diagnostic="fold:capture_unstable")
            current = await self._validation_memory(key, current)
            if any(current[k] != captured[k] for k in ("source", "conversation", "policy")):
                return SummaryUpdate.failed("source_changed", diagnostic="fold:source_changed")
        result = self.persist(
            key,
            captured,
            prior,
            value,
            digest,
            accepted,
            verdicts["done_next_action_ids"] if verdicts else [],
            folding=folding,
            stage_only=span_plan is not None,
            require_current=span_plan is not None,
        )
        if result is None:
            return SummaryUpdate.failed("source_changed", diagnostic="span:persistence_conflict")
        return SummaryUpdate.from_revision(result)

    def persist(
        self,
        key: str,
        captured: dict[str, Any],
        prior: dict[str, Any] | None,
        value: dict[str, Any],
        digest: dict[str, Any],
        accepted: list[dict[str, str]],
        done: list[str],
        *,
        require_current: bool = False,
        checkpoint: dict[str, Any] | None = None,
        folding: dict[str, Any] | None = None,
        stage_only: bool = False,
    ) -> dict[str, Any] | None:
        """Single atomic write path, including explicitly owner-reviewed baselines."""
        from duckterm import memory_checkpoint

        now = int(time.time() * 1000)
        conn = self.server.history._conn
        with transaction(conn):
            latest = self.server.history.session(key)
            if (
                latest is None
                or conversation(self.server.history, key, latest) != captured["conversation"]
                or policy_key() != captured["policy"]
            ):
                return None
            if (
                event_source(conn, key, captured["source"]["events"]["last"], work_only=True)
                != captured["source"]["events"]
            ):
                return None
            # A same-generation late result never overwrites a newer revision.
            promote = (
                current_revision(latest) == (prior["id"] if prior else None)
                and (
                    not memory_checkpoint.changed(self.server, key, captured)
                    if "checkpoint_selection" in captured
                    else self.facts(key) == captured["facts"]
                )
                and "source_changed" not in value["summary_validation"]["reason_codes"]
            )
            if require_current and not promote:
                return None
            if stage_only:
                from duckterm import memory_spans

                # Another coordinator/process may have committed while providers ran.
                duplicate = self.server.digests.find_revision(key, value["input_key"])
                if duplicate is not None:
                    return duplicate
                saved = memory_folding.rows(
                    self.server.digests, key, captured["source"]["scope_hash"]
                )
                if any(memory_spans.overlaps(value["span_input"], revision) for revision in saved):
                    return None
            if promote and not stage_only:
                self.server.digests.merge(key, accepted, done, now, commit=False)
            if folding is not None:
                status = (
                    memory_folding.apply(self.server.digests, key, folding, now)
                    if promote
                    else {"state": "pending", "reason": "source_changed"}
                )
                value = {**value, "compaction": status}
            refs = [{"id": r["id"], "status": r["status"]} for r in self.server.digests.items(key)]
            revision_id = self.server.digests.add_revision(key, {**value, "item_refs": refs}, now)
            if checkpoint is not None:
                record = checkpoint_marker(captured, revision_id, checkpoint["git"], now)
                if "memory_sources" in checkpoint:
                    record["memory_sources"] = checkpoint["memory_sources"]
                conn.execute(
                    "INSERT INTO checkpoints "
                    "(id,session_key,label,summary,record_json,markdown_path,created_at) "
                    "VALUES (?,?,?,'',?,NULL,?)",
                    (checkpoint["id"], key, checkpoint["label"], json.dumps(record), now),
                )
            # Keep a last good summary on failure. First unverified progress may
            # be displayed, but can never certify a handoff.
            if (
                promote
                and not stage_only
                and (
                    value["summary_validation"]["ready"]
                    or value.get("continuity", {}).get("verified")
                    or value.get("memory_baseline_ref")
                    or prior is None
                    or not prior["summary_validation"]["ready"]
                )
            ):
                cache = {**digest, "revision_id": revision_id}
                conn.execute(
                    "UPDATE sessions SET progress=?,progress_at=?,outcome_summary=? "
                    "WHERE session_key=?",
                    (json.dumps(cache), now, digest["summary"], key),
                )
        return self.server.digests.revision(key, revision_id)
