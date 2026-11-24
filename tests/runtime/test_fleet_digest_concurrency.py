"""Context reads overlap, stay bounded and leave no readers after a failed request."""

import asyncio

import pytest

from duckterm.core import fleet_questions


def test_digest_reads_overlap_with_bounded_parallelism_and_stable_order():
    async def scenario():
        active = peak = completed = 0
        release = asyncio.Event()
        full = asyncio.Event()

        async def render(row):
            nonlocal active, peak, completed
            active += 1
            peak = max(peak, active)
            if active == fleet_questions.DIGEST_CONCURRENCY:
                full.set()
            await release.wait()
            # Reverse completion order inside each group.
            await asyncio.sleep((3 - row["index"] % 4) * 0.001)
            active -= 1
            completed += 1
            return f"digest-{row['index']}"

        task = asyncio.create_task(
            fleet_questions.collect_digests([{"index": i} for i in range(11)], render)
        )
        async with asyncio.timeout(2):
            await full.wait()  # a serial implementation never reaches this
            assert completed == 0
            release.set()
            result = await task
        assert peak == fleet_questions.DIGEST_CONCURRENCY
        assert result == [f"digest-{i}" for i in range(11)]
        assert active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_collection_drains_sibling_readers(cancel):
    async def scenario():
        started = set()
        stopped = set()
        full = asyncio.Event()
        fail = asyncio.Event()

        async def render(row):
            key = row["index"]
            started.add(key)
            if len(started) == fleet_questions.DIGEST_CONCURRENCY:
                full.set()
            try:
                if key == 0 and not cancel:
                    await fail.wait()
                    raise ValueError("fixture")
                await asyncio.Event().wait()
            finally:
                stopped.add(key)

        task = asyncio.create_task(
            fleet_questions.collect_digests([{"index": i} for i in range(10)], render)
        )
        async with asyncio.timeout(2):
            await full.wait()
            if cancel:
                task.cancel()
            else:
                fail.set()
            with pytest.raises(asyncio.CancelledError if cancel else ValueError):
                await task
        assert started == stopped == set(range(fleet_questions.DIGEST_CONCURRENCY))

    asyncio.run(scenario())
