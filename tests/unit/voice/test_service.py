import hashlib
import io
import json
import platform
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from duckterm.voice import service
from duckterm.voice.service import LocalVoice, VoiceError, gpl_violations, verify_model

FAKE_WORKER = """
import json, sys, time, wave
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req["text"] == "slow":
        time.sleep(30)
    if req["text"] == "crash":
        sys.exit(3)
    if req["text"] == "half a reply":
        sys.stdout.write('{"id": '); sys.stdout.flush(); time.sleep(30)
    with wave.open(req["out"], "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\\0\\0" * 2400)
    print(json.dumps({"id": req["id"], "ok": True}), flush=True)
"""


@pytest.fixture
def voice(tmp_path, monkeypatch):
    """An installed voice whose worker is a stdlib stand-in for Kokoro."""
    monkeypatch.setattr(service, "unsupported_reason", lambda: None)
    root = tmp_path / "voice"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").symlink_to(sys.executable)
    (root / "model").mkdir()
    with zipfile.ZipFile(root / "model" / service.manifest()["voices"], "w") as pack:
        for v in ("af_heart", "bf_emma", "jf_alpha"):  # jf_alpha: a Japanese voice, not listed
            pack.writestr(f"{v}.npy", b"")
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER)
    monkeypatch.setattr(service, "WORKER", worker)
    onnx = service.manifest()["onnx"]
    record = {"model": service.manifest()["files"][onnx]["sha256"], "lock": service.lock_digest()}
    (root / "installed.json").write_text(json.dumps(record))
    v = LocalVoice(root)
    yield v
    v.stop()


def test_gpl_packages_are_refused_and_lgpl_is_allowed() -> None:
    dists = [
        {
            "name": "num2words",
            "license": "LGPL",
            "classifiers": "License :: GNU Library or Lesser General Public License (LGPL)",
        },
        {"name": "mlx", "license": "MIT", "classifiers": ""},
        {"name": "phonemizer-fork", "license": "", "classifiers": ""},
        {"name": "espeakng-loader", "license": "", "classifiers": ""},
        {"name": "sneaky", "license": "GPL-3.0-or-later", "classifiers": ""},
    ]
    assert gpl_violations(dists) == ["phonemizer-fork", "espeakng-loader", "sneaky"]


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_pinned_locks_leave_out_espeak_and_heavy_runtimes(arch, monkeypatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: arch)
    lock = service.lock_file().read_text().lower()
    names = {line.split("==")[0].strip() for line in lock.splitlines() if "==" in line}
    assert names.isdisjoint({"phonemizer", "phonemizer-fork", "espeakng-loader", "torch", "mlx"})
    assert {"onnxruntime", "misaki", "spacy"} <= names
    assert lock.count("--hash=sha256:") >= len(names)  # every pin carries its hashes


def test_model_files_are_checked_against_their_pinned_hashes(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}")
    files = {
        "config.json": {
            "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        },
        "kokoro-v1_0.safetensors": {"sha256": "0" * 64},
    }
    assert verify_model(tmp_path, files) == ["kokoro-v1_0.safetensors"]
    (tmp_path / "config.json").write_text("tampered")
    assert verify_model(tmp_path, files) == ["config.json", "kokoro-v1_0.safetensors"]


def test_status_is_honest_about_every_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service.sys, "platform", "linux")
    assert LocalVoice(tmp_path).status() == {
        "state": "unsupported",
        "reason": "Natural voices need a Mac.",
    }
    monkeypatch.setattr(service, "unsupported_reason", lambda: None)
    assert LocalVoice(tmp_path).status() == {"state": "absent", "size": service.SIZE_NOTE}
    (tmp_path / "install-error.txt").write_text("Model download failed: offline")
    assert LocalVoice(tmp_path).status()["state"] == "failed"


