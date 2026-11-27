"""Overlapping Home requests share expensive owner-history reads."""

import asyncio
from types import SimpleNamespace

import pytest

from duckterm.review import lists
from duckterm.server import Server


def subject() -> Server:
    server = object.__new__(Server)
    server._branch_owners_cache = (0.0, {})
    server._branch_owners_refresh = None
    server.history = SimpleNamespace(push_commands=lambda since: [])  # type: ignore[assignment]
    return server


def test_overlapping_folder_reads_share_one_scan_and_explicit_refresh_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = subject()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        owners = {("/repo", "topic"): "author"}

        async def worker(read):
            nonlocal calls
            calls += 1
            result = read()
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(asyncio, "to_thread", worker)
        monkeypatch.setattr(lists, "branch_owners", lambda commands: owners)
        pending = [asyncio.create_task(server._branch_owners(False)) for _ in range(12)]
        await entered.wait()
        try:
            assert calls == 1
        finally:
            release.set()
            results = await asyncio.gather(*pending)
        assert results == [owners] * 12
        assert await server._branch_owners(False) == owners
        assert calls == 1
        assert await server._branch_owners(True) == owners
        assert calls == 2

    asyncio.run(scenario())


def test_one_cancelled_request_does_not_cancel_other_folder_or_repeat_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = subject()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        owners = {("/repo", "topic"): "author"}

        async def worker(read):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return owners

        monkeypatch.setattr(asyncio, "to_thread", worker)
        first = asyncio.create_task(server._branch_owners(False))
        second = asyncio.create_task(server._branch_owners(False))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == owners
        assert await server._branch_owners(False) == owners
        assert calls == 1

    asyncio.run(scenario())


def test_explicit_refresh_observes_pushes_after_the_inflight_scan_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = subject()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def worker(read):
            nonlocal calls
            calls += 1
            result = {("/repo", f"push-{calls}"): "author"}
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(asyncio, "to_thread", worker)
        background = asyncio.create_task(server._branch_owners(False))
        await entered.wait()
        requested = asyncio.create_task(server._branch_owners(True))
        await asyncio.sleep(0)  # Let the request join the still-blocked background scan.
        release.set()
        assert await background == {("/repo", "push-1"): "author"}
        assert await requested == {("/repo", "push-2"): "author"}
        assert await server._branch_owners(False) == {("/repo", "push-2"): "author"}
        assert calls == 2

    asyncio.run(scenario())


def test_shared_failure_is_not_cached_and_next_request_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = subject()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        failure = OSError("read unavailable")
        should_fail = True

        async def worker(read):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            if should_fail:
                raise failure
            return {("/repo", "recovered"): "author"}

        monkeypatch.setattr(asyncio, "to_thread", worker)
        pending = [asyncio.create_task(server._branch_owners(False)) for _ in range(12)]
        await entered.wait()
        release.set()
        results = await asyncio.gather(*pending, return_exceptions=True)
        assert results == [failure] * 12
        assert calls == 1
        assert server._branch_owners_cache == (0.0, {})
        should_fail = False
        assert await server._branch_owners(False) == {("/repo", "recovered"): "author"}
        assert calls == 2

    asyncio.run(scenario())


def test_refresh_finishes_and_populates_cache_after_all_waiters_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = subject()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        owners = {("/repo", "topic"): "author"}

        async def worker(read):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return owners

        monkeypatch.setattr(asyncio, "to_thread", worker)
        first = asyncio.create_task(server._branch_owners(False))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await server._branch_owners(False) == owners
        assert calls == 1

    asyncio.run(scenario())
