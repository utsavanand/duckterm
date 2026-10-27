"""Benchmark accounting and isolation, including real failure cleanup."""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "benchmark_server.py"
spec = importlib.util.spec_from_file_location("benchmark_server", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_environment_drops_live_targets_and_credentials(tmp_path, monkeypatch):
    for name in ("DUCKTERM_URL", "DUCKTERM_HOME", "DUCKTERM_TMUX_SOCKET", "GH_TOKEN"):
        monkeypatch.setenv(name, "must-not-reach-fixture")
    env = benchmark.isolated_env(tmp_path, "duckterm-bench-owned", "lite")
    assert env["DUCKTERM_HOME"] == env["HOME"] == str(tmp_path)
    assert env["DUCKTERM_TMUX_SOCKET"] == "duckterm-bench-owned"
    assert "DUCKTERM_URL" not in env and "GH_TOKEN" not in env
    assert env["DUCKTERM_SUMMARIZER"] == env["DUCKTERM_ORACLE"] == "off"
    assert env["DUCKTERM_EDITION"] == "lite"


def test_cpu_is_delta_not_wall_time_or_machine_percentage():
    before = {
        "wall_s": 100,
        "self_cpu_s": 40,
        "child_cpu_s": 10,
        "spawns": {"tmux": 80},
        "tmux": {"list-sessions": 70},
    }
    after = {
        "wall_s": 120,
        "self_cpu_s": 41,
        "child_cpu_s": 10.5,
        "spawns": {"tmux": 100},
        "tmux": {"list-sessions": 90},
        "peak_rss_bytes": 4096,
    }
    result = benchmark.difference(before, after)
    assert result["server_cpu_s"] == 1
    assert result["server_percent_of_one_core"] == 5
    assert result["completed_child_cpu_s"] == 0.5
    assert result["tmux_commands"] == {"list-sessions": 20}
    assert benchmark.summarize_latencies([20, 1, 10, 3])["p95_ms"] == 20


@pytest.mark.skipif(not shutil.which("tmux"), reason="real tmux benchmark smoke")
def test_all_workloads_validate_output_and_remove_test_sessions(tmp_path):
    output = tmp_path / "result.json"
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sessions",
            "1",
            "--clients",
            "1",
            "--rounds",
            "1",
            "--interval",
            "0",
            "--warmup",
            "0.1",
            "--lines",
            "8",
            "--replay-events",
            "2",
            "--event-bytes",
            "16",
            "--repeat",
            "1",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    data = json.loads(output.read_text())
    assert [row["scenario"] for row in data["trials"]] == ["idle", "output", "reconnect"]
    for row in data["trials"]:
        assert row["cleaned_sessions"] == 1 and row["remaining_sessions"] == 0
        assert row["latency"]["count"] == 1
        assert row["server_cpu_s"] >= 0
    assert data["trials"][1]["validated_payload_bytes"] == 8 * 120


@pytest.mark.skipif(not shutil.which("tmux"), reason="real tmux failure cleanup")
def test_failed_workload_removes_its_owned_namespace(monkeypatch):
    owned = {}
    original = benchmark.isolated_env

    def environment(root, namespace, edition):
        owned.update(root=root, namespace=namespace)
        return original(root, namespace, edition)

    async def fail(*args):
        raise RuntimeError("deliberate measurement failure")

    monkeypatch.setattr(benchmark, "isolated_env", environment)
    monkeypatch.setattr(benchmark, "workload", fail)
    args = SimpleNamespace(sessions=1, edition="full", warmup=0)
    with pytest.raises(RuntimeError, match="deliberate measurement failure"):
        asyncio.run(benchmark.trial(args, "idle"))
    assert not owned["root"].exists()
    result = subprocess.run(
        ["tmux", "-L", owned["namespace"], "list-sessions"],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert os.environ["DUCKTERM_TMUX_SOCKET"] != owned["namespace"]


@pytest.mark.parametrize("size", [0, 125, 126, 65536])
def test_benchmark_input_frames_follow_browser_masking(size):
    from duckterm.transport.websocket import read_frame

    payload = b"x" * size
    encoded = benchmark.input_frame(payload)
    assert encoded[1] & 0x80

    async def decode():
        reader = asyncio.StreamReader()
        reader.feed_data(encoded)
        reader.feed_eof()
        return await read_frame(reader)

    assert asyncio.run(decode()) == (2, payload)


def test_missing_workload_content_is_rejected():
    with pytest.raises(RuntimeError, match="session identities"):
        benchmark.validate_sessions({"sessions": []}, ["bench-0"])
    with pytest.raises(RuntimeError, match="fixture events"):
        benchmark.validate_replay({"events": []}, 1, 16)
    row = {"notification_type": "benchmark", "benchmark_index": 0, "test": True, "message": "short"}
    with pytest.raises(RuntimeError, match="payload"):
        benchmark.validate_replay({"events": [row]}, 1, 16)


@pytest.mark.parametrize("option", ["--interval", "--warmup", "--batch-delay"])
@pytest.mark.parametrize("value", ["nan", "inf"])
def test_nonfinite_timing_is_rejected_before_starting_workers(monkeypatch, capsys, option, value):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), option, value])
    with pytest.raises(SystemExit) as error:
        benchmark.main()
    assert error.value.code == 2
    assert "intervals must be finite" in capsys.readouterr().err
