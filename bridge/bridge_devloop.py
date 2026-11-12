"""Build / deploy / self-test loop for the ClaudeBridge Sandbox plugin.

Runs OUTSIDE Sandbox, on the real Python install. That matters for two
independent reasons:

  * Sandbox.exe runs inside a Windows Job object, so any process it spawns is
    killed the moment Sandbox exits. A deploy that has to outlive the editor
    therefore cannot be a child of it -- which is why `cycle` is meant to be
    started by Task Scheduler (see --register), not by Sandbox's own Python.
  * The self-test talks to the plugin over TCP; driven from inside Sandbox it
    would deadlock, because the interpreter occupies the very main thread that
    services the queue.

Subcommands:
    build     compile the plugin (Sandbox may stay open -- the build writes to
              the source tree, not the installed engine)
    deploy    ask the scheduled task to swap the DLL, then close Sandbox so it
              can; the task relaunches it
    selftest  run bridge_selftest.py against the running editor
    cycle     build, deploy, wait for the bridge to come back, then selftest

    --register  create/refresh the Task Scheduler entries this relies on
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import argparse
import json
import os
import socket
import subprocess
import sys
import time

# Maintainer-only tool. Override the machine-specific locations with environment variables:
#   CRYENGINE_ENGINE_DIR  installed engine folder (the one holding bin\win_x64\EditorPlugins)
#   CRYENGINE_SOURCE      CRYENGINE source tree the plugin is built in (CRYENGINE_SOURCE_DIR is read too); build / deploy / cycle need it
#   CRYENGINE_WORKSPACE   folder for the build log, the devloop log and the deploy / rollback batch files (state/ by default)
#   CRYENGINE_MSBUILD     full path of MSBuild.exe
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
SRC = _paths.ENGINE_SOURCE                       # "" when CRYENGINE_SOURCE is not set; build / deploy / cycle then stop with a message
PROJECT = __import__("project_root").PROJECT

MSBUILD = os.environ.get("CRYENGINE_MSBUILD") or r"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe"
VCXPROJ = os.path.join(SRC, r"solutions_cmake\win64\Code\Sandbox\Plugins\ClaudeBridgePlugin\ClaudeBridgePlugin.vcxproj")
BUILT_DLL = os.path.join(SRC, r"bin\win_x64\EditorPlugins\ClaudeBridgePlugin.dll")
LIVE_DLL = os.path.join(ENGINE, r"bin\win_x64\EditorPlugins\ClaudeBridgePlugin.dll")
BUILD_LOG = _paths.workspace("claude_build.log")
DEPLOY_TASK = "ClaudeBridgeDeploy"
ROLLBACK_TASK = "ClaudeBridgeRollback"
CYCLE_TASK = "ClaudeBridgeCycle"
SELFTEST = _paths.script("bridge_selftest.py")

HOST, PORT = "127.0.0.1", 37778


def bridge(cmd, timeout=30.0):
    payload = json.dumps({"id": 1, "cmd": cmd}) + "\n"
    with socket.create_connection((HOST, PORT), timeout) as s:
        s.settimeout(timeout)
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def bridge_alive(timeout=3.0):
    try:
        return bool(bridge("bridge.window.info", timeout).get("ok"))
    except Exception:
        return False


def wait_for_bridge(seconds=180):
    """Sandbox takes a while to come back, and the plugin's listener starts
    late in startup, so absence early on means nothing."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if bridge_alive():
            return True
        time.sleep(3)
    return False


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_build(_args):
    t0 = time.time()
    # BuildProjectReferences=false: the vcxproj references CryQt, EditorCommon, MFCToolsPlugin and Sandbox,
    # and without this MSBuild rebuilds those editor binaries too (into the source tree) -- an engine build
    # nobody asked for. The plugin links the import libraries that are already there.
    with open(BUILD_LOG, "wb") as log:
        rc = subprocess.call(
            [MSBUILD, VCXPROJ, "/t:Build", "/p:Configuration=Profile",
             "/p:Platform=x64", "/p:BuildProjectReferences=false", "/m", "/nologo", "/v:minimal"],
            stdout=log, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0

    text = open(BUILD_LOG, "r", errors="replace").read()
    # Only diagnostics from the plugin itself matter; the CryEngine
    # dependencies emit a steady stream of their own warnings.
    diags = [l.strip() for l in text.splitlines()
             if "ClaudeBridgePlugin" in l and ("error" in l.lower() or "warning" in l.lower())]

    print("build rc=%d in %.0fs" % (rc, elapsed))
    for d in diags[:20]:
        print("   ", d[:220])
    if rc == 0 and os.path.isfile(BUILT_DLL):
        print("built dll: %d bytes, %ds old" % (os.path.getsize(BUILT_DLL),
                                                int(time.time() - os.path.getmtime(BUILT_DLL))))
    return rc


def sandbox_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"],
                         capture_output=True, text=True).stdout
    return "Sandbox.exe" in out


