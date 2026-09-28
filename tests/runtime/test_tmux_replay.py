"""Real tmux seam tests: delayed file bytes, mid-flood attaches and client loss."""

import asyncio
import re
import shlex
import shutil
import sys

import pytest

from duckterm.agents import tmux
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator, SessionSupervisor
from duckterm.runtimes.generic import GenericRuntime

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")


def plain(raw):
    return re.sub(rb"\x1b\[[0-9;]*[A-Za-z]", b"", raw).replace(b"\r", b"")


async def until(feed, marker):
    got = bytearray()
    async with asyncio.timeout(10):
        async for chunk in feed:
            got.extend(chunk)
            if marker in got:
                return bytes(got)
    raise AssertionError(f"stream ended before {marker!r}: {bytes(got)!r}")


def test_delayed_spool_bytes_never_replay_after_snapshot(tmp_path, monkeypatch):
    async def frozen_tail(self):
        while tmux.session_exists(self._tmux_target):
            await asyncio.sleep(0.01)

    monkeypatch.setattr(SessionSupervisor, "_tail_pipe", frozen_tail)

    async def scenario():
        orch = Orchestrator(EventBus())
        key = await orch.launch(runtime=GenericRuntime("/bin/cat"), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        feed = None
        try:
            assert sup is not None and sup._tmux_target is not None
            assert sup.write_bytes(b"DRAFT_SURVIVES_EVICTION")
            # Observe tmux, not the deliberately frozen file reader.
            async with asyncio.timeout(5):
                while "DRAFT_SURVIVES_EVICTION" not in tmux.capture_pane(sup._tmux_target):
                    await asyncio.sleep(0.01)
            feed = sup.subscribe_bytes()
            snapshot = await asyncio.wait_for(anext(feed), 5)
            assert snapshot.count(b"DRAFT_SURVIVES_EVICTION") == 1
            # These are the exact pre-capture bytes the old implementation
            # incorrectly delivered *after* the captured screen.
            sup._record_bytes(b"DRAFT_SURVIVES_EVICTION")
            pending = asyncio.create_task(until(feed, b"_CONTINUED"))
            assert sup.write_bytes(b"_CONTINUED")
            sup._record_bytes(b"_CONTINUED")  # frozen file reader catches up
            live = await pending
            assert b"DRAFT_SURVIVES_EVICTION" not in live
            assert live.count(b"_CONTINUED") == 1
            assert not sup._byte_subs  # tmux terminal no longer consumes file-tail bytes
        finally:
            if feed is not None:
                await feed.aclose()
            await orch.stop(key)

    asyncio.run(scenario())


def test_repeated_attach_mid_flood_has_no_missing_or_duplicate_records(tmp_path):
    script = tmp_path / "flood.py"
    trigger = tmp_path / "go"
    script.write_text(
        "import os,time,pathlib\n"
        f"p=pathlib.Path({str(trigger)!r})\n"
        'print("READY",flush=True)\n'
        "while not p.exists(): time.sleep(.002)\n"
        "for i in range(400):\n"
        ' os.write(1, ("R%06d\\n"%i).encode()); time.sleep(.002)\n'
        'print("DONE",flush=True)\n'
        "time.sleep(30)\n"
    )

    async def scenario():
        orch = Orchestrator(EventBus())
        key = await orch.launch(
            runtime=GenericRuntime(shlex.join([sys.executable, str(script)])),
            cwd=str(tmp_path),
            test=True,
        )
        sup = orch.get(key)
        assert sup is not None and sup._tmux_target is not None
        feeds = []
        tasks = []
        try:
            async with asyncio.timeout(5):
                while "READY" not in tmux.capture_pane(sup._tmux_target):
                    await asyncio.sleep(0.01)

            async def collect(feed):
                return await until(feed, b"DONE")

            first = sup.subscribe_bytes()
            feeds.append(first)
            initial = await asyncio.wait_for(anext(first), 5)
            assert b"READY" in initial
            tasks.append(asyncio.create_task(collect(first)))
            trigger.touch()
            for number in (40, 100, 160, 220):
                async with asyncio.timeout(5):
                    while f"R{number:06d}" not in tmux.capture_screen(sup._tmux_target).decode():
                        await asyncio.sleep(0.005)
                feed = sup.subscribe_bytes()
                feeds.append(feed)
                tasks.append(asyncio.create_task(collect(feed)))
            results = await asyncio.gather(*tasks)
            for result in results:
                records = [int(n) for n in re.findall(rb"R(\d{6})", plain(result))]
                assert records == list(range(400)), records
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for feed in feeds:
                await feed.aclose()
            assert tmux._tmux("list-clients")[1].strip() == ""
            await orch.stop(key)

    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["detach", "pause"])
def test_client_loss_reconnect_keeps_pane_and_other_viewer(tmp_path, loss):
    async def scenario():
        orch = Orchestrator(EventBus())
        key = await orch.launch(runtime=GenericRuntime("/bin/cat"), cwd=str(tmp_path), test=True)
        sup = orch.get(key)
        assert sup is not None and sup._tmux_target is not None
        feeds = []
        try:
            before = tmux._tmux("display-message", "-p", "-t", sup._tmux_target, "#{pane_pid}")[1]
            first = sup.subscribe_bytes()
            feeds.append(first)
            await asyncio.wait_for(anext(first), 5)
            client = tmux._tmux("list-clients", "-F", "#{client_name}")[1].strip()
            assert client
            second = sup.subscribe_bytes()
            feeds.append(second)
            await asyncio.wait_for(anext(second), 5)
            assert tmux.capture_pane(sup._tmux_target).strip() == ""
            if loss == "detach":
                assert tmux._tmux("detach-client", "-t", client)[0]
            else:
                pane = tmux._tmux("display-message", "-p", "-t", sup._tmux_target, "#{pane_id}")[
                    1
                ].strip()
                assert tmux._tmux("refresh-client", "-t", client, "-A", pane + ":pause")[0]
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(first), 5)
            assert sup.write_bytes(b"STILL_ALIVE")
            assert b"STILL_ALIVE" in await until(second, b"STILL_ALIVE")
            third = sup.subscribe_bytes()
            feeds.append(third)
            assert b"STILL_ALIVE" in await asyncio.wait_for(anext(third), 5)
            assert (
                tmux._tmux("display-message", "-p", "-t", sup._tmux_target, "#{pane_pid}")[1]
                == before
            )
        finally:
            for feed in feeds:
                await feed.aclose()
            assert tmux._tmux("list-clients")[1].strip() == ""
            await orch.stop(key)

    asyncio.run(scenario())
