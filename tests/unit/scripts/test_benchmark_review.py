import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "bench_review", Path(__file__).resolve().parents[3] / "scripts/benchmark_server.py"
)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "init", "events": [{"message": "x" * 1024} for _ in range(100)]},
    ],
)
def test_default_replay_size_is_readable(monkeypatch, payload):
    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"data: " + json.dumps(payload).encode() + b"\n\n")

        async def connect(*args, **kwargs):
            return reader, Writer()

        monkeypatch.setattr(bench, "connection", connect)
        return await bench.request(0, "", "/stream", sse=True)

    asyncio.run(run())


def test_non_init_event_is_rejected(monkeypatch):
    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b'data: {"type":"wrong","message":"init"}\n\n')

        async def connect(*args, **kwargs):
            return reader, Writer()

        monkeypatch.setattr(bench, "connection", connect)
        with pytest.raises(RuntimeError):
            await bench.request(0, "", "/stream", sse=True)

    asyncio.run(run())
