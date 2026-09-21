"""Exercise the real Swift transfer bridge against two isolated local HTTP servers.

No remote upload, provider login, or real project data is used. The synthetic
session is test-flagged, stopped, and deleted before both temporary servers exit.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port() -> int:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def request(base: str, operation: str, body: dict) -> dict:
    with urllib.request.urlopen(base) as response:
        html = response.read().decode()
    token = re.search(r'<meta name="duckterm-token" content="([A-Za-z0-9_-]+)">', html)[1]
    req = urllib.request.Request(
        base + operation,
        data=json.dumps(body).encode(),
        headers={"X-Duckterm-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def main() -> None:
    processes = []
    sockets = []
    with tempfile.TemporaryDirectory(prefix="duckterm-native-transfer-") as directory:
        root = Path(directory)
        source = root / "project with spaces"
        source.mkdir()
        (source / "hello.txt").write_text("synthetic native transfer\n")
        (source / ".env").write_text("SYNTHETIC_SECRET=excluded\n")
        destination = root / "destination with spaces"
        bases = []
        try:
            for index in range(2):
                number = port()
                socket_name = f"duckterm-native-transfer-{os.getpid()}-{index}"
                sockets.append(socket_name)
                env = {
                    **os.environ,
                    "DUCKTERM_HOME": str(root / f"state-{index}"),
                    "DUCKTERM_TMUX_SOCKET": socket_name,
                    "DUCKTERM_SUMMARIZER": "off",
                    "DUCKTERM_NO_BROWSER": "1",
                }
                env.pop("DUCKTERM_HOSTED", None)
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "duckterm.cli",
                            "serve",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(number),
                        ],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
                base = f"http://127.0.0.1:{number}"
                bases.append(base)
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(base, timeout=1) as response:
                            assert response.headers.get("X-Duckterm") == "1"
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Local rehearsal server failed to start")
            swift = root / "Smoke.swift"
            swift.write_text(
                """import Foundation
@main struct Smoke {
  @MainActor static func main() async throws {
    let args = CommandLine.arguments
    let api = LaunchDestination()
    let local = URL(string: args[1])!, remote = URL(string: args[2])!
    let review = try await api.perform(base: local, operation: "transfer-preview", params: ["source": args[3]]) as! [String: Any]
    let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    let params: [String: Any] = ["id": id, "source": args[3], "destination": args[4], "fingerprint": review["fingerprint"]!]
    let transfer = ProjectTransfer()
    let copied = try await transfer.copy(api: api, local: local, remote: remote, params: params) as! [String: Any]
    precondition(copied["stage"] as? String == "ready")
    let again = try await transfer.copy(api: api, local: local, remote: remote, params: params) as! [String: Any]
    precondition(again["stage"] as? String == "ready")
    let launch: [String: Any] = ["id": id, "command": "/bin/cat", "name": "Synthetic native transfer", "test": true]
    let started = try await api.perform(base: remote, operation: "transfer-launch", params: launch) as! [String: Any]
    let retried = try await api.perform(base: remote, operation: "transfer-launch", params: launch) as! [String: Any]
    precondition(started["session_key"] as? String == retried["session_key"] as? String)
    print(started["session_key"] as! String)
  }
}
"""
            )
            executable = root / "smoke"
            subprocess.run(
                [
                    "swiftc",
                    "-parse-as-library",
                    str(ROOT / "mac/Sources/Duckterm/LaunchDestination.swift"),
                    str(ROOT / "mac/Sources/Duckterm/ProjectTransfer.swift"),
                    str(swift),
                    "-o",
                    str(executable),
                ],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                [str(executable), *bases, str(source), str(destination)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            key = result.stdout.strip()
            assert (destination / "hello.txt").read_bytes() == (source / "hello.txt").read_bytes()
            assert not (destination / ".env").exists()
            request(bases[1], f"/sessions/{key}/stop", {})
            with urllib.request.urlopen(bases[1] + "/sessions") as response:
                rows = json.load(response)["sessions"]
            assert len(rows) == 1 and rows[0]["test"] == 1
            print(
                json.dumps(
                    {
                        "native_copy": True,
                        "repeat_copy": True,
                        "repeat_launch_same_session": True,
                        "secret_excluded": True,
                        "source_preserved": True,
                    }
                )
            )
        finally:
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for name in sockets:
                if shutil.which("tmux"):
                    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)
            # The temporary databases and all synthetic test records are removed here.


if __name__ == "__main__":
    main()
