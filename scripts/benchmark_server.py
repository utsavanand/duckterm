"""Opt-in, stdlib-only server benchmark; see docs/performance-benchmark.md."""

import argparse
import asyncio
import collections
import contextlib
import json
import math
import os
import platform
import resource
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
AGENT = """import json, os, sys, termios, time
from pathlib import Path
settings = termios.tcgetattr(0)
settings[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, settings)
print("[idle] BENCH_READY", flush=True)
Path(os.environ["DUCKTERM_HOME"], "ready-" + os.environ["DUCKTERM_SESSION_KEY"]).touch()
for line in sys.stdin:
    request = json.loads(line)
    print("[busy]", flush=True)
    for index in range(request["lines"]):
        print("x" * request["width"])
        if request["delay"] and (index + 1) % request["batch"] == 0:
            time.sleep(request["delay"])
    print("[idle] BENCH_DONE", flush=True)
"""


def isolated_env(root, namespace, edition):
    # Inherit no application/credential configuration; fixture agents never call models.
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    env.update(
        HOME=str(root),
        XDG_CONFIG_HOME=str(root / "config"),
        CLAUDE_CONFIG_DIR=str(root / "claude"),
        CODEX_HOME=str(root / "codex"),
        PYTHONPATH=str(SOURCE),
        DUCKTERM_HOME=str(root),
        DUCKTERM_TMUX_SOCKET=namespace,
        DUCKTERM_EDITION=edition,
        DUCKTERM_SUMMARIZER="off",
        DUCKTERM_ORACLE="off",
        DUCKTERM_NO_TERMINAL="1",
    )
    return env


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def summarize_latencies(values):
    return {
        "count": len(values),
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def input_frame(payload):
    """One masked client binary frame, as sent by the browser terminal."""
    mask = os.urandom(4)
    size = len(payload)
    if size < 126:
        header = bytes([0x82, 0x80 | size])
    elif size < 65536:
        header = bytes([0x82, 0xFE]) + struct.pack(">H", size)
    else:
        header = bytes([0x82, 0xFF]) + struct.pack(">Q", size)
    return header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))


def difference(before, after):
    wall = after["wall_s"] - before["wall_s"]
    cpu = after["self_cpu_s"] - before["self_cpu_s"]
    return {
        "wall_s": wall,
        "server_cpu_s": cpu,
        "server_percent_of_one_core": 100 * cpu / wall,
        "completed_child_cpu_s": after["child_cpu_s"] - before["child_cpu_s"],
        "server_lifetime_peak_rss_bytes": after["peak_rss_bytes"],
        "subprocess_starts": dict(
            collections.Counter(after["spawns"]) - collections.Counter(before["spawns"])
        ),
        "tmux_commands": dict(
            collections.Counter(after["tmux"]) - collections.Counter(before["tmux"])
        ),
    }


