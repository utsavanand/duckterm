"""Owner-requested memory preparation. No process is stopped in this module."""

from __future__ import annotations

import asyncio
import copy
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duckterm import memory_sources, memory_summary
from duckterm.core.saved_progress import file_version, policy_key
from duckterm.core.session_api import APIError
from duckterm.persistence.checkpoints import _git_state
from duckterm.persistence.saved_state import conversation, current_revision, fingerprint

if TYPE_CHECKING:
    from duckterm.server import Server

LIFETIME_MS = 1800000
MAX_JOBS = 64


class PreparationError(APIError):
    def __init__(self, status: int, code: str, text: str) -> None:
        super().__init__(status, text)
        self.code = code


def generation(server: Server, key: str) -> str:
    row = server.history.session(key)
    if row is None:
        raise APIError(404, "Session not found")
    value = conversation(server.history, key, row)
    return value.get("generation") or fingerprint(value)


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", value):
        raise APIError(400, "Invalid request identity")
    return value


class MemoryPreparation:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.jobs: dict[str, dict[str, Any]] = {}
        self.requests: dict[tuple[str, str], str] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.workers = asyncio.Semaphore(2)

    def get(self, key: str, identity: str) -> dict[str, Any]:
        job = self.jobs.get(identity)
        if not job or job["key"] != key or job["expires"] < time.time() * 1000:
            raise PreparationError(409, "preparation_expired", "Preparation expired; prepare again")
        if self.server.history.session(key) is None:
            raise PreparationError(404, "stale_source", "Session was removed")
        return job

    def view(self, job: dict[str, Any], request_key: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = copy.deepcopy(job["view"])
        result["request_key"] = request_key or next(iter(job["leases"]), "")
        if result["state"] == "ready":
            result["proof"].pop("brief", None)
        else:
            # A later invalidation must not expose the old proof (including
            # its private full brief) through the lightweight status route.
            result.pop("proof", None)
        return result

    def update(self, job: dict[str, Any], **value: Any) -> None:
        view = job["view"]
        view.update(value, sequence=view["sequence"] + 1)

    def cheap_validate(self, job: dict[str, Any]) -> None:
        if job["canceled"]:
            raise PreparationError(409, "preparation_expired", "Preparation was canceled")
        if job["expires"] <= time.time() * 1000:
            raise PreparationError(409, "preparation_expired", "Preparation expired; prepare again")
        key = job["key"]
        if generation(self.server, key) != job["binding"]["source_generation"]:
            raise PreparationError(409, "stale_source", "The conversation changed; prepare again")
        captured = job.get("captured")
        if captured and (
            self.server.progress_coordinator.facts(key) != captured["facts"]
            or policy_key() != captured["policy"]
        ):
            raise PreparationError(409, "stale_source", "Current work changed; prepare again")
        if job.get("catalog") and fingerprint(self.server.memory.catalog(key)) != fingerprint(
            job["catalog"]
        ):
            raise PreparationError(409, "stale_source", "Available history or permissions changed")
        for source in job.get("sources", []):
            if source.get("fence") and file_version(source["path"]) != source["fence"]:
                raise PreparationError(409, "stale_source", "A conversation changed; prepare again")

    async def start(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"request_key", "binding"}:
            raise APIError(400, "Supply request_key and binding")
        request_key = identifier(body["request_key"])
        binding = body["binding"]
        if not isinstance(binding, dict) or set(binding) != {
            "session_key",
            "source_generation",
            "target",
        }:
            raise APIError(400, "Invalid preparation binding")
        if binding["session_key"] != key:
            raise APIError(400, "Preparation belongs to another session")
        existing_id = self.requests.get((key, request_key))
        if existing_id:
            job = self.get(key, existing_id)
            if job["binding"] != binding:
                raise PreparationError(
                    409, "operation_conflict", "Request key was used for another target"
                )
            return self.view(job, request_key)
        target = binding["target"]
        if not isinstance(target, dict) or set(target) != {"harness", "model"}:
            raise APIError(400, "Invalid target")
        model = target["model"]
        if not isinstance(model, dict) or not (
            model == {"mode": "default"}
            or (
                set(model) == {"mode", "id"}
                and model["mode"] == "explicit"
                and isinstance(model["id"], str)
                and 0 < len(model["id"]) <= 200
                and not any(ord(c) < 32 for c in model["id"])
            )
        ):
            raise APIError(400, "Invalid target model")
        if not isinstance(target["harness"], str) or target["harness"] not in {
            "codex",
            "claude-code",
        }:
            raise PreparationError(
                409, "unsupported", "Memory preparation is unavailable for this harness"
            )
        if generation(self.server, key) != binding["source_generation"]:
            raise PreparationError(409, "stale_source", "Conversation changed; reopen Restart")
        _, _, _, binary = await self.server.restarts.live_plan(key, target["harness"])
        cli_version = await self.server.restarts.switch_version(target["harness"], binary)
        async with self.locks.setdefault(key, asyncio.Lock()):
            # The target/CLI probes yielded. Another dialog may have claimed
            # this retry key while they were in flight.
            existing_id = self.requests.get((key, request_key))
            if existing_id:
                job = self.get(key, existing_id)
                if job["binding"] != binding:
                    raise PreparationError(
                        409, "operation_conflict", "Request key was used for another target"
                    )
                return self.view(job, request_key)
            if generation(self.server, key) != binding["source_generation"]:
                raise PreparationError(409, "stale_source", "Conversation changed; reopen Restart")
            # Same binding shares work and independent caller leases. Each new
            # request has its own key; releasing one cannot cancel another.
            for job in self.jobs.values():
                if (
                    job["key"] == key
                    and job["binding"] == binding
                    and job["cli_version"] == cli_version
                    and job["view"]["state"] in {"preparing", "ready"}
                ):
                    try:
                        self.cheap_validate(job)
                    except APIError:
                        continue
                    job["leases"].add(request_key)
                    self.requests[(key, request_key)] = job["id"]
                    return self.view(job, request_key)
            for identity, old in list(self.jobs.items()):
                if old["expires"] <= time.time() * 1000 and identity not in self.tasks:
                    self.jobs.pop(identity)
                    self.requests = {k: v for k, v in self.requests.items() if v != identity}
            if len(self.jobs) >= MAX_JOBS:
                raise APIError(429, "Too many preparations; cancel unused dialogs and retry")
            identity = uuid.uuid4().hex
            job = {
                "id": identity,
                "key": key,
                "binding": copy.deepcopy(binding),
                "expires": time.time() * 1000 + LIFETIME_MS,
                "canceled": False,
                "cli_version": cli_version,
                "leases": {request_key},
                "view": {
                    "version": 1,
                    "preparation_id": identity,
                    "sequence": 0,
                    "binding": copy.deepcopy(binding),
                    "state": "preparing",
                    "phase": "resolving",
                    "coverage": {
                        "state": "unknown",
                        "available_text": "not_processed",
                        "retrieval": "unavailable",
                        "source_count": 0,
                        "covered_source_count": 0,
                        "gap_count": 0,
                        "gaps": [],
                        "has_more": False,
                        "details_cursor": None,
                        "retention": "unknown",
                    },
                },
            }
            self.jobs[identity] = job
            self.requests[(key, request_key)] = identity
            task = asyncio.create_task(self.worker(job))
            self.tasks[identity] = task
            task.add_done_callback(lambda t: self.tasks.pop(identity, None))
            return self.view(job, request_key)

    async def worker(self, job: dict[str, Any]) -> None:
        async with self.workers:
            if job["canceled"]:
                return
            await self.run(job)

    def resumable_progress(self, job: dict[str, Any]) -> memory_summary.BatchProgress | None:
        candidates: list[memory_summary.BatchProgress] = []
        for old in self.jobs.values():
            if (
                old is not job
                and old["id"] not in self.tasks
                and old.get("partial") is not None
                and old["expires"] > time.time() * 1000
                and old["key"] == job["key"]
                and old["binding"] == job["binding"]
                and old["cli_version"] == job["cli_version"]
                and old["catalog"] == job["catalog"]
                and all(
                    old["captured"][k] == job["captured"][k]
                    for k in ("facts", "policy", "source", "conversation")
                )
            ):
                candidates.append(old["partial"])
        return max(candidates, key=lambda p: p.completed) if candidates else None

    def retain_progress(self, job: dict[str, Any], progress: memory_summary.BatchProgress) -> None:
        self.cheap_validate(job)
        job["partial"] = progress
        # Expire abandoned/stalled jobs, not a long history that keeps passing
        # review. Every provider call still has its own bounded timeout.
        job["expires"] = time.time() * 1000 + LIFETIME_MS

    async def run(self, job: dict[str, Any]) -> None:
        key = job["key"]
        coordinator = self.server.progress_coordinator
        try:
            captured = await coordinator.capture(key)
            if captured is None:
                raise PreparationError(409, "stale_source", "Conversation changed during capture")
            job["captured"] = captured
            job["catalog"] = catalog = self.server.memory.catalog(key)
            self.update(job, phase="reading")
            async with self.server.memory.locks.setdefault(key, asyncio.Lock()):
                sources, missing = await asyncio.to_thread(self.server.memory.materialize, catalog)
            job["sources"] = sources
            gaps = [
                {"source_id": s["id"], "kind": "missing", "reason": s["reason"], "blocking": True}
                for s in missing
            ]
            for source in sources:
                if source.get("unprocessed_attachments"):
                    gaps.append(
                        {
                            "source_id": source["id"],
                            "kind": "unread_attachment",
                            "reason": (
                                "Attachment contents were not interpreted; "
                                "text/tool records remain available"
                            ),
                            "blocking": False,
                        }
                    )
            coverage = {
                "state": "partial" if gaps else "complete",
                "available_text": "not_processed",
                "retrieval": "partial" if missing else "available",
                "source_count": len(sources) + len(missing),
                "covered_source_count": 0,
                "gap_count": len(gaps),
                "gaps": gaps[:50],
                "has_more": len(gaps) > 50,
                "details_cursor": "50" if len(gaps) > 50 else None,
                "retention": "native_conditional",
            }
            job["gaps"] = gaps
            self.update(job, coverage=coverage)
            if missing:
                raise PreparationError(
                    409, "incomplete_source", "Some conversation sources are unavailable"
                )
            self.cheap_validate(job)
            row = self.server.history.session(key) or {}
            prior = self.server.digests.revision(key, current_revision(row))
            baseline = prior
            if prior and prior.get("memory_baseline_ref"):
                baseline = self.server.digests.revision(key, prior["memory_baseline_ref"])
            if baseline and baseline.get("memory", {}).get("cli_version") != job["cli_version"]:
                baseline = None
            self.update(job, phase="summarizing")
            context, pieces = await memory_summary.summarize(
                sources,
                job["binding"]["target"],
                baseline,
                lambda: self.cheap_validate(job),
                lambda completed, total, step: self.update(
                    job,
                    progress={"completed_batches": completed, "total_batches": total, "step": step},
                ),
                resume=self.resumable_progress(job),
                retain=lambda value: self.retain_progress(job, value),
            )
            self.update(job, phase="validating")
            seed = memory_summary.brief(context, captured["required"])
            retained = []
            for source in sources:
                if source["kind"] == "conversation":
                    saved = await asyncio.to_thread(
                        memory_sources.read_native, key, source, retain=True
                    )
                    retained.append(
                        {
                            k: saved[k]
                            for k in ("id", "runtime", "native_id", "cwd", "path", "snapshot")
                        }
                    )
                    retained[-1]["root"] = catalog["root"]
            git = await asyncio.to_thread(
                _git_state, Path(str(row.get("worktree_path") or row.get("cwd") or "."))
            )
            current = await coordinator.capture(key)
            self.cheap_validate(job)
            if current is None or any(
                current[k] != captured[k] for k in ("source", "conversation", "policy")
            ):
                raise PreparationError(409, "stale_source", "Sources changed during preparation")
            checkpoint_id = uuid.uuid4().hex
            coverage.update(
                available_text="processed",
                covered_source_count=len(sources),
                retention="retained_snapshot",
            )
            value = {
                **{k: captured[k] for k in ("source", "conversation", "policy")},
                "source_at": captured["captured_at"],
                "input_key": fingerprint(
                    [job["binding"], pieces, captured["source"], captured["policy"]]
                ),
                "prior_revision": prior["id"] if prior else None,
                "summary": context["overview"],
                "summary_validation": {
                    "ready": True,
                    "reason_codes": [],
                    "method": "whole-source-generation-and-validation",
                },
                "memory": {
                    "policy": memory_summary.POLICY,
                    "cli_version": job["cli_version"],
                    "target": job["binding"]["target"],
                    "pieces": pieces,
                    "context": context,
                    "coverage": coverage,
                    "checkpoint_id": checkpoint_id,
                },
            }
            digest = {
                "summary": context["overview"],
                "deliverables": [],
                "learnings": [],
                "user_learnings": [],
                "next_actions": [],
            }
            saved_revision = coordinator.persist(
                key,
                captured,
                prior,
                value,
                digest,
                [],
                [],
                require_current=True,
                checkpoint={
                    "id": checkpoint_id,
                    "label": "Prepared harness switch",
                    "git": git,
                    "memory_sources": retained,
                },
            )
            if saved_revision is None:
                raise PreparationError(
                    409, "stale_source", "Progress changed while preparing; prepare again"
                )
            checkpoint = next(
                cp for cp in self.server.history.checkpoints(key) if cp["id"] == checkpoint_id
            )
            await self.server._export_checkpoint(key, checkpoint)
            job["checkpoint_id"] = checkpoint_id
            job["seed"] = seed
            # The new marker has added retained references. Bind readiness to
            # that authoritative registry and validate its retained bytes too.
            job["catalog"] = self.server.memory.catalog(key)
            async with self.server.memory.locks.setdefault(key, asyncio.Lock()):
                job["sources"], missing = await asyncio.to_thread(
                    self.server.memory.materialize, job["catalog"]
                )
            self.cheap_validate(job)
            if missing:
                raise PreparationError(
                    409, "incomplete_source", "Retained history could not be verified"
                )
            job["sources"] = [
                {k: v for k, v in source.items() if k != "records"} for source in job["sources"]
            ]
            snapshot = fingerprint(
                [job["binding"], value["input_key"], job["catalog"], job["cli_version"]]
            )
            job["expires"] = time.time() * 1000 + LIFETIME_MS
            self.update(
                job,
                state="ready",
                coverage=coverage,
                proof={
                    "snapshot_id": snapshot,
                    "revision_id": saved_revision["id"],
                    "prepared_at": int(time.time() * 1000),
                    "expires_at": int(job["expires"]),
                    "resolved_model": job["binding"]["target"]["model"].get("id"),
                    "overview": context["overview"],
                    "brief": {
                        "text": seed,
                        "utf8_bytes": len(seed.encode()),
                        "budget_bytes": memory_summary.MAX_BRIEF_BYTES,
                    },
                },
            )
            job.pop("partial", None)
        except asyncio.CancelledError:
            job.pop("partial", None)
            self.update(job, state="canceled")
        except Exception as exc:
            code = exc.code if isinstance(exc, PreparationError) else "preparation_failed"
            state = code if code in {"stale_source", "incomplete_source"} else "failed"
            if job["canceled"]:
                state = "canceled"
            if state != "failed":
                job.pop("partial", None)
            self.update(
                job,
                state=state,
                reason=str(exc) if isinstance(exc, APIError) else "Memory preparation failed",
                retryable=state == "failed",
            )

    def status(self, key: str, identity: str) -> dict[str, Any]:
        job = self.get(key, identity)
        if job["view"]["state"] == "ready":
            try:
                self.cheap_validate(job)
            except APIError as exc:
                self.update(job, state="stale_source", reason=str(exc))
        return self.view(job)

    def details(self, key: str, identity: str, cursor: str = "0") -> dict[str, Any]:
        view = self.status(key, identity)
        job = self.get(key, identity)
        if view["state"] != "ready":
            raise PreparationError(409, "stale_source", "Preparation is not ready")
        if not cursor.isdigit() or len(cursor) > 8:
            raise APIError(400, "Invalid details cursor")
        offset = int(cursor)
        sources = job["sources"]
        gaps = job.get("gaps", [])
        return {
            "preparation_id": identity,
            "snapshot_id": view["proof"]["snapshot_id"],
            "brief": job["view"]["proof"]["brief"],
            "sources": [
                {
                    "source_id": s["id"],
                    "source_version": s["version"],
                    "read_handle": s["id"] + ":" + s["version"],
                }
                for s in sources[offset : offset + 50]
            ],
            "gaps": gaps[offset : offset + 50],
            "next_cursor": str(offset + 50) if offset + 50 < max(len(sources), len(gaps)) else None,
        }

    def cancel(self, key: str, identity: str, request_key: str) -> dict[str, bool]:
        job = self.get(key, identity)
        if request_key not in job["leases"]:
            return {"released": True}
        job["leases"].remove(request_key)
        if not job["leases"] and not job.get("claimed_by"):
            if job["view"]["state"] != "failed":
                job.pop("partial", None)
            job["canceled"] = True
            # Do not abandon an off-loop native read/write. Its result observes
            # the canceled fence before persistence; no further provider calls.
            self.update(job, state="canceled")
        return {"released": True}

    def resolve_proof(self, key: str, proof: Any, target: str, model: str) -> dict[str, Any]:
        if (
            not isinstance(proof, dict)
            or set(proof) != {"version", "preparation_id", "snapshot_id", "source_generation"}
            or proof.get("version") != 1
        ):
            raise PreparationError(
                409, "preparation_expired", "Prepare the handoff before switching"
            )
        job = self.get(key, identifier(proof["preparation_id"]))
        self.cheap_validate(job)
        view = job["view"]
        expected = {
            "harness": target,
            "model": {"mode": "explicit", "id": model} if model else {"mode": "default"},
        }
        if job["binding"]["target"] != expected:
            raise PreparationError(409, "target_changed", "Target or model changed; prepare again")
        if (
            view["state"] != "ready"
            or view["proof"]["snapshot_id"] != proof["snapshot_id"]
            or job["binding"]["source_generation"] != proof["source_generation"]
        ):
            raise PreparationError(409, "stale_source", "Prepared context is no longer ready")
        return job

    async def prepared(
        self, key: str, proof: dict[str, Any], target: str, model: str
    ) -> dict[str, Any]:
        job = self.resolve_proof(key, proof, target, model)
        async with self.server.memory.locks.setdefault(key, asyncio.Lock()):
            sources, missing = await asyncio.to_thread(
                self.server.memory.materialize, job["catalog"]
            )
        self.cheap_validate(job)
        before = {s["id"]: s["version"] for s in job["sources"]}
        if missing or {s["id"]: s["version"] for s in sources} != before:
            raise PreparationError(409, "stale_source", "Original history changed; prepare again")
        row = self.server.history.session(key)
        assert row is not None
        control = self.server.history.restart_control(key)
        return {
            "runtime": row.get("runtime"),
            "native_id": self.server.history.session_id_for(key),
            "command": row.get("command"),
            "model": row.get("model"),
            "configured_model": control.get("configured_model"),
            "native_binding": control.get("native_binding"),
            "cwd": str(row.get("worktree_path") or row.get("cwd") or "."),
            "test": bool(row.get("test")),
            "checkpoint_id": job["checkpoint_id"],
            "seed": job["seed"],
            "_captured": job["captured"],
            "_memory_job": job["id"],
        }

    async def close(self) -> None:
        for job in self.jobs.values():
            job["canceled"] = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            # Cancellation kills owned provider subprocesses. A native-file
            # worker can finish its private atomic write, but its canceled job
            # cannot resume and commit metadata after the DB is closed.
            await asyncio.gather(*tasks, return_exceptions=True)
