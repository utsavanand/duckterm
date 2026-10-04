"""The optional local voice: install, status, and speech through a worker
subprocess. Stdlib only. The architect's ruling (2026-09-29) set the terms:
installed only on request, pinned packages and model, no GPL code, excluded
from backups, a worker that never blocks the server, and honest status.

The voice is Kokoro-82M run through ONNX Runtime (CPU, any Mac on macOS
13+). The owner turned down the MLX build's 785 MB ("just for voice it will
be extra 1.4 gb?"); this one is about 310 MB with the int8 model, and the
2026-09-29 comparison on the owner's Mac showed no measurable quality loss.

Layout under ~/.duckterm/voice/ (reinstallable, excluded from backup):
  venv/            the worker's Python environment
  model/           the pinned ONNX model and voice pack, checked by SHA256
  cache/           synthesized phrases, pruned to CACHE_LIMIT
  installed.json   what was installed, to detect a stale install
"""

import hashlib
import json
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "kokoro-manifest.json"
WORKER = HERE / "worker.py"

CACHE_LIMIT = 200 * 1024 * 1024
START_TIMEOUT_S = 60.0  # imports and model load; the first run after install is slowest
SAY_TIMEOUT_S = 15.0
SIZE_NOTE = "about 310 MB"
VOICE_ID = re.compile(r"[ab][fm]_[a-z]+")

# Packages that would bring GPL code in. misaki's [en] extra pulls these for an
# espeak fallback; the lock leaves the extra out and install() fails closed if
# they appear anyway.
GPL_PACKAGES = {"phonemizer", "phonemizer-fork", "espeakng-loader", "espeak-ng"}

# Kokoro's English voices, labelled for people.
VOICE_LABELS = {
    "af_heart": "Heart",
    "af_bella": "Bella",
    "af_nicole": "Nicole",
    "af_sarah": "Sarah",
    "af_sky": "Sky",
    "af_nova": "Nova",
    "af_alloy": "Alloy",
    "af_aoede": "Aoede",
    "af_jessica": "Jessica",
    "af_kore": "Kore",
    "af_river": "River",
    "am_michael": "Michael",
    "am_fenrir": "Fenrir",
    "am_puck": "Puck",
    "am_adam": "Adam",
    "am_echo": "Echo",
    "am_eric": "Eric",
    "am_liam": "Liam",
    "am_onyx": "Onyx",
    "am_santa": "Santa",
    "bf_emma": "Emma",
    "bf_isabella": "Isabella",
    "bf_alice": "Alice",
    "bf_lily": "Lily",
    "bm_george": "George",
    "bm_fable": "Fable",
    "bm_lewis": "Lewis",
    "bm_daniel": "Daniel",
}


def find_tool(name: str) -> str | None:
    """A tool on PATH or in the usual install places. The Mac app starts the
    server with a short PATH that often lacks both."""
    extra = [
        Path.home() / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path.home() / ".cargo" / "bin",
    ]
    return shutil.which(name) or shutil.which(name, path=os.pathsep.join(map(str, extra)))


class VoiceError(RuntimeError):
    """Local voice can't speak right now; the dashboard uses a macOS voice."""


def lock_file() -> Path:
    """The pinned packages for this Mac's processor."""
    return HERE / f"requirements-{platform.machine()}.lock"


def unsupported_reason() -> str | None:
    """Why this machine can't run the local voice, or None if it can."""
    if sys.platform != "darwin" or not lock_file().is_file():
        return "Natural voices need a Mac."
    major = int((platform.mac_ver()[0] or "0").split(".")[0])
    if major < 13:
        return "Natural voices need macOS 13 or later."
    if not find_tool("uv") and not find_tool("python3.12"):
        return "Natural voices need uv or Python 3.12 to install."
    return None


def gpl_violations(dists: Iterable[dict[str, str]]) -> list[str]:
    """Installed distributions that are GPL (not LGPL), by name or license."""
    bad = []
    for d in dists:
        name = d.get("name", "").lower()
        text = f"{d.get('license', '')} {d.get('classifiers', '')}".upper()
        lesser = "LGPL" in text or "LESSER" in text
        if name in GPL_PACKAGES or (not lesser and ("GPL" in text or "GENERAL PUBLIC" in text)):
            bad.append(d.get("name", "?"))
    return bad


def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST.read_text())
    return data