def answer_dialog():
    """Close-time prompts ("save changes?") are answered by name through the plugin's dialog verbs, which
    need no focus. The default button is what Return used to press. A plugin without the dialog verbs
    falls back to Return, which only reaches the dialog if Sandbox is in front."""
    try:
        r = bridge("bridge.dialog.list", 15)
        print("   dialogs: %s" % str(r.get("output", r))[:500])
        if r.get("ok") and '"count": 0' in r.get("output", ""):
            return
        r = bridge("bridge.dialog.click default", 15)
        print("   dialog.click default: %s" % str(r.get("output", r.get("error", r)))[:300])
        if r.get("ok"):
            return
    except Exception as exc:
        print("   dialog verbs unavailable: %s" % exc)
    try:
        print("   key return: %s" % str(bridge("bridge.input.key return"))[:300])
    except Exception:
        pass


def wait_for_exit(seconds=120):
    """Closing a level with unsaved edits raises a modal "save changes?"
    prompt, and the editor then sits there forever waiting for a human. Saving
    first should prevent it; answering the prompt is the backstop, because an
    unattended run has nobody to click Yes."""
    deadline = time.time() + seconds
    nudged_at = []
    while time.time() < deadline:
        if not sandbox_running():
            return True
        waited = seconds - (deadline - time.time())
        for mark in (8, 20, 40):
            if waited > mark and mark not in nudged_at:
                nudged_at.append(mark)
                print("still running after %ds -- a dialog is probably up" % mark)
                answer_dialog()
        time.sleep(2)
    return False


def close_sandbox():
    """WM_CLOSE posted to the main window: the same path as the title bar's X, with no focus needed. The
    old way, a synthetic Alt+F4, went to whatever window was in front -- a console, or the Firewall
    prompt. Only a plugin without bridge.window.close needs Alt+F4, and only if
    Sandbox really is in front."""
    r = bridge("bridge.window.close")
    if r.get("ok"):
        print("close posted")
        return
    print("bridge.window.close failed (%s)" % str(r.get("error", r))[:200])
    info = bridge("bridge.window.info")
    if '"foreground": true' in info.get("output", ""):
        bridge("bridge.input.key f4 alt")
        print("sent Alt+F4 to the foreground Sandbox")
    else:
        print("Sandbox is not in front -- NOT sending Alt+F4 to someone else's window")


