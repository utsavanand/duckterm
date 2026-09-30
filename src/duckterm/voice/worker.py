"""Kokoro voice worker. Runs ONLY inside ~/.duckterm/voice/venv, started by the
server as a subprocess; the server never imports this file.

Speech is one ONNX Runtime call: misaki turns text into phonemes, the
manifest's vocabulary turns phonemes into token ids, and the voice pack gives
a style vector per length. The kokoro-onnx package would do the same, but it
imports the GPL espeak phonemizer at load, so it isn't used.

Protocol, one JSON object per line. The worker prints {"ready": true} once the
model is loaded, then answers each request {"id", "text", "voice", "out"} with
{"id", "ok": true} after writing a 24 kHz mono WAV to "out", or
{"id", "ok": false, "error"}.

Usage: python worker.py MODEL_DIR MANIFEST_JSON
"""

import json
import os
import sys
import types
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from names import speakable  # noqa: E402  (stdlib-only sibling, imported by path)

UNKNOWN = "❓"
SAMPLE_RATE = 24000


def main() -> None:
    model_dir, manifest_path = sys.argv[1], sys.argv[2]
    # Keep the protocol on the real stdout; libraries may print.
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    # misaki's espeak fallback is GPL and never installed; a stub stands in, and
    # names.speakable keeps the words it would have dropped.
    stub = types.ModuleType("misaki.espeak")
    stub.EspeakFallback = None  # type: ignore[attr-defined]
    sys.modules["misaki.espeak"] = stub

    import numpy as np
    import onnxruntime as rt
    from misaki import en

    with open(manifest_path) as f:
        manifest = json.load(f)
    vocab = manifest["vocab"]
    voices = np.load(os.path.join(model_dir, manifest["voices"]))
    session = rt.InferenceSession(
        os.path.join(model_dir, manifest["onnx"]), providers=["CPUExecutionProvider"]
    )
    g2p = {
        "a": en.G2P(trf=False, british=False, fallback=None, unk=UNKNOWN),
        "b": en.G2P(trf=False, british=True, fallback=None, unk=UNKNOWN),
    }
    protocol.write(json.dumps({"ready": True}) + "\n")

    for line in sys.stdin:
        req = json.loads(line)
        try:
            voice = req["voice"]
            convert = g2p[voice[0]]
            text = speakable(req["text"], lambda word, g=convert: UNKNOWN not in g(word)[0])
            tokens = [vocab[p] for p in convert(text)[0] if p in vocab]
            style = voices[voice][min(len(tokens), len(voices[voice])) - 1]
            audio = session.run(
                None,
                {
                    "tokens": np.array([[0, *tokens, 0]], dtype=np.int64),
                    "style": np.asarray(style, dtype=np.float32),
                    "speed": np.array([1.0], dtype=np.float32),
                },
            )[0].ravel()
            pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
            partial = req["out"] + ".part"
            with wave.open(partial, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                out.writeframes(pcm.tobytes())
            os.replace(partial, req["out"])
            reply = {"id": req["id"], "ok": True}
        except Exception as exc:  # noqa: BLE001 - reported to the server, which falls back
            reply = {"id": req.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        protocol.write(json.dumps(reply) + "\n")


if __name__ == "__main__":
    main()