def lock_digest() -> str:
    return hashlib.sha256(lock_file().read_bytes()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model(model_dir: Path, files: dict[str, dict[str, Any]]) -> list[str]:
    """Pinned files that are missing or don't match their SHA256."""
    return [
        rel
        for rel, meta in files.items()
        if not (model_dir / rel).is_file() or _sha256(model_dir / rel) != meta["sha256"]
    ]


class LocalVoice:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.venv = root / "venv"
        self.model = root / "model"
        self.cache = root / "cache"
        self.record = root / "installed.json"
        self._lock = threading.Lock()
        self._worker: subprocess.Popen[bytes] | None = None
        self._install: dict[str, Any] | None = None  # progress while installing

    # ── status ──

    def installed(self) -> bool:
        try:
            record = json.loads(self.record.read_text())
        except (OSError, ValueError):
            return False
        return (
            record.get("model") == manifest()["files"][manifest()["onnx"]]["sha256"]
            and record.get("lock") == lock_digest()
            and (self.venv / "bin" / "python").exists()
        )

    def voices(self) -> list[dict[str, str]]:
        # The voice pack is a NumPy .npz, which is a zip of one array per voice.
        with zipfile.ZipFile(self.model / manifest()["voices"]) as pack:
            found = sorted(name.removesuffix(".npy") for name in pack.namelist())
        return [
            {"id": v, "label": VOICE_LABELS.get(v, v), "accent": "UK" if v[0] == "b" else "US"}
            for v in found
            if VOICE_ID.fullmatch(v)
        ]

    def status(self) -> dict[str, Any]:
        reason = unsupported_reason()
        if reason:
            return {"state": "unsupported", "reason": reason}
        if self._install is not None:
            return {"state": "installing", **self._install}
        if self.installed():
            return {"state": "ready", "voices": self.voices()}
        failed = self.root / "install-error.txt"
        if failed.is_file():
            return {"state": "failed", "reason": failed.read_text().strip(), "size": SIZE_NOTE}
        return {"state": "absent", "size": SIZE_NOTE}

    # ── install ──

    def install(self, report: Callable[[str, float], None] = lambda step, done: None) -> None:
        """Create the venv, install the pinned packages, check licenses, and
        download and verify the pinned model. Raises VoiceError, and leaves no
        half-installed environment behind."""
        reason = unsupported_reason()
        if reason:
            raise VoiceError(reason)
        self.stop()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "install-error.txt").unlink(missing_ok=True)
        self.record.unlink(missing_ok=True)
        try:
            report("Creating the voice environment", 0.02)
            shutil.rmtree(self.venv, ignore_errors=True)
            python = self.venv / "bin" / "python"
            uv = find_tool("uv")
            if uv:
                self._run([uv, "venv", "--python", "3.12", str(self.venv)])
                pip = [uv, "pip", "install", "--python", str(python)]
            else:
                self._run([find_tool("python3.12") or "python3.12", "-m", "venv", str(self.venv)])
                pip = [str(python), "-m", "pip", "install"]
            report("Installing pinned packages", 0.08)
            self._run([*pip, "--require-hashes", "--no-deps", "-r", str(lock_file())])
            report("Checking licenses", 0.4)
            bad = gpl_violations(json.loads(self._run([str(python), "-c", _LIST_DISTS])))
            if bad:
                raise VoiceError(f"Refusing GPL packages: {', '.join(sorted(bad))}")
            self._download(report)
            report("Checking the model", 0.97)
            broken = verify_model(self.model, manifest()["files"])
            if broken:
                raise VoiceError(f"Model files failed their checksum: {', '.join(broken[:3])}")
            self.record.write_text(
                json.dumps(
                    {
                        "model": manifest()["files"][manifest()["onnx"]]["sha256"],
                        "lock": lock_digest(),
                        "at": time.time(),
                    }
                )
            )
            report("Ready", 1.0)
        except (VoiceError, OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(self.venv, ignore_errors=True)
            (self.root / "install-error.txt").write_text(str(exc)[:500])
            raise VoiceError(str(exc)) from exc

    def install_in_background(self) -> None:
        with self._lock:
            if self._install is not None:
                return
            self._install = {"step": "Starting", "done": 0.0, "size": SIZE_NOTE}

        def report(step: str, done: float) -> None:
            self._install = {"step": step, "done": round(done, 3), "size": SIZE_NOTE}

        def run() -> None:
            try:
                self.install(report)
            except VoiceError:
                pass  # recorded in install-error.txt for status()
            finally:
                self._install = None

        threading.Thread(target=run, name="voice-install", daemon=True).start()

    def remove(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _download(self, report: Callable[[str, float], None]) -> None:
        """Fetch the pinned model files from the kokoro-onnx release, hashing as
        they arrive; a file that doesn't match is deleted, never used."""
        pinned = manifest()
        total = sum(meta["size"] for meta in pinned["files"].values())
        self.model.mkdir(parents=True, exist_ok=True)
        done = 0
        for name, meta in pinned["files"].items():
            target = self.model / name
            if target.is_file() and _sha256(target) == meta["sha256"]:
                done += meta["size"]
                continue
            partial = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            url = pinned["release"] + name
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
                for block in iter(lambda: response.read(1 << 20), b""):
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    report(
                        f"Downloading the voice model ({SIZE_NOTE} in all)",
                        0.45 + 0.5 * min(1.0, done / total),
                    )
            if digest.hexdigest() != meta["sha256"]:
                partial.unlink(missing_ok=True)
                raise VoiceError(f"{name} doesn't match its pinned checksum")
            partial.replace(target)

    @staticmethod
    def _run(cmd: list[str]) -> str:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if done.returncode:
            raise VoiceError(f"{Path(cmd[0]).name} failed: {(done.stderr or done.stdout)[-300:]}")
        return done.stdout

    # ── speaking ──

    def say(self, text: str, voice: str) -> Path:
        """A WAV of this line in this voice, from the cache or the worker."""
        if not VOICE_ID.fullmatch(voice) or not 0 < len(text) <= 300:
            raise VoiceError("unknown voice or bad text")
        if not self.installed():
            raise VoiceError("Natural voices aren't installed.")
        model = manifest()["files"][manifest()["onnx"]]["sha256"]
        key = hashlib.sha256(f"{model}|{voice}|{text}".encode()).hexdigest()[:32]
        path = self.cache / f"{key}.wav"
        if path.is_file():
            os.utime(path)  # recently used, for pruning
            return path
        self.cache.mkdir(parents=True, exist_ok=True)
        # The lock serializes requests to the one worker. stop() doesn't take it:
        # voice-off and shutdown kill the worker, and a request in flight then
        # fails fast instead of making them wait.
        with self._lock:
            worker = self._ensure_worker()
            request = {"id": uuid.uuid4().hex, "text": text, "voice": voice, "out": str(path)}
            try:
                assert worker.stdin is not None
                worker.stdin.write((json.dumps(request) + "\n").encode())
                worker.stdin.flush()
                reply = self._read(worker, SAY_TIMEOUT_S)
            except (OSError, ValueError, VoiceError) as exc:
                self._retire(worker)
                raise VoiceError(f"The voice worker stopped: {exc}") from exc
        if not reply.get("ok"):
            raise VoiceError(str(reply.get("error") or "synthesis failed"))
        self._prune()
        return path

    def stop(self) -> None:
        """Kill the worker now, even mid-request (voice-off, server shutdown)."""
        worker, self._worker = self._worker, None
        _kill(worker)

    def _retire(self, worker: "subprocess.Popen[bytes]") -> None:
        """Kill a worker that failed, unless a newer one has replaced it."""
        if self._worker is worker:
            self._worker = None
        _kill(worker)

    def _ensure_worker(self) -> "subprocess.Popen[bytes]":
        if self._worker is not None and self._worker.poll() is None:
            return self._worker
        if self._worker is not None:
            self._retire(self._worker)  # it exited on its own
        log = (self.root / "worker.log").open("a")
        worker = subprocess.Popen(
            [str(self.venv / "bin" / "python"), str(WORKER), str(self.model), str(MANIFEST)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            bufsize=0,
        )
        log.close()
        self._worker = worker
        try:
            self._read(worker, START_TIMEOUT_S)
        except (ValueError, VoiceError):
            self._retire(worker)
            raise
        return worker

    @staticmethod
    def _read(worker: "subprocess.Popen[Any]", timeout: float) -> dict[str, Any]:
        """One reply line, within `timeout` for the WHOLE line: a worker that
        writes part of a line and stalls must not block past the deadline, so
        this reads the pipe's raw bytes rather than a buffered readline."""
        assert worker.stdout is not None
        fd = worker.stdout.fileno()
        deadline = time.monotonic() + timeout
        line = b""
        while not line.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise VoiceError(f"no answer within {timeout:.0f} s")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise VoiceError("the worker exited")
            line += chunk
        reply: dict[str, Any] = json.loads(line.split(b"\n", 1)[0])
        return reply

    def _prune(self) -> None:
        files = sorted(self.cache.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for oldest in files:
            if total <= CACHE_LIMIT:
                break
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)


_LIST_DISTS = """
import importlib.metadata as md, json
rows = []
for d in md.distributions():
    m = d.metadata
    classifiers = [c for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    license = m.get("License-Expression") or m.get("License") or ""
    rows.append({"name": m["Name"], "license": license, "classifiers": "; ".join(classifiers)})
print(json.dumps(rows))
"""


def _kill(worker: "subprocess.Popen[Any] | None") -> None:
    if worker is None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=3)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()