def test_install_fails_closed_when_a_gpl_package_appears(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(service, "unsupported_reason", lambda: None)
    monkeypatch.setattr(service, "find_tool", lambda name: "/usr/bin/true")
    listing = json.dumps([{"name": "phonemizer-fork", "license": "", "classifiers": ""}])

    def run(cmd: list[str]) -> str:
        (tmp_path / "venv" / "bin").mkdir(parents=True, exist_ok=True)
        return listing if "-c" in cmd else ""

    monkeypatch.setattr(LocalVoice, "_run", staticmethod(run))
    downloaded = []
    monkeypatch.setattr(LocalVoice, "_download", lambda self, report: downloaded.append(report))
    with pytest.raises(VoiceError, match="Refusing GPL packages: phonemizer-fork"):
        LocalVoice(tmp_path).install()
    assert not (tmp_path / "venv").exists()  # nothing half-installed is left behind
    assert downloaded == []
    assert "phonemizer-fork" in (tmp_path / "install-error.txt").read_text()


def test_says_a_line_then_serves_repeats_from_the_cache(voice) -> None:
    first = voice.say("architect needs your input", "af_heart")
    assert first.read_bytes()[:4] == b"RIFF"
    voice.stop()  # a repeat mustn't need the worker at all
    assert voice.say("architect needs your input", "af_heart") == first
    assert voice._worker is None


def test_a_slow_worker_times_out_and_is_killed(voice, monkeypatch) -> None:
    monkeypatch.setattr(service, "SAY_TIMEOUT_S", 0.5)
    voice.say("warm up", "af_heart")
    worker = voice._worker
    started = time.monotonic()
    with pytest.raises(VoiceError, match="no answer within"):
        voice.say("slow", "af_heart")
    assert time.monotonic() - started < 5
    assert voice._worker is None and worker.poll() is not None  # reaped, not leaked


def test_a_crashed_worker_fails_the_request_and_restarts_next_time(voice) -> None:
    with pytest.raises(VoiceError, match="worker"):
        voice.say("crash", "af_heart")
    assert voice.say("back again", "bf_emma").is_file()


def test_bad_input_and_missing_install_are_refused(voice, tmp_path, monkeypatch) -> None:
    with pytest.raises(VoiceError):
        voice.say("hello", "../../etc/passwd")
    with pytest.raises(VoiceError):
        voice.say("x" * 301, "af_heart")
    monkeypatch.setattr(service, "unsupported_reason", lambda: None)
    with pytest.raises(VoiceError, match="aren't installed"):
        LocalVoice(tmp_path / "elsewhere").say("hello", "af_heart")


def test_cache_is_pruned_oldest_first(voice, monkeypatch) -> None:
    monkeypatch.setattr(service, "CACHE_LIMIT", 12_000)  # room for two 4.8 KB clips
    paths = []
    for i in range(4):
        paths.append(voice.say(f"line {i}", "af_heart"))
        time.sleep(0.02)
    assert [p.exists() for p in paths] == [False, False, True, True]


def test_voices_lists_installed_english_voices(voice) -> None:
    assert voice.status() == {
        "state": "ready",
        "voices": [
            {"id": "af_heart", "label": "Heart", "accent": "US"},
            {"id": "bf_emma", "label": "Emma", "accent": "UK"},
        ],
    }


def test_stop_reaps_the_worker(voice) -> None:
    voice.say("hello", "af_heart")
    worker = voice._worker
    voice.stop()
    assert worker.poll() is not None


def test_worker_script_never_imports_into_the_server() -> None:
    server = Path(service.__file__).resolve().parents[1] / "server.py"
    assert "voice.worker" not in server.read_text()


def test_download_keeps_only_files_that_match_their_pinned_hash(tmp_path, monkeypatch) -> None:
    good, bad = b"voices", b"tampered"
    pinned = {
        "release": "https://example.invalid/",
        "onnx": "model.onnx",
        "voices": "voices.bin",
        "files": {
            "voices.bin": {"sha256": hashlib.sha256(good).hexdigest(), "size": len(good)},
            "model.onnx": {"sha256": hashlib.sha256(b"real").hexdigest(), "size": 4},
        },
    }
    monkeypatch.setattr(service, "manifest", lambda: pinned)
    served = {"voices.bin": good, "model.onnx": bad}
    monkeypatch.setattr(
        service.urllib.request,
        "urlopen",
        lambda url, timeout: io.BytesIO(served[url.rsplit("/", 1)[1]]),
    )
    with pytest.raises(VoiceError, match="model.onnx doesn't match its pinned checksum"):
        LocalVoice(tmp_path)._download(lambda step, done: None)
    assert (tmp_path / "model" / "voices.bin").read_bytes() == good
    assert not list((tmp_path / "model").glob("model.onnx*"))  # nothing unverified is kept


# From main-qa's worker-contract probes on 5037a8d.
def test_a_worker_that_stalls_mid_reply_still_times_out(voice, monkeypatch) -> None:
    monkeypatch.setattr(service, "SAY_TIMEOUT_S", 0.5)
    voice.say("warm up", "af_heart")
    worker = voice._worker
    started = time.monotonic()
    with pytest.raises(VoiceError, match="no answer within"):
        voice.say("half a reply", "af_heart")
    assert time.monotonic() - started < 3
    assert worker.poll() is not None


def test_voice_off_interrupts_a_request_in_flight(voice) -> None:
    voice.say("warm up", "af_heart")
    worker = voice._worker
    failed: list[Exception] = []

    def speak() -> None:
        try:
            voice.say("slow", "af_heart")
        except VoiceError as exc:
            failed.append(exc)

    thread = threading.Thread(target=speak)
    thread.start()
    time.sleep(0.3)  # the request is now waiting on the worker
    started = time.monotonic()
    voice.stop()
    assert time.monotonic() - started < 2  # didn't wait for the 15 s timeout
    thread.join(5)
    assert worker.poll() is not None and failed and not thread.is_alive()
