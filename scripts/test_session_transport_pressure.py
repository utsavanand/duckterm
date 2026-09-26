"""Stress the actual native transport/WebKit boundary with 500 MB of random data.

Uses a synthetic loopback WebSocket server, no agent/session or remote machine.
The slow page processes each frame synchronously. Reports native RSS growth;
WebKit's separate-process RSS is not included in the asserted limit.
"""

import base64
import hashlib
import http.server
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
SIZE = 500_000_000
case = Path(tempfile.mkdtemp(prefix="transport-pressure-"))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            body = b"""<meta name="duckterm-token" content="synthetic-test-token">
<script>window.framesSeen=0;window.addEventListener('remote-terminal',()=>{
 const end=performance.now()+1;while(performance.now()<end){};window.framesSeen++;
});window.pressureReady=true;</script>"""
            self.send_response(200)
            self.send_header("X-Duckterm", "1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/sessions/pressure/terminal":
            self.send_error(404)
            return
        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        # Same stress payload as head -c 500000000 /dev/urandom; bounded reads.
        with open("/dev/urandom", "rb") as source:
            remaining = SIZE
            try:
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    self.wfile.write(b"\x82\x7f" + struct.pack("!Q", len(chunk)) + chunk)
                    remaining -= len(chunk)
                self.wfile.flush()
                time.sleep(10)
            except (BrokenPipeError, ConnectionResetError):
                pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
source = r"""
import AppKit
import WebKit
MainActor.assumeIsolated {
 let app=NSApplication.shared
 app.setActivationPolicy(.accessory)
 let base=URL(string:"http://127.0.0.1:PORT")!
 let window=DashboardWindow(url:base);window.show()
 let transport=SessionTransport();let api=LaunchDestination()
 var received=0
 transport.onTerminalData = { event in
   await window.dispatchAndWait(name:"remote-terminal",detail:event)
   received += Data(base64Encoded:event["data"] as! String)!.count
 }
 Task { @MainActor in
  do {
   var ready=false
   for _ in 0..<200 {
    ready=await withCheckedContinuation { c in window.evaluate("window.pressureReady===true") { c.resume(returning:$0 as? Bool == true) } }
    if ready {break};try await Task.sleep(nanoseconds:100_000_000)
   }
   guard ready else {throw NSError(domain:"Page not ready",code:1)}
   print("BASELINE");fflush(stdout)
   try await Task.sleep(nanoseconds:500_000_000)
   _=try await transport.terminal(host:"synthetic",base:base,api:api,operation:"terminal-open",params:["id":UUID().uuidString,"key":"pressure"])
   for _ in 0..<2400 {
    if received>=500_000_000 {break};try await Task.sleep(nanoseconds:100_000_000)
   }
   guard received==500_000_000 else {throw NSError(domain:"Incomplete stream \(received)",code:2)}
   print("PASS bytes=\(received)");fflush(stdout);transport.close();app.terminate(nil)
  } catch {print("FAIL",error);fflush(stdout);exit(1)}
 }
 app.run()
}
""".replace(
    "PORT", str(server.server_port)
)
(case / "main.swift").write_text(source)
files = [str(p) for p in (ROOT / "mac/Sources/Duckterm").glob("*.swift") if p.name != "main.swift"]
subprocess.run(
    ["swiftc", *files, str(case / "main.swift"), "-o", str(case / "pressure")], check=True
)
log_path = case / "native.log"
with log_path.open("w") as log:
    proc = subprocess.Popen([str(case / "pressure")], stdout=log, stderr=log, env=os.environ)
    baseline = None
    peak = 0
    deadline = time.monotonic() + 260
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            output = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(proc.pid)], capture_output=True, text=True
            ).stdout.strip()
            if output and "BASELINE" in log_path.read_text():
                rss = int(output) * 1024
                baseline = rss if baseline is None else baseline
                peak = max(peak, rss)
                if peak - baseline > 256 * 1024 * 1024:
                    raise RuntimeError("Native RSS grew beyond 256 MiB")
            time.sleep(0.1)
        if proc.poll() is None:
            raise RuntimeError("Stress check timed out")
        if proc.returncode:
            raise RuntimeError(log_path.read_text())
        assert baseline is not None and "PASS bytes=500000000" in log_path.read_text()
        print(log_path.read_text())
        print(f"PASS native RSS baseline={baseline} peak={peak} growth={peak - baseline}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        server.shutdown()
        print("Evidence", case)
