import base64
import hashlib
import importlib.util
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "controller", Path(__file__).parents[1] / "wsl/controller.py"
)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.client = controller.Controller(self.home)

    def test_foreign_workspace_is_not_adopted(self):
        self.client.workspace.mkdir(mode=0o700)
        content = self.client.workspace / "keep.txt"
        content.write_text("existing data")
        with self.assertRaises(controller.Failure):
            controller.private_directory(self.client.workspace)
        self.assertEqual(content.read_text(), "existing data")
        self.assertFalse((self.client.workspace / "windows-package.json").exists())

    def test_symlinked_workspace_is_rejected(self):
        target = self.home / "existing"
        target.mkdir()
        self.client.workspace.symlink_to(target)
        with self.assertRaises(controller.Failure):
            controller.private_directory(self.client.workspace)
        self.assertEqual(list(target.iterdir()), [])

    def test_only_one_install_or_launch_holds_lock(self):
        with self.client.lock():
            with self.assertRaises(controller.Failure) as caught, self.client.lock():
                self.fail("second lock acquired")
            self.assertEqual(caught.exception.code, "busy")

    def test_receipt_cannot_escape_runtime_directory(self):
        with self.client.lock():
            controller.write_json(self.client.root / "active.json", {"sha256": "../../somewhere"})
            with self.assertRaises(controller.Failure):
                self.client.active()

    def test_corrupt_payload_never_creates_workspace(self):
        with self.client.lock(), self.assertRaises(controller.Failure):
            self.client.install(
                {
                    "wheel_name": "duckterm-0.1.0-py3-none-any.whl",
                    "wheel_base64": base64.b64encode(b"wheel").decode(),
                    "sha256": "a" * 64,
                }
            )
        self.assertFalse(self.client.workspace.exists())

    def test_path_traversal_wheel_is_rejected(self):
        with self.assertRaises(controller.Failure):
            self.client.install({"wheel_name": "../duckterm-0.1.0-py3-none-any.whl"})

    def test_launch_environment_does_not_inherit_another_instance(self):
        with patch.dict(
            os.environ,
            {
                "DUCKTERM_DESKTOP_PROFILE": "full",
                "DUCKTERM_URL": "http://other.invalid",
                "DUCKTERM_SESSION_TOKEN_FILE": "/other/token",
                "PYTHONPATH": "/other/code",
            },
        ):
            environment = self.client.environment({"sha256": "a" * 64})
        self.assertEqual(environment["DUCKTERM_EDITION"], "lite")
        self.assertEqual(environment["DUCKTERM_HOME"], str(self.client.workspace))
        self.assertEqual(environment["DUCKTERM_URL"], "http://127.0.0.1:4317")
        for key in ("DUCKTERM_DESKTOP_PROFILE", "DUCKTERM_SESSION_TOKEN_FILE", "PYTHONPATH"):
            self.assertNotIn(key, environment)

    def test_status_has_no_token_and_does_not_launch(self):
        result = self.client.dispatch({"protocol": 1, "action": "status"})
        self.assertEqual(result, {"state": "not_installed"})
        self.assertFalse(self.client.workspace.exists())

    def test_private_reader_does_not_follow_symlinks(self):
        target = self.home / "target"
        target.write_text("secret")
        linked = self.home / "linked"
        linked.symlink_to(target)
        with self.assertRaises(OSError):
            controller.private_read(linked)

    def test_unknown_commands_do_not_create_state(self):
        with self.assertRaises(controller.Failure):
            self.client.dispatch({"protocol": 1, "action": "delete"})
        self.assertFalse(self.client.root.exists())


@unittest.skipUnless(
    os.environ.get("DUCKTERM_WINDOWS_TEST_WHEEL") and sys.platform == "linux",
    "requires a built wheel and Linux integration tools",
)
class LifecycleTests(unittest.TestCase):
    def test_install_launch_reconnect_restart_and_conflict(self):
        import signal
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as directory:
            client = controller.Controller(directory)
            wheel = Path(os.environ["DUCKTERM_WINDOWS_TEST_WHEEL"])
            payload = wheel.read_bytes()
            request = {
                "protocol": 1,
                "action": "install",
                "wheel_name": wheel.name,
                "wheel_base64": base64.b64encode(payload).decode(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            running = None
            agent_key = "windows-package-test"
            try:
                result = client.dispatch(request)
                self.assertEqual(result["state"], "installed")
                self.assertEqual(client.dispatch(request), result)  # idempotent installation
                with socket.socket() as conflict:
                    conflict.bind(("127.0.0.1", controller.PORT))
                    conflict.listen()
                    with self.assertRaises(controller.Failure) as caught:
                        client.dispatch({"protocol": 1, "action": "start"})
                    self.assertEqual(caught.exception.code, "port_conflict")
                running = client.dispatch({"protocol": 1, "action": "start"})
                self.assertEqual(running["state"], "running")
                same = client.dispatch({"protocol": 1, "action": "start"})
                self.assertEqual(same["pid"], running["pid"])
                self.assertNotIn("token", client.dispatch({"protocol": 1, "action": "status"}))
                # A real owned test session must survive a backend restart.
                import urllib.request

                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

                def api(path, body=None, method=None):
                    request = urllib.request.Request(
                        client.url + path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={
                            "X-Duckterm-Token": running["token"],
                            "Content-Type": "application/json",
                        },
                        method=method,
                    )
                    with opener.open(request, timeout=15) as response:
                        return json.loads(response.read())

                launched = api(
                    "/sessions/launch",
                    {
                        "command": "sleep 120",
                        "cwd": directory,
                        "session_key": agent_key,
                        "test": True,
                        "in_terminal": False,
                    },
                )
                self.assertIsNotNone(launched)
                previous = running["pid"]
                running = client.dispatch({"protocol": 1, "action": "restart"})
                self.assertNotEqual(previous, running["pid"])
                target = "rd_" + agent_key
                live = subprocess.run(
                    ["tmux", "-L", "duckterm-windows-lite", "has-session", "-t", "=" + target],
                    capture_output=True,
                )
                self.assertEqual(live.returncode, 0, live.stderr)
                api("/sessions/" + agent_key, method="DELETE")
            finally:
                subprocess.run(
                    ["tmux", "-L", "duckterm-windows-lite", "kill-server"], capture_output=True
                )
                # Dedicated CI workspace only; do not affect any normal instance.
                if running:
                    with controller.contextlib.suppress(ProcessLookupError):
                        os.kill(running["pid"], signal.SIGTERM)
                    time.sleep(1)


if __name__ == "__main__":
    unittest.main()