async def worker(args):
    # This private mode must only operate in the temporary home created by the driver.
    root = Path(os.environ["DUCKTERM_HOME"])
    namespace = os.environ["DUCKTERM_TMUX_SOCKET"]
    if not namespace.startswith("duckterm-bench-") or (root / "owner").read_text() != namespace:
        raise RuntimeError("benchmark worker requires its owned temporary namespace")
    from duckterm import __version__
    from duckterm.agents import tmux
    from duckterm.runtimes.generic import GenericRuntime
    from duckterm.server import Server

    control_output = sys.stdout
    sys.stdout = sys.stderr  # Keep application logging out of the JSON control channel.
    spawns = collections.Counter()
    commands = collections.Counter()

    def audit(event, values):
        if event == "subprocess.Popen":
            spawns[Path(values[0]).name] += 1

    sys.addaudithook(audit)
    original_tmux = tmux._tmux

    def counted_tmux(*argv):
        commands[argv[0]] += 1
        return original_tmux(*argv)

    tmux._tmux = counted_tmux
    server = Server()
    keys = []
    serving = None

    def reply(value):
        control_output.write(json.dumps(value) + "\n")
        control_output.flush()

    def stats():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {
            "wall_s": time.monotonic(),
            "self_cpu_s": usage.ru_utime + usage.ru_stime,
            "child_cpu_s": children.ru_utime + children.ru_stime,
            "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
            "spawns": dict(spawns),
            "tmux": dict(commands),
        }

    try:
        # The existing serve callback reports its input port. Reserve an ephemeral
        # port, then fail if binding loses the race; never attach to an existing server.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        os.environ["DUCKTERM_URL"] = f"http://127.0.0.1:{port}"
        ready = asyncio.Event()
        serving = asyncio.create_task(
            server.serve("127.0.0.1", port, on_listening=lambda *_: ready.set())
        )
        while not ready.is_set():
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        agent = root / "agent.py"
        agent.write_text(AGENT)
        for index in range(args.sessions):
            key = f"bench-{index}"
            keys.append(key)
            await server.orchestrator.launch(
                runtime=GenericRuntime(shlex.join([sys.executable, "-u", str(agent)])),
                cwd=str(root),
                session_key=key,
                test=True,
                record_intention=False,
            )
        deadline = time.monotonic() + 20
        while not all((root / ("ready-" + key)).exists() for key in keys):
            if time.monotonic() > deadline:
                raise RuntimeError("fixture agents did not become ready")
            await asyncio.sleep(0.05)
        rows = server.history.sessions()
        if len(rows) != args.sessions or not all(row["test"] for row in rows):
            raise RuntimeError("benchmark sessions must all be flagged test:true")
        reply({"port": port, "version": __version__, "keys": keys})
        while line := await asyncio.to_thread(sys.stdin.readline):
            request = json.loads(line)
            if request["op"] == "stats":
                if request.get("output_complete"):
                    deadline = time.monotonic() + 20
                    while not all(
                        "[idle] BENCH_DONE" in "".join(server.orchestrator.get(key).output_tail(8))
                        for key in keys
                    ):
                        if time.monotonic() > deadline:
                            raise RuntimeError("server output tails did not finish processing")
                        await asyncio.sleep(0.01)
                reply(stats())
            elif request["op"] == "seed":
                for index in range(request["events"]):
                    server.bus.publish(
                        {
                            "event_type": "Notification",
                            "notification_type": "benchmark",
                            "session_key": keys[index % len(keys)],
                            "test": True,
                            "benchmark_index": index,
                            "message": "x" * request["bytes"],
                        }
                    )
                reply({"seeded": request["events"]})
            elif request["op"] == "stop":
                break
            else:
                raise ValueError("unknown benchmark control operation")
    finally:
        for key in keys:
            await server.orchestrator.stop(key)
        if serving is not None:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving
        server.history.purge_test_sessions()
        remaining = len(server.history.sessions())
        server.history.close()
    if remaining:
        raise RuntimeError("benchmark sessions survived cleanup")
    reply({"cleaned_sessions": len(keys), "remaining_sessions": remaining})


async def exchange(reader, writer, data=None, timeout=30):
    if data is not None:
        writer.write((json.dumps(data) + "\n").encode())
        await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout)
    if not line:
        raise RuntimeError("benchmark worker exited before replying")
    return json.loads(line)


async def connection(port, token, path, body=None, websocket=False):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    method = "POST" if body is not None else "GET"
    payload = json.dumps(body).encode() if body is not None else b""
    headers = (
        f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n" f"X-Duckterm-Token: {token}\r\n"
    )
    if websocket:
        headers += (
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: ZHVja3Rlcm0tYmVuY2htaw==\r\nSec-WebSocket-Version: 13\r\n"
        )
    else:
        headers += "Connection: close\r\n"
    if body is not None:
        headers += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    try:
        writer.write(headers.encode() + b"\r\n" + payload)
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        status = int(head.split()[1])
        if status != (101 if websocket else 200):
            raise RuntimeError(f"benchmark request {path} returned {status}")
        return reader, writer
    except BaseException:
        writer.close()
        await writer.wait_closed()
        raise


async def read_init(reader):
    # Default replay exceeds StreamReader's 64 KiB readuntil limit. Read bounded
    # chunks instead; keep the whole-frame timeout in request().
    raw = bytearray()
    while b"\n\n" not in raw:
        chunk = await reader.read(8192)
        if not chunk:
            raise RuntimeError("reconnect closed before delivering an init event")
        raw.extend(chunk)
        if len(raw) > 16 * 1024 * 1024:
            raise RuntimeError("reconnect init exceeds the 16 MiB benchmark limit")
    frame = bytes(raw).split(b"\n\n", 1)[0] + b"\n\n"
    data = b"\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:"))
    value = json.loads(data)
    if (
        not isinstance(value, dict)
        or value.get("type") != "init"
        or not isinstance(value.get("events"), list)
    ):
        raise RuntimeError("reconnect did not deliver an init event")
    return frame, value


