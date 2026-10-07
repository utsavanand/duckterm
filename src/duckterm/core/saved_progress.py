"""The sole progress-summary writer for turn end, exit and checkpoints.

Capture on the DB owner thread, do provider/file I/O off it, then compare-and-swap
under one write lock. A canceled HTTP waiter cannot cancel a shared refresh.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm.core import progress
from duckterm.persistence.saved_state import (
    checkpoint_marker,
    conversation,
    current_revision,
    event_source,
    fingerprint,
    transaction,
)

if TYPE_CHECKING:
    from duckterm.server import Server

POLICY = "maintained-progress-v3/summary-validator-v1"
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


def transcript_fence(source: tuple[str, str, str | None] | None) -> dict[str, Any] | None:
    # Locator work belongs off-loop. Final stop checks stat only the captured path.
    from duckterm.harnesses import runtime_for

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
    tasks = []
    mail = []
    if permitted:
        tasks = [
            dict(r)
            for r in conn.execute(
                "SELECT id,title,note,status FROM folder_tasks WHERE owner_session=? "
                "AND status IN ('in_progress','parked') "
                "AND (folder=? OR substr(folder,1,length(?)+1)=?||'/') ORDER BY id",
                (key, scope["root"], scope["root"], scope["root"]),
            )
        ]
        mail = [
            dict(r)
            for r in conn.execute(
                "SELECT q.id,q.sender,q.question,q.answer,q.status FROM session_questions q "
                "WHERE q.recipient=? AND q.root=? AND ((q.status IN ('queued','accepted')) "
                "OR (q.sender='owner' AND q.status='answered')) AND (q.sender='owner' OR "
                "EXISTS (SELECT 1 FROM session_api_members m JOIN sessions s "
                "ON s.session_key=m.session_key WHERE m.session_key=q.sender "
                "AND m.root=? AND m.folder=coalesce(s.grp,''))) ORDER BY q.rowid",
                (key, scope["root"], scope["root"]),
            )
        ]
    return {
        "name": row.get("name"),
        "purpose": scope.get("purpose"),
        "intention": row.get("intention"),
        "notes": row.get("notes"),
        "scope": scope,
        "tasks": tasks,
        "mail": mail,
    }


class ProgressCoordinator:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.pending: dict[str, asyncio.Task[dict[str, Any] | None]] = {}
        self.active: dict[str, asyncio.Task[dict[str, Any] | None]] = {}

    def facts(self, key: str) -> dict[str, Any] | None:
        row = self.server.history.session(key)
        if row is None:
            return None
        return {
            "conversation": conversation(self.server.history, key, row),
            "events": event_source(self.server.history._conn, key, work_only=True),
            "required": required_context(self.server, key, row),
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
        from duckterm import memory_continuity

        captured = captured or await self.capture(key)
        if captured is None:
            return None
        captured = await memory_continuity.capture(self.server, key, captured)
        if self.facts(key) != captured["facts"]:
            return None
        row = self.server.history.session(key)
        if row is None:
            return None
        prior_id = current_revision(row)
        prior = self.server.digests.revision(key, prior_id)
        identity = {k: captured[k] for k in ("source", "conversation", "policy")}
        if (
            prior
            and prior.get("summary_validation", {}).get("ready") is True
            and all(prior.get(k) == v for k, v in identity.items())
        ):
            return prior
        input_key = fingerprint({**identity, "prior_revision": prior_id, "session": key})
        existing = self.server.digests.find_revision(key, input_key)
        if existing and existing.get("summary_validation", {}).get("ready") is True:
            return existing
        task = self.pending.get(input_key)
        if task is None:
            active = self.active.get(key)
            if captured.get("memory_sources") is not None and active and not active.done():
                # A different manual/automatic trigger waits for the current update,
                # then captures fresh input. It cannot start another provider batch.
                await asyncio.shield(active)
                return await self.refresh(key)
            task = asyncio.create_task(self._generate(key, captured, prior, input_key))
            self.pending[input_key] = task
            self.active[key] = task

            def finished(done: asyncio.Task[dict[str, Any] | None]) -> None:
                self.pending.pop(input_key, None)
                if self.active.get(key) is done:
                    self.active.pop(key, None)
                if not done.cancelled():
                    done.exception()  # consume errors even if all HTTP waiters canceled

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _generate(
        self,
        key: str,
        captured: dict[str, Any],
        prior: dict[str, Any] | None,
        input_key: str,
    ) -> dict[str, Any] | None:
        # Resolve on the server module to retain one injectable provider boundary.
        from duckterm import memory_continuity
        from duckterm.server import summarize

        if not captured["transcript"]:
            return None
        row = self.server.history.session(key)
        if row is None:
            return None
        prior_cache = {}
        if (
            prior
            and prior.get("conversation") == captured["conversation"]
            and prior.get("source", {}).get("scope_hash") == captured["source"]["scope_hash"]
        ):
            prior_cache = json.loads(row.get("progress") or "{}")
        existing = self.server.digests.items(key)[-200:]
        required = captured["required"]
        too_large = (
            len(required) > MAX_REQUIRED_CHARS or len(required.encode()) > MAX_REQUIRED_BYTES
        )
        bounded_required = (
            required[:MAX_REQUIRED_CHARS].encode()[:MAX_REQUIRED_BYTES].decode(errors="ignore")
        )
        maintained = (
            memory_continuity.plan(captured, prior) if "memory_sources" in captured else None
        )
        if maintained is not None and not maintained["selected"] and maintained["prior_context"]:
            # Nothing eligible fits this bounded update. Keep explicit gaps and
            # avoid paying to restate the same summary on every eligible turn.
            return prior
        prompt = (
            memory_continuity.prompt(maintained, bounded_required)
            if maintained is not None
            else progress.build_prompt(captured["transcript"], prior_cache, bounded_required)
        )
        reply = await asyncio.to_thread(summarize, prompt)
        digest = progress.parse(reply.text)
        if digest is None or not digest["summary"]:
            return None
        context = memory_continuity.context(reply.text, maintained) if maintained else None
        policy_check = await self.capture(key)
        if policy_check is None or policy_check["policy"] != captured["policy"]:
            return None
        verdict_reply = await asyncio.to_thread(
            summarize,
            progress.validate_prompt(
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
            ),
        )
        verdicts = progress.parse_verdicts(verdict_reply.text)
        validation = (verdicts or {}).get("summary_validation") or {}
        reasons = []
        if validation.get("ready") is not True or validation.get("reason_codes") != []:
            reasons.append("summary_unverified")
        if too_large:
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
        refreshed = await self.capture(key)
        if refreshed is None or refreshed["conversation"] != captured["conversation"]:
            return None
        refreshed = await memory_continuity.capture(self.server, key, refreshed)
        if refreshed["policy"] != captured["policy"]:
            return None
        if refreshed["source"] != captured["source"]:
            reasons.append("source_changed")
        continuity = None
        retained: dict[str, Any] = {}
        if maintained is not None and context is not None:
            # Failures do not advance coverage or replace the last good revision.
            if reasons:
                return None
            continuity = memory_continuity.result(maintained, context, captured["memory_gaps"])
            retained = await memory_continuity.retain(key, captured, maintained)
            final = await self.capture(key)
            if final is None:
                return None
            final = await memory_continuity.capture(self.server, key, final)
            if any(final[k] != captured[k] for k in ("source", "conversation", "policy")):
                return None
            if continuity["remaining_records"] or continuity["gaps"]:
                reasons.append("summary_coverage_partial")
        accepted = verdicts["accept"] if verdicts else []
        candidates = progress.candidate_items(digest)
        accepted = [item for item in accepted if item in candidates]
        value = {
            **{k: captured[k] for k in ("source", "conversation", "policy")},
            "source_at": captured["captured_at"],
            "input_key": input_key,
            "prior_revision": prior["id"] if prior else None,
            "summary": digest["summary"],
            **({"continuity": continuity, **retained} if continuity is not None else {}),
            **({"memory_baseline_ref": baseline} if baseline else {}),
            "summary_validation": {"ready": not reasons, "reason_codes": reasons},
        }
        return self.persist(
            key,
            captured,
            prior,
            value,
            digest,
            accepted,
            verdicts["done_next_action_ids"] if verdicts else [],
        )

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
    ) -> dict[str, Any] | None:
        """Single atomic write path, including explicitly owner-reviewed baselines."""
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
                and self.facts(key) == captured["facts"]
                and "source_changed" not in value["summary_validation"]["reason_codes"]
            )
            if require_current and not promote:
                return None
            if promote:
                self.server.digests.merge(key, accepted, done, now, commit=False)
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
            if promote and (
                value["summary_validation"]["ready"]
                or value.get("continuity", {}).get("verified")
                or value.get("memory_baseline_ref")
                or prior is None
                or not prior["summary_validation"]["ready"]
            ):
                cache = {**digest, "revision_id": revision_id}
                conn.execute(
                    "UPDATE sessions SET progress=?,progress_at=?,outcome_summary=? "
                    "WHERE session_key=?",
                    (json.dumps(cache), now, digest["summary"], key),
                )
        return self.server.digests.revision(key, revision_id)
