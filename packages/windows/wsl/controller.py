"""Private WSL adapter for the Windows package. No changes to the shared server.

The launcher sends one JSON request on stdin and consumes one JSON response.
Only start/restart responses contain a login token; callers must never log them.
"""

import base64
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROTOCOL = 1
INSTANCE = "windows-lite"
PORT = 4317
TOOLS = ("tmux", "jq", "curl", "git", "lsof")
MAX_WHEEL = 32 * 1024 * 1024
MARKER = {"protocol": PROTOCOL, "owner": "duckterm-windows"}


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def private_directory(path):
    """Never adopt a pre-existing directory as a package-owned workspace."""
    created = False
    try:
        path.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Failure(
            "unsafe_directory", "The Windows workspace must be a private owned directory."
        )
    marker = path / "windows-package.json"
    if created:
        write_json(marker, MARKER)
    elif read_json(marker) != MARKER:
        raise Failure("workspace_conflict", "An existing directory is not owned by this package.")


def private_read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise Failure("unsafe_file", "A Windows package state file is not private and owned.")
        return stream.read(65537)


def read_json(path):
    value = private_read(path)
    return json.loads(value) if value is not None else None


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Controller:
    def __init__(self, home=None):
        self.home = Path.home() if home is None else Path(home)
        self.root = self.home / ".duckterm-windows-package"
        self.workspace = self.home / ".duckterm-windows-lite"
        self.url = f"http://127.0.0.1:{PORT}"

    @contextlib.contextmanager
    def lock(self):
        private_directory(self.root)
        fd = os.open(self.root / "controller.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Failure(
                "busy", "Another Windows install or launch is in progress. Try again."
            ) from None
        try:
            yield
        finally:
            os.close(fd)

    def active(self):
        receipt = read_json(self.root / "active.json")
        if receipt is None:
            return None
        if not isinstance(receipt, dict) or not re.fullmatch(
            "[a-f0-9]{64}", str(receipt.get("sha256", ""))
        ):
            raise Failure("invalid_installation", "The Windows installation receipt is invalid.")
        runtime = self.root / receipt["sha256"]
        if runtime.is_symlink() or read_json(runtime / "installed.json") != receipt:
            raise Failure(
                "invalid_installation", "The Windows runtime is incomplete; reinstall the package."
            )
        return receipt

    def environment(self, receipt):
        environment = {
            k: v for k, v in os.environ.items() if not k.startswith(("DUCKTERM_", "PYTHON"))
        }
        binary = self.root / receipt["sha256"] / "bin"
        environment.update(
            HOME=str(self.home),
            DUCKTERM_INSTANCE=INSTANCE,
            DUCKTERM_HOME=str(self.workspace),
            DUCKTERM_PORT=str(PORT),
            DUCKTERM_URL=self.url,
            DUCKTERM_TMUX_SOCKET="duckterm-" + INSTANCE,
            DUCKTERM_EDITION="lite",
            DUCKTERM_NO_BROWSER="1",
            PATH=os.pathsep.join(
                [str(binary), str(self.home / ".local/bin"), environment.get("PATH", os.defpath)]
            ),
        )
        return environment

    def doctor(self):
        missing = [tool for tool in TOOLS if shutil.which(tool) is None]
        # The adapter probes the user's Python before installing the application.
        if sys.version_info < (3, 11):  # noqa: UP036
            missing.append("python3 >= 3.11")
        probe = subprocess.run(
            [sys.executable, "-I", "-c", "import ensurepip, venv"], capture_output=True
        )
        if probe.returncode:
            missing.append("python3-venv")
        return {"state": "ready" if not missing else "missing_dependencies", "missing": missing}

    def install(self, request):
        wheel_name = request.get("wheel_name", "")
        if not re.fullmatch(r"duckterm-[0-9][A-Za-z0-9.+]*-py3-none-any\.whl", wheel_name):
            raise Failure("invalid_package", "The payload must be a DuckTerm Python wheel.")
        data = base64.b64decode(request.get("wheel_base64", ""), validate=True)
        digest = hashlib.sha256(data).hexdigest()
        if not data or len(data) > MAX_WHEEL or digest != request.get("sha256"):
            raise Failure("invalid_package", "The backend payload checksum or size is invalid.")
        diagnosis = self.doctor()
        if diagnosis["missing"]:
            raise Failure(
                "missing_dependencies",
                "Install WSL prerequisites: " + ", ".join(diagnosis["missing"]),
            )
        receipt = {"sha256": digest, "wheel_name": wheel_name}
        runtime = self.root / digest
        complete = False
        if runtime.exists() or runtime.is_symlink():
            if runtime.is_symlink():
                raise Failure(
                    "incomplete_installation",
                    "An unrecognized runtime exists; it was left unchanged.",
                )
            complete = read_json(runtime / "installed.json") == receipt
            if not complete and read_json(runtime / "pending.json") != receipt:
                raise Failure(
                    "incomplete_installation",
                    "An unrecognized runtime exists; it was left unchanged.",
                )
        else:
            runtime.mkdir(mode=0o700)
            write_json(runtime / "pending.json", receipt)
        if not complete:
            # Create at its final immutable path: venv entrypoints contain absolute paths.
            result = subprocess.run(
                [sys.executable, "-I", "-m", "venv", str(runtime)], capture_output=True, timeout=120
            )
            if result.returncode:
                raise Failure(
                    "install_failed",
                    "Could not create the private Python environment. Check python3-venv.",
                )
            wheel = runtime / wheel_name
            wheel.write_bytes(data)
            python = runtime / "bin/python"
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    str(wheel),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode:
                raise Failure("install_failed", "Could not install the bundled backend wheel.")
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import duckterm.server, duckterm.cli; "
                    "from importlib.resources import files; "
                    "assert files('duckterm').joinpath('dashboard/index.html').is_file()",
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode:
                raise Failure(
                    "install_failed", "The bundled backend failed its import/dashboard check."
                )
            write_json(runtime / "installed.json", receipt)
            (runtime / "pending.json").unlink(missing_ok=True)
        private_directory(self.workspace)
        write_json(self.root / "active.json", receipt)
        return {"state": "installed", "wheel_name": wheel_name}

    def listening(self):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return True
        except OSError as exc:
            if exc.errno == errno.ECONNREFUSED:
                return False
            raise Failure(
                "network_unavailable", "Could not probe the Windows workspace port."
            ) from None

    def verify_process(self):
        """Do not send the workspace token to an unrelated listener."""
        pid_text = private_read(self.workspace / "server.pid")
        try:
            pid = int(pid_text or "0")
            if pid <= 0:
                raise ValueError()
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            executable = Path(os.fsdecode(args[0]))
            runtime = executable.parent.parent
            valid = (
                runtime.parent == self.root
                and re.fullmatch("[a-f0-9]{64}", runtime.name)
                and read_json(runtime / "installed.json") is not None
                and executable == runtime / "bin/python"
                and args[1:4] == [b"-I", b"-m", b"duckterm.cli"]
                and args[4] in {b"serve", b"restart"}
            )
            listeners = subprocess.run(
                ["lsof", "-t", f"-iTCP:{PORT}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if (
                not valid
                or listeners.returncode != 0
                or set(listeners.stdout.split()) != {str(pid)}
            ):
                raise ValueError()
            return pid
        except (OSError, ValueError, IndexError):
            raise Failure(
                "port_conflict",
                "Port 4317 belongs to another or unverified process; it was left running.",
            ) from None

    def status(self, *, login=False):
        receipt = self.active()
        if receipt is None:
            return {"state": "not_installed"}
        private_directory(self.workspace)
        if not self.listening():
            return {"state": "stopped", "wheel_name": receipt["wheel_name"]}
        pid = self.verify_process()
        token = (private_read(self.workspace / "token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise Failure("invalid_token", "The workspace login token is missing or invalid.")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            request = urllib.request.Request(
                self.url + "/edition", headers={"X-Duckterm-Token": token}
            )
            with opener.open(request, timeout=3) as response:
                description = json.loads(response.read(65536))
        except (OSError, ValueError):
            raise Failure(
                "server_unavailable", "The workspace server did not return its identity."
            ) from None
        identity = description.get("instance", {})
        expected = {
            "instance_id": INSTANCE,
            "home": str(self.workspace),
            "tmux_socket": "duckterm-" + INSTANCE,
            "port": PORT,
            "profile_id": None,
        }
        if description.get("id") != "lite" or any(
            identity.get(k) != v for k, v in expected.items()
        ):
            raise Failure(
                "identity_conflict", "The running server does not own this Windows Lite workspace."
            )
        result = {
            "state": "running",
            "url": self.url,
            "pid": pid,
            "wheel_name": receipt["wheel_name"],
        }
        if login:
            result["token"] = token
        return result

    def start(self, *, restart=False):
        receipt = self.active()
        if receipt is None:
            raise Failure("not_installed", "Run install before launching DuckTerm Lite.")
        status = self.status(login=True)
        if status["state"] == "running" and not restart:
            return status
        old_pid = status.get("pid")
        environment = self.environment(receipt)
        python = self.root / receipt["sha256"] / "bin/python"
        log = self.root / "server.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "ab") as output:
            process = subprocess.Popen(
                [
                    str(python),
                    "-I",
                    "-m",
                    "duckterm.cli",
                    "restart" if restart and old_pid else "serve",
                    "--edition",
                    "lite",
                ],
                cwd=self.home,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
                close_fds=True,
                umask=0o077,
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise Failure(
                    "launch_failed",
                    "The backend exited. Inspect ~/.duckterm-windows-package/server.log in WSL.",
                )
            try:
                status = self.status(login=True)
                if status["state"] == "running" and status["pid"] != old_pid:
                    return status
            except Failure as exc:
                if exc.code not in {"port_conflict", "invalid_token", "server_unavailable"}:
                    raise
            time.sleep(0.2)
        raise Failure(
            "launch_timeout", "The backend is still starting. Run status before trying again."
        )

    def dispatch(self, request):
        action = request.get("action")
        if request.get("protocol") != PROTOCOL or action not in {
            "doctor",
            "install",
            "status",
            "start",
            "restart",
        }:
            raise Failure("invalid_request", "Unsupported Windows launcher request.")
        if action == "doctor":
            return self.doctor()
        with self.lock():
            if action == "install":
                return self.install(request)
            if action == "status":
                return self.status()
            return self.start(restart=action == "restart")


def main():
    try:
        if os.getuid() == 0:
            raise Failure("root_user", "Use a regular WSL user, not root, to run DuckTerm.")
        if "microsoft-standard" not in platform.release().lower():
            raise Failure("wsl2_required", "Select a WSL 2 distribution for DuckTerm Lite.")
        raw = sys.stdin.buffer.read(MAX_WHEEL * 2 + 1)
        if len(raw) > MAX_WHEEL * 2:
            raise Failure("invalid_request", "The launcher request is too large.")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise Failure("invalid_request", "The launcher request must be an object.")
        result = Controller().dispatch(request)
        print(json.dumps({"protocol": PROTOCOL, "ok": True, **result}))
        return 0
    except Failure as exc:
        print(
            json.dumps({"protocol": PROTOCOL, "ok": False, "code": exc.code, "message": str(exc)})
        )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "code": "operation_failed",
                    "message": "The WSL operation failed. Check the installation and run doctor.",
                }
            )
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
