"""Native acceptance against an explicitly selected isolated remote QA service.

Build web/dist first. This compiles the actual AppDelegate and DashboardWindow,
uses a separate bundle ID/local database/tmux socket, and cleans up test agents.
Example: python3 scripts/test_unified_sessions_native.py --remote-host duckterm-dev
"""

import argparse, json, os, plistlib, re, socket, subprocess, tempfile, time, urllib.request, uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--remote-host", required=True)
parser.add_argument("--remote-port", type=int, default=4341)
args = parser.parse_args()
if (
    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", args.remote_host)
    or not 1 <= args.remote_port <= 65535
):
    parser.error("Expected a configured SSH host and a valid isolated QA port")
run = uuid.uuid4().hex[:10]
case = Path(tempfile.mkdtemp(prefix="unified-native-"))
app = case / "DuckTerm Unified QA.app"
contents = app / "Contents"
(contents / "MacOS").mkdir(parents=True)
state = case / "state"
project = case / "project"
project.mkdir()
state.mkdir()
with socket.socket() as p:
    p.bind(("127.0.0.1", 0))
    port = p.getsockname()[1]
remote_name = "unified-qa-" + run + "-remote"
env = dict(
    os.environ,
    DUCKTERM_HOME=str(state),
    DUCKTERM_NO_BROWSER="1",
    DUCKTERM_SUMMARIZER="off",
    DUCKTERM_TMUX_SOCKET="unified-qa-" + run,
    PYTHONPATH=str(root / "src"),
    CLAUDE_CONFIG_DIR=str(case / "claude"),
    CODEX_HOME=str(case / "codex"),
)
info = {
    "CFBundleIdentifier": "com.rubberduckhq.unifiedqa." + run,
    "CFBundleName": "DuckTerm Unified QA",
    "CFBundleDisplayName": "DuckTerm Unified QA",
    "CFBundleExecutable": "DuckTerm",
    "CFBundlePackageType": "APPL",
    "DucktermTestBuild": True,
    "DucktermTestPort": port,
}
(contents / "Info.plist").write_bytes(plistlib.dumps(info))
js = (
    (root / "mac/Tests/unified-sessions.js")
    .read_text()
    .replace("LOCAL_KEY", "unified-local-" + run)
    .replace("REMOTE_NAME", remote_name)
    .replace("PROJECT", str(project))
    .replace("duckterm-dev", args.remote_host)
)
(case / "probe.js").write_text(js)
source = (root / "mac/Sources/Duckterm/main.swift").read_text()
source = source[: source.index("MainActor.assumeIsolated {")]
# On the second process start, withhold only this test app's SSH tunnels until
# the local feed has loaded. The real remote service and agents keep running.
source = source.replace(
    "connection.start()",
    'if ProcessInfo.processInfo.environment["QA_RESTORE"] != "1" { connection.start() }',
)
source += (
    """
import WebKit
@MainActor func probeEval(_ web: WKWebView, _ script: String) async throws -> Any? {
 try await withCheckedThrowingContinuation { continuation in
  web.evaluateJavaScript(script) { result,error in
   if let error { continuation.resume(throwing:error) } else { continuation.resume(returning:result) }
  }
 }
}
MainActor.assumeIsolated {
 let app=NSApplication.shared
 RemoteHost.save([try! RemoteHost(name:"duckterm-dev",target:"duckterm-dev",remotePort:4341)])
 let delegate=AppDelegate();app.delegate=delegate;app.setActivationPolicy(.regular);app.mainMenu=buildMainMenu()
 Task { @MainActor in
  do {
   var web:WKWebView?;var hostWindow:NSWindow?
   for _ in 0..<200 {
    hostWindow=app.windows.first { $0.contentView is WKWebView };web=hostWindow?.contentView as? WKWebView
    if let web, (try? await probeEval(web,"!!document.querySelector('[aria-controls=header-new-panel]')")) as? Bool == true {break}
    try await Task.sleep(nanoseconds:100_000_000)
   }
   guard let web,let hostWindow else {throw NSError(domain:"Missing dashboard",code:1)}
   if ProcessInfo.processInfo.environment["QA_RESTORE"] == "1" {
    var localReady=false
    for _ in 0..<200 {
     if (try? await probeEval(web,"[...document.querySelectorAll('.rd-row-name')].some(e=>e.textContent==='Local original')")) as? Bool == true {localReady=true;break}
     try await Task.sleep(nanoseconds:100_000_000)
    }
    guard localReady else {throw NSError(domain:"Local rows missing during offline startup",code:10)}
    try await Task.sleep(nanoseconds:2_000_000_000)
    let held=try await probeEval(web,"(() => { const s=JSON.parse(localStorage.getItem('rd.selectedSession')); return s?.host==='duckterm-dev' && !document.querySelector('.rd-row.selected'); })()") as? Bool == true
    guard held else {throw NSError(domain:"Offline remote selection was overwritten",code:11)}
    let connections=Mirror(reflecting:delegate).children.first {$0.label=="launchConnections"}!.value as! [String:RemoteConnection]
    connections.values.forEach {$0.start()}
    var selected=false
    for _ in 0..<600 {
     if (try? await probeEval(web,"document.querySelector('.rd-row.selected .rd-row-name')?.textContent==='REMOTE_NAME'")) as? Bool == true {selected=true;break}
     try await Task.sleep(nanoseconds:100_000_000)
    }
    guard selected else {throw NSError(domain:"Remote selection lost after app relaunch",code:12)}
    print("PASS actual app relaunch: offline selection retained, then remote selected after reconnect");fflush(stdout)
    app.terminate(nil);return
   }
   hostWindow.setContentSize(NSSize(width:1400,height:850))
   _=try await probeEval(web,try String(contentsOfFile:"SCRIPT") + ";undefined")
   var previous="";var success=false;var disconnected=false;var reconnected=false
   for _ in 0..<1500 {
    let result=try await probeEval(web,"window.qaResult || {}") as? [String:Any] ?? [:]
    let stage=result["stage"] as? String ?? ""
    if stage != previous {print("STAGE",stage);fflush(stdout);previous=stage}
    if stage=="failed" {print(result);throw NSError(domain:"UI checks failed",code:2)}
    if stage=="ready-for-outage" && !disconnected {
      disconnected=true
      let connections=Mirror(reflecting:delegate).children.first {$0.label=="launchConnections"}!.value as! [String:RemoteConnection]
      connections.values.forEach {$0.stop()}
      _=try await probeEval(web,"window.__qaDisconnected=true")
    }
    if stage=="ready-for-reconnect" && !reconnected {
      reconnected=true
      let connections=Mirror(reflecting:delegate).children.first {$0.label=="launchConnections"}!.value as! [String:RemoteConnection]
      connections.values.forEach {$0.start()}
      _=try await probeEval(web,"window.__qaReconnected=true")
    }
    if stage=="passed" {
      guard hostWindow.contentView === web,app.windows.filter({$0.contentView is WKWebView}).count==1 else {throw NSError(domain:"Dashboard/window replaced",code:3)}
      let image:NSImage=try await withCheckedThrowingContinuation { c in web.takeSnapshot(with:nil) {image,error in if let image {c.resume(returning:image)} else {c.resume(throwing:error!)} } }
      let png=NSBitmapImageRep(data:image.tiffRepresentation!)!.representation(using:.png,properties:[:])!
      try png.write(to:URL(fileURLWithPath:"SCREENSHOT"))
      // Reload the same local page: remote presentation grouping must survive.
      web.reload()
      var restored=false
      for _ in 0..<300 {
        let check="(() => { const g=[...document.querySelectorAll('.rd-group-head')].find(e=>e.textContent.includes('Unified QA')); if(g?.querySelector('.rd-group-caret')?.textContent==='▸')g.click(); return document.querySelector('.rd-row.selected .rd-row-name')?.textContent==='REMOTE_NAME'; })()"
        if (try? await probeEval(web,check)) as? Bool == true {restored=true;break}
        try await Task.sleep(nanoseconds:100_000_000)
      }
      guard restored,hostWindow.contentView === web else {throw NSError(domain:"Mixed grouping did not survive reload",code:5)}
      print("PASS",result,"grouping and selection persisted across reload");success=true;break
    }
    try await Task.sleep(nanoseconds:100_000_000)
   }
   if !success {throw NSError(domain:"QA timed out",code:4)}
   app.terminate(nil)
  } catch {print("FAIL",error);fflush(stdout);exit(1)}
 }
 app.run()
}
""".replace(
        "SCRIPT", str(case / "probe.js")
    )
    .replace("SCREENSHOT", "/tmp/duckterm-unified-native.png")
    .replace("duckterm-dev", args.remote_host)
    .replace("remotePort:4341", "remotePort:" + str(args.remote_port))
    .replace("REMOTE_NAME", remote_name)
)
(case / "main.swift").write_text(source)
files = [str(p) for p in (root / "mac/Sources/Duckterm").glob("*.swift") if p.name != "main.swift"]
subprocess.run(
    [
        "swiftc",
        "-framework",
        "AppKit",
        "-framework",
        "WebKit",
        "-framework",
        "UserNotifications",
        *files,
        str(case / "main.swift"),
        "-o",
        str(contents / "MacOS/DuckTerm"),
    ],
    check=True,
)
subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
log = (case / "server.log").open("w")
server = subprocess.Popen(
    [str(root / ".venv/bin/python"), "-m", "duckterm.cli", "serve", "--port", str(port)],
    env=env,
    stdout=log,
    stderr=log,
)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
            break
        except OSError:
            time.sleep(0.1)
    result = subprocess.run([str(contents / "MacOS/DuckTerm")], env=env, timeout=200)
    if result.returncode:
        print("SERVER LOG", (case / "server.log").read_text()[-5000:])
    result.check_returncode()
    subprocess.run(
        [str(contents / "MacOS/DuckTerm")], env={**env, "QA_RESTORE": "1"}, timeout=100, check=True
    )
finally:
    cleanup = """import json,re,urllib.request
base='http://127.0.0.1:REMOTE_PORT'
token=re.search(r'<meta name="duckterm-token" content="([A-Za-z0-9_-]+)">',urllib.request.urlopen(base).read().decode()).group(1)
rows=json.load(urllib.request.urlopen(base+'/sessions'))['sessions']
for row in rows:
 if row.get('name')==NAME:
  req=urllib.request.Request(base+'/sessions/'+row['session_key'],data=b'{"force":true}',method='DELETE',headers={'X-Duckterm-Token':token,'Content-Type':'application/json'})
  urllib.request.urlopen(req).close()
  print('Removed own remote QA fixture')
""".replace(
        "NAME", repr(remote_name)
    ).replace(
        "REMOTE_PORT", str(args.remote_port)
    )
    subprocess.run(
        ["ssh", args.remote_host, "python3 -"], input=cleanup, text=True, timeout=40, check=False
    )
    server.terminate()
    server.wait(timeout=20)
    log.close()
    subprocess.run(["tmux", "-L", "unified-qa-" + run, "kill-server"], capture_output=True)
    print("QA work directory", case)