async def request(port, token, path, body=None, sse=False):
    start = time.monotonic()
    reader, writer = await connection(port, token, path, body)
    try:
        if sse:
            raw, value = await asyncio.wait_for(read_init(reader), 10)
        else:
            raw = await asyncio.wait_for(reader.read(), 10)
            value = json.loads(raw)
        return (time.monotonic() - start) * 1000, len(raw), value
    finally:
        writer.close()
        await writer.wait_closed()


async def workload(args, scenario, ready, token):
    port = ready["port"]
    latencies = []
    received = 0
    if scenario == "idle":
        # Fixed request count/cadence per client, including quiet time between polls.
        async def poll():
            nonlocal received
            for _ in range(args.rounds):
                latency, size, value = await request(port, token, "/sessions")
                validate_sessions(value, ready["keys"])
                latencies.append(latency)
                received += size
                await asyncio.sleep(args.interval)

        await asyncio.gather(*(poll() for _ in range(args.clients)))
    elif scenario == "reconnect":

        async def reconnect():
            nonlocal received
            for _ in range(args.rounds):
                latency, size, value = await request(port, token, "/stream", sse=True)
                validate_replay(value, args.replay_events, args.event_bytes)
                latencies.append(latency)
                received += size
                await asyncio.sleep(args.interval)

        await asyncio.gather(*(reconnect() for _ in range(args.clients)))
    else:
        from duckterm.transport.websocket import read_frame

        async def output(key):
            nonlocal received
            reader, writer = await connection(
                port, token, f"/sessions/{key}/terminal", websocket=True
            )
            try:
                # Consume the initial screen before sending input. Otherwise the
                # initial snapshot could include output also delivered by the feed.
                initial = b""
                while b"BENCH_READY" not in initial:
                    frame = await asyncio.wait_for(read_frame(reader), 20)
                    if frame is None or frame[0] == 8:
                        raise RuntimeError("terminal closed before its initial screen")
                    if frame[0] == 2:
                        initial = (initial + frame[1])[-4096:]
                start = time.monotonic()
                payload = (
                    json.dumps(
                        {
                            "lines": args.lines,
                            "width": args.width,
                            "batch": args.batch_lines,
                            "delay": 0 if scenario == "burst" else args.batch_delay,
                        }
                    )
                    + "\n"
                ).encode()
                writer.write(input_frame(payload))
                await writer.drain()
                tail = b""
                payload_bytes = 0
                while b"[idle] BENCH_DONE" not in tail:
                    frame = await asyncio.wait_for(read_frame(reader), 20)
                    if frame is None or frame[0] == 8:
                        raise RuntimeError("terminal closed before completing output")
                    if frame[0] == 2:
                        received += len(frame[1])
                        payload_bytes += frame[1].count(b"x")
                        tail = (tail + frame[1])[-128:]
                if payload_bytes != args.lines * args.width:
                    raise RuntimeError(f"terminal payload mismatch: {payload_bytes} bytes")
                latencies.append((time.monotonic() - start) * 1000)
            finally:
                writer.close()
                await writer.wait_closed()

        await asyncio.gather(*(output(key) for key in ready["keys"]))
    return {"latency": summarize_latencies(latencies), "received_bytes": received}


def validate_sessions(value, keys):
    rows = value.get("sessions", []) if isinstance(value, dict) else []
    if len(rows) != len(keys) or {row.get("session_key") for row in rows} != set(keys):
        raise RuntimeError("idle response did not contain the expected session identities")
    if not all(row.get("test") for row in rows):
        raise RuntimeError("idle response contained an unflagged test session")


def validate_replay(value, count, size):
    rows = [row for row in value["events"] if row.get("notification_type") == "benchmark"]
    if len(rows) != count or [row.get("benchmark_index") for row in rows] != list(range(count)):
        raise RuntimeError("reconnect replay did not contain the expected fixture events")
    if any(row.get("message") != "x" * size or row.get("test") is not True for row in rows):
        raise RuntimeError("reconnect replay payload did not match the fixture")


