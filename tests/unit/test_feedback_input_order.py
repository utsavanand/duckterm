"""Owner feedback must follow keyboard bytes already accepted by the server."""

import asyncio
import json
import threading
from types import SimpleNamespace

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def test_feedback_cannot_overtake_pending_keyboard_input(tmp_path, monkeypatch):
    async def scenario():
        sup = SessionSupervisor(
            bus=EventBus(),
            runtime=GenericRuntime("cat"),
            session_key="test-order",
            cwd=str(tmp_path),
            extra={"test": True},
        )
        first_started = asyncio.Event()
        feedback_started = asyncio.Event()
        release_first = threading.Event()
        sent = []
        loop = asyncio.get_running_loop()

        def write(data):
            if data == b"DRAFT_":
                loop.call_soon_threadsafe(first_started.set)
                assert release_first.wait(5)
            sent.append(data)
            return True

        async def respond(*args):
            pass

        def annotation(*args):
            feedback_started.set()

        monkeypatch.setattr(sup, "write_bytes", write)
        monkeypatch.setattr("duckterm.server._write_json", respond)
        server = SimpleNamespace(
            orchestrator=SimpleNamespace(get=lambda _: sup),
            history=SimpleNamespace(add_annotation=annotation),
        )
        sup.queue_bytes(b"DRAFT_")
        sup.queue_bytes(b"END")
        task = None
        try:
            await asyncio.wait_for(first_started.wait(), 2)
            task = asyncio.create_task(
                Server._add_annotation(
                    server,
                    None,
                    "test-order",
                    json.dumps({"note": "feedback"}).encode(),
                )
            )
            await asyncio.wait_for(feedback_started.wait(), 2)
            # If feedback bypasses the FIFO it can finish while the first key
            # is blocked. The timeout only gives that broken path time to run.
            await asyncio.wait({task}, timeout=0.1)
            assert sent == []
            release_first.set()
            await asyncio.wait_for(task, 2)
            assert sent == [b"DRAFT_", b"END", b"feedback\r"]
        finally:
            release_first.set()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            if sup._input_task:
                sup._input_task.cancel()
                await asyncio.gather(sup._input_task, return_exceptions=True)

    asyncio.run(scenario())


def test_feedback_failure_does_not_stall_following_writes(tmp_path, monkeypatch):
    async def scenario():
        sup = SessionSupervisor(
            bus=EventBus(),
            runtime=GenericRuntime("cat"),
            session_key="test-failure",
            cwd=str(tmp_path),
            extra={"test": True},
        )

        def write(data):
            if data == b"broken":
                raise OSError("closed terminal")
            return data == b"good"

        monkeypatch.setattr(sup, "write_bytes", write)
        try:
            assert not await asyncio.wait_for(sup.write_queued_bytes(b"broken"), 2)
            assert await asyncio.wait_for(sup.write_queued_bytes(b"good"), 2)
        finally:
            sup._input_task.cancel()
            await asyncio.gather(sup._input_task, return_exceptions=True)

    asyncio.run(scenario())


def test_stopping_input_releases_queued_feedback(tmp_path, monkeypatch):
    async def scenario():
        sup = SessionSupervisor(
            bus=EventBus(),
            runtime=GenericRuntime("cat"),
            session_key="test-stop",
            cwd=str(tmp_path),
            extra={"test": True},
        )
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def write(data):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return True

        monkeypatch.setattr(sup, "write_bytes", write)
        first = asyncio.create_task(sup.write_queued_bytes(b"first"))
        second = asyncio.create_task(sup.write_queued_bytes(b"second"))
        try:
            await asyncio.wait_for(started.wait(), 2)
            sup._input_task.cancel()
            await asyncio.gather(sup._input_task, return_exceptions=True)
            assert await asyncio.wait_for(asyncio.gather(first, second), 2) == [False, False]
        finally:
            release.set()

    asyncio.run(scenario())


def test_stop_before_drain_starts_rejects_feedback(tmp_path):
    async def scenario():
        sup = SessionSupervisor(
            bus=EventBus(),
            runtime=GenericRuntime("cat"),
            session_key="test-early-stop",
            cwd=str(tmp_path),
            extra={"test": True},
        )
        receipt = asyncio.get_running_loop().create_future()
        sup._enqueue_input(b"pending", receipt)
        await sup.stop()
        assert await asyncio.wait_for(receipt, 2) is False
        assert await asyncio.wait_for(sup.write_queued_bytes(b"after stop"), 2) is False
        await asyncio.gather(sup._input_task, return_exceptions=True)

    asyncio.run(scenario())