def cmd_deploy(args):
    """The DLL is locked while Sandbox runs, so the swap has to happen after it
    exits -- and by something Sandbox is not the parent of."""
    if not os.path.isfile(BUILT_DLL):
        print("no built DLL to deploy")
        return 1

    built = sha256(BUILT_DLL)

    if bridge_alive():
        # Save BEFORE asking it to close: an unsaved level turns Alt+F4 into a
        # modal prompt, and the deploy then waits on a click that never comes.
        try:
            r = bridge("bridge.execute_python general.save_level()", timeout=120)
            print("pre-close save: %s" % str(r.get("output", r))[:160])
        except Exception as exc:
            print("could not save before closing: %s" % exc)

    rc = subprocess.call(["schtasks", "/Run", "/TN", DEPLOY_TASK],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        print("could not start %s (rc=%d) -- run register first" % (DEPLOY_TASK, rc))
        return rc

    if bridge_alive():
        try:
            close_sandbox()
        except Exception as exc:
            print("could not ask Sandbox to close: %s" % exc)
    else:
        print("bridge not answering; assuming Sandbox is already closed")

    if not wait_for_exit(120):
        print("Sandbox never exited -- deploy aborted, nothing was swapped")
        return 1

    if not wait_for_bridge(240):
        print("Sandbox did not come back within 240s")
        return 1

    # By hash: the old size check said MATCH for a copy that had failed, whenever the sizes happened to agree.
    live = sha256(LIVE_DLL)
    ok = (built == live)
    print("deployed: built=%s live=%s %s" % (built[:16], live[:16], "MATCH" if ok else "MISMATCH"))
    if os.path.isfile(LIVE_DLL + ".prev"):
        print("previous plugin kept as %s.prev (restore with bridge_devloop.py rollback)" % LIVE_DLL)

    # A fresh Sandbox opens with no level, and every terrain/object/vegetation
    # check needs one.
    level = getattr(args, "level", None)
    if level:
        try:
            r = bridge("bridge.execute_command asset.edit levels/%s/%s.level" % (level, level), timeout=180)
            print("level '%s' load requested: ok=%s" % (level, r.get("ok")))
        except Exception as exc:
            print("could not load level '%s': %s" % (level, exc))

    return 0 if ok else 1


def cmd_rollback(_args):
    """Put back ClaudeBridgePlugin.dll.prev -- for a plugin that does not load, when there is no bridge left to
    close the editor with. The task kills Sandbox, swaps the DLL and relaunches it."""
    if not os.path.isfile(LIVE_DLL + ".prev"):
        print("no %s.prev to roll back to" % LIVE_DLL)
        return 1
    rc = subprocess.call(["schtasks", "/Run", "/TN", ROLLBACK_TASK], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        print("could not start %s (rc=%d) -- run register first" % (ROLLBACK_TASK, rc))
        return rc
    time.sleep(10)
    if not wait_for_bridge(240):
        print("Sandbox did not come back within 240s")
        return 1
    ok = sha256(LIVE_DLL) == sha256(LIVE_DLL + ".prev")
    print("rolled back: live %s .prev" % ("==" if ok else "!="))
    return 0 if ok else 1


def cmd_selftest(args):
    cmd = [sys.executable, SELFTEST]
    if args.destructive:
        cmd.append("--destructive")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    return proc.returncode


def cmd_cycle(args):
    rc = cmd_build(args)
    if rc != 0:
        print("build failed; not deploying")
        return rc
    rc = cmd_deploy(args)
    if rc != 0:
        print("deploy failed; not testing")
        return rc
    # The editor answers before it has finished settling; a couple of seconds
    # here avoids a first check that fails for no interesting reason.
    time.sleep(5)
    return cmd_selftest(args)


def register(_args):
    """One-time setup. The cycle task exists so the loop can outlive the
    editor it restarts."""
    here = os.path.abspath(__file__)
    tasks = [
        (DEPLOY_TASK, "cmd /c " + _paths.workspace("claude_deploy.bat")),
        (ROLLBACK_TASK, "cmd /c " + _paths.workspace("claude_rollback.bat")),
        (CYCLE_TASK, '"%s" "%s" cycle' % (sys.executable, here)),
    ]
    for name, action in tasks:
        rc = subprocess.call(["schtasks", "/Create", "/TN", name, "/TR", action,
                              "/SC", "ONCE", "/ST", "23:59", "/F"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("%s: %s" % (name, "registered" if rc == 0 else "FAILED rc=%d" % rc))
    return 0


class _Tee:
    """Task Scheduler throws away a task's console output, and `cycle` is meant
    to be run as a task -- so mirror everything to a file that survives it."""

    def __init__(self, stream, path):
        self.stream = stream
        try:
            self.fh = open(path, "a", encoding="utf-8")
        except Exception:
            self.fh = None

    def write(self, s):
        self.stream.write(s)
        if self.fh:
            self.fh.write(s)
            self.fh.flush()

    def flush(self):
        self.stream.flush()
        if self.fh:
            self.fh.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["build", "deploy", "selftest", "cycle", "register", "rollback"])
    ap.add_argument("--destructive", action="store_true")
    ap.add_argument("--level", default=None,
                    help="level to reopen after a deploy (a fresh Sandbox starts with none); omit to not reload")
    args = ap.parse_args()

    if args.action in ("build", "deploy", "cycle") and not SRC:
        print("CRYENGINE_SOURCE is not set: %s needs the CRYENGINE source tree the ClaudeBridge plugin is built in. "
              "Set the environment variable CRYENGINE_SOURCE to that folder." % args.action)
        return 2
    log = _paths.workspace("claude_devloop.log")
    try:
        os.makedirs(_paths.WORKSPACE, exist_ok=True)
        open(log, "w", encoding="utf-8").write(
            "=== %s %s ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), args.action))
    except Exception:
        pass
    sys.stdout = _Tee(sys.stdout, log)

    rc = {"build": cmd_build, "deploy": cmd_deploy, "selftest": cmd_selftest,
          "cycle": cmd_cycle, "register": register, "rollback": cmd_rollback}[args.action](args)
    print("exit rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