async def trial(args, scenario):
    namespace = "duckterm-bench-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="duckterm-benchmark-") as directory:
        root = Path(directory)
        (root / "owner").write_text(namespace)
        env = isolated_env(root, namespace, args.edition)
        log = root / "worker.log"
        process = None
        try:
            with log.open("wb") as output:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--sessions",
                    str(args.sessions),
                    env=env,
                    cwd=root,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=output,
                )
                ready = await exchange(process.stdout, process.stdin, timeout=120)
                token = (root / "token").read_text().strip()
                # Let startup output settle and adaptive tail polling reach idle.
                await asyncio.sleep(args.warmup)
                if scenario == "reconnect":
                    await exchange(
                        process.stdout,
                        process.stdin,
                        {
                            "op": "seed",
                            "events": args.replay_events,
                            "bytes": args.event_bytes,
                        },
                    )
                before = await exchange(process.stdout, process.stdin, {"op": "stats"})
                result = await asyncio.wait_for(workload(args, scenario, ready, token), 180)
                after = await exchange(
                    process.stdout,
                    process.stdin,
                    {
                        "op": "stats",
                        "output_complete": scenario in ("output", "burst"),
                    },
                )
                cleanup = await exchange(process.stdout, process.stdin, {"op": "stop"})
                await asyncio.wait_for(process.wait(), 20)
                if process.returncode != 0:
                    raise RuntimeError("benchmark worker failed during cleanup")
            return {
                "scenario": scenario,
                "version": ready["version"],
                **difference(before, after),
                **result,
                **cleanup,
                "validated_payload_bytes": (
                    args.sessions * args.lines * args.width
                    if scenario in ("output", "burst")
                    else None
                ),
            }
        except BaseException:
            print(log.read_text(errors="replace"), file=sys.stderr)
            raise
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            # Even worker failure/interrupt can only clean this freshly owned socket.
            subprocess.run(
                ["tmux", "-L", namespace, "kill-server"], capture_output=True, timeout=10
            )
            probe = subprocess.run(
                ["tmux", "-L", namespace, "list-sessions"], capture_output=True, timeout=10
            )
            if probe.returncode == 0:
                raise RuntimeError("owned benchmark tmux sessions survived cleanup")


async def run(args):
    results = []
    for repeat in range(args.repeat):
        for scenario in args.scenarios:
            print(f"benchmark {repeat + 1}/{args.repeat}: {scenario}", file=sys.stderr)
            results.append({"repeat": repeat + 1, **await trial(args, scenario)})
    return {
        "schema": 1,
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=SOURCE.parent,
            text=True,
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=SOURCE.parent,
                text=True,
            ).strip()
        ),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "configuration": {
            key: value for key, value in vars(args).items() if key not in ("worker", "output")
        },
        "scope": (
            "Server process; real synthetic tmux terminals and HTTP clients. "
            "No browser renderer, model, credential provider or live database."
        ),
        "measurement_notes": [
            "CPU is process CPU, not subprocess wall time. Completed-child CPU excludes "
            "still-running tmux/agent/writer processes.",
            "Peak RSS is the server lifetime high-water mark, including startup; "
            "it is not current RSS or a whole-app memory sum.",
            "Spawn counters observe server subprocess calls only. "
            "No performance thresholds or causal claims are inferred.",
        ],
        "trials": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sessions", type=int, default=16)
    parser.add_argument("--clients", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--warmup", type=float, default=6)
    parser.add_argument("--lines", type=int, default=1024)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--batch-lines", type=int, default=32)
    parser.add_argument("--batch-delay", type=float, default=0.02)
    parser.add_argument("--replay-events", type=int, default=100)
    parser.add_argument("--event-bytes", type=int, default=1024)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--edition", choices=("lite", "code", "full"), default="full")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=("idle", "output", "reconnect", "burst"),
        default=["idle", "output", "reconnect"],
    )
    parser.add_argument("--output", type=Path, default=Path("/tmp/duckterm-server-benchmark.json"))
    args = parser.parse_args()
    if not all(math.isfinite(value) for value in (args.interval, args.warmup, args.batch_delay)):
        parser.error("intervals must be finite")
    if args.replay_events > 100:
        parser.error("replay-events must be at most 100, the server init replay limit")
    if (
        min(
            args.sessions,
            args.clients,
            args.rounds,
            args.lines,
            args.width,
            args.repeat,
            args.replay_events,
            args.event_bytes,
            args.batch_lines,
        )
        < 1
        or min(args.interval, args.warmup, args.batch_delay) < 0
    ):
        parser.error("counts must be positive; intervals must be nonnegative")
    if not shutil.which("tmux"):
        parser.error("tmux is required")
    if args.worker:
        asyncio.run(worker(args))
    else:
        result = asyncio.run(run(args))
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Results: {args.output}")


if __name__ == "__main__":
    main()
