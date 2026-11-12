"""Keep Sandbox alive, from outside Sandbox.

The bridge is the only way to run anything in the editor, and it lives inside
Sandbox.exe. So when Sandbox crashes or wedges, there is no way left to notice
or to recover -- the editor sits dead and the next command just times out. This
runs from Task Scheduler instead, which owes nothing to the editor.

Two failure shapes, both handled:

  * Sandbox is not running at all -- crashed, or closed by a deploy that never
    got as far as relaunching it.
  * Sandbox is running but not answering. A hung UI thread is worse than a
    crash: even the out-of-band verbs stop responding, because GetWindowRect on
    a window whose thread is hung blocks the caller too.

An unhealthy-but-running editor is given two consecutive strikes before being
killed, so a long shader compile or level load is not mistaken for a hang.

Deliberate closes. safe_close.close_editor writes a marker, <workspace>/claude_editor_closed (the workspace folder is state/ by default), when an editor is closed ON PURPOSE (a
package, a test run, the user): while it is fresh this stands down instead of reopening the editor mid-job, and it is removed as soon as an editor runs again (or by the job that wrote it
when it ends, or void two minutes after the script that wrote it is gone). A PAUSE file, <workspace>/claude_watchdog_pause (empty = until deleted; or an epoch time; or
{"until": epoch, "why": text}), stops it entirely: it neither starts nor kills anything. It also reopens the level that was open (remembered at every healthy check and at a deliberate close),
not always ashvale. Nothing here saves a hung editor: a kill of an editor that stopped answering without a crash report still happens after two strikes, and the last healthy
check's level and object count are in the state file to show what was lost.

Install:  python bridge/bridge_watchdog.py --install      (every 5 minutes)
Remove:   python bridge/bridge_watchdog.py --uninstall
Once:     python bridge/bridge_watchdog.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safe_close  # noqa: E402  (the marker, the pause file and the last-level file are defined THERE, one place for the writer and the reader)

# CRYENGINE_ENGINE: the engine folder (the one holding bin/win_x64/Sandbox.exe); the default is the Launcher 5.7.1 LTS install
ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
SANDBOX = os.path.join(ENGINE, r"bin\win_x64\Sandbox.exe")
PROJECT = __import__("project_root").PROJECT
# The lock / state / log / marker files live in ONE folder, the one safe_close.py names (safe_close.SRC), so the writer and this reader always agree.
SRC = os.path.normpath(getattr(safe_close, "SRC", _paths.WORKSPACE))
try:
    os.makedirs(SRC, exist_ok=True)          # an absent folder would make every check stand down (the run lock could not be taken)
except OSError:
    pass

STATE = os.path.join(SRC, "claude_watchdog_state.json")
# A deploy deliberately closes Sandbox for a minute or so. Without this the
# watchdog would see a dead editor, relaunch it, and the deploy would then find
# the DLL locked -- the two would fight every cycle.
DEPLOY_LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or os.path.join(SRC, "claude_deploy_in_progress")
DEPLOY_LOCK_MAX_AGE = 300.0
LOG = os.path.join(SRC, "claude_watchdog.log")
PROJECTS_JSON = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Crytek", "CRYENGINE", "projects.json")
PROJECTS_SNAPSHOT = os.path.join(SRC, "claude_projects_startup.json")

# A relaunched Sandbox comes up with NO level loaded. Recovering the process is
# only half of recovery: an editor sitting at "Untitled" answers every command
# cheerfully and reports zero terrain, which is the most misleading state of
# all. One project lives on this machine, so the level is a constant.
# CRYENGINE_FALLBACK_LEVEL: the level asset to reopen when no level is remembered, e.g. levels/mylevel/mylevel.level (empty = reopen nothing)
LEVEL_ASSET = os.environ.get("CRYENGINE_FALLBACK_LEVEL", "")
LEVEL_WAIT = 240.0
MARKER = safe_close.MARKER
PAUSE_FILE = safe_close.PAUSE_FILE
LEVEL_FILE = safe_close.LEVEL_FILE

# Time-of-day and fog live in the shading PRESET, not the level, and the editor
# rewrites the preset from its own defaults on startup -- so a restart silently
# reverts them (for example the fog density comes back as the default instead of the
# value that was set). Reopening the level is therefore still not the whole state; these are.
# CRYENGINE_ENV_AFTER_RELOAD: bridge commands to re-apply after a reopen, separated by ';' (e.g. "bridge.env.time 15.0"); empty by default.
ENV_AFTER_RELOAD = [c.strip() for c in os.environ.get("CRYENGINE_ENV_AFTER_RELOAD", "").split(";") if c.strip()]
_ENV_AFTER_RELOAD_NOTE = [
    # The fog values that used to be forced here with bridge.env.set -- which writes ONE value into every key
    # of the day curve -- now live keyed in the level's own preset (libs/environmentpresets/ashvale_clear.env).
    # Re-flattening them after every restart kept the moon's radial glow on all night.
]

TASK = "ClaudeBridgeWatchdog"
HOST, PORT = "127.0.0.1", 37778
PROBE_TIMEOUT = 20.0
STRIKES_BEFORE_KILL = 2


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sandbox_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"],
                         capture_output=True, text=True).stdout
    return "Sandbox.exe" in out


def bridge_healthy():
    """A queued command, on purpose.

    An out-of-band verb would answer from the TCP thread even while the editor
    is wedged, which is precisely the state worth catching. cvar_get has to
    reach the main thread, so it only succeeds if the editor is genuinely alive."""
    try:
        payload = json.dumps({"id": 1, "cmd": "bridge.cvar_get r_DisplayInfo"}) + "\n"
        with socket.create_connection((HOST, PORT), PROBE_TIMEOUT) as s:
            s.settimeout(PROBE_TIMEOUT)
            s.sendall(payload.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
        return bool(resp.get("ok"))
    except Exception:
        return False


ERROR_LOG = os.path.join(PROJECT, "error.log")


def crash_report_after(started, error_log=None):
    """The time error.log was written when that is AFTER the running Sandbox started (the engine's crash handler writes it at the moment of a crash, and the process then sits hung in the crash handler), else None.
    started: epoch seconds the running process started. An error.log from an earlier run is older than that and says nothing about this one."""
    path = error_log or ERROR_LOG
    try:
        written = os.path.getmtime(path)
    except OSError:
        return None
    return written if started is not None and written > started + 5 else None


def sandbox_started():
    """epoch seconds the running Sandbox.exe was started, or None (PowerShell: tasklist does not say)"""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "$p = Get-Process Sandbox -ErrorAction SilentlyContinue | Select-Object -First 1; if ($p) { [DateTimeOffset]::new($p.StartTime).ToUnixTimeSeconds() }"],
                             capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"strikes": 0, "restarts": 0}


def write_state(st):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def restore_startup_project():
    """Without this a relaunch stops at the Project Browser and waits for a
    human, which defeats the entire point of a watchdog."""
    try:
        # project_root keeps a one-time copy (projects.json.mcp-backup) of the Launcher list before it replaces it
        __import__("project_root").write_projects_json(PROJECTS_JSON)         # the ACTIVE project (project_root.py), so a relaunch opens the same project the tools work on
    except Exception as exc:
        log("could not write the startup project: %s" % exc)
        if os.path.isfile(PROJECTS_SNAPSHOT):
            try:
                open(PROJECTS_JSON, "wb").write(open(PROJECTS_SNAPSHOT, "rb").read())
            except Exception as exc2:
                log("could not restore startup project: %s" % exc2)


def send(cmd, timeout=PROBE_TIMEOUT):
    """One bridge command. Returns the raw response text, or "" on any failure."""
    try:
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
        return buf.split(b"\n", 1)[0].decode("utf-8", "replace")
    except Exception:
        return ""


def deliberately_closed():
    """the marker's dict while an editor closed on purpose should stay closed, else None"""
    return safe_close.marker_state(MARKER)


def paused():
    """a text while the pause file is in force, else None"""
    return safe_close.pause_state(PAUSE_FILE)


def clear_marker():
    """an editor is running again: the deliberate close is over"""
    try:
        os.remove(MARKER)
        return True
    except OSError:
        return False


def level_asset(name, project=PROJECT):
    """'levels/<name>/<name>.level' when that level exists in the project, else None"""
    if name and all(ch.isalnum() or ch in "_-" for ch in name) and os.path.isdir(os.path.join(project, "Assets", "levels", name)):
        return "levels/%s/%s.level" % (name, name)
    return None


def current_level_name():
    """the level the running editor holds (and its object count), or (None, None) when it cannot be asked"""
    src = "print(general.get_current_level_name())\nprint(general.get_objects_count())"
    try:
        resp = json.loads(send("bridge.execute_python.b64 " + base64.b64encode(src.encode()).decode("ascii"), 20.0) or "{}")
        out = resp.get("output", "")
        try:
            out = json.loads(out).get("log", "")
        except ValueError:
            pass
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return (lines[0] if lines else None), (int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else None)
    except Exception:
        return None, None


def remember_level(st):
    """at a healthy check: which level is open and how many objects it holds, into the state (a crash later reopens THAT level)"""
    name, objects = current_level_name()
    if name and level_asset(name):
        st["level"], st["objects"], st["level_seen"] = name, objects, time.strftime("%Y-%m-%d %H:%M:%S")


def level_to_reopen(st=None, marker=None):
    """the asset path of the level to put back: the one named at a deliberate close, else the last one a healthy check saw, else the fallback"""
    for name in ((marker or {}).get("level"), safe_close.last_level(LEVEL_FILE), (st or read_state()).get("level")):
        asset = level_asset(name)
        if asset:
            return asset
    return LEVEL_ASSET


def reopen_level(deadline=LEVEL_WAIT, asset=None):
    """Wait for the relaunched editor, then put the level back.

    Without this the watchdog restores the process and leaves the working state
    behind -- which reads as success from the outside and is not."""
    asset = asset or level_to_reopen()
    if not asset:
        log("no level remembered and CRYENGINE_FALLBACK_LEVEL is not set -- the editor is left without a level")
        return False
    started = time.time()
    up = False
    while time.time() - started < deadline:
        if '"ok": true' in send("bridge.cvar_get r_DisplayInfo", 10.0).replace('"ok":true', '"ok": true'):
            up = True
            break
        time.sleep(5)
    if not up:
        log("bridge never answered within %d s -- level not reopened" % int(deadline))
        return False
    resp = send("bridge.execute_command asset.edit " + asset, 180.0)
    ok = '"ok": true' in resp.replace('"ok":true', '"ok": true')
    log("reopened %s: %s" % (asset, "ok" if ok else "FAILED (%s)" % resp[:200]))
    if not ok:
        return False
    time.sleep(8)
    applied = 0
    for cmd in ENV_AFTER_RELOAD:
        if '"ok": true' in send(cmd, 30.0).replace('"ok":true', '"ok": true'):
            applied += 1
    log("re-applied %d of %d environment settings" % (applied, len(ENV_AFTER_RELOAD)))
    return True


def launch():
    restore_startup_project()
    asset = level_to_reopen()                     # read BEFORE the marker goes: the level file it names is the level that was open at a deliberate close
    clear_marker()                                # an editor is being started: the deliberate close is over
    if not os.path.isfile(SANDBOX):
        log("cannot launch Sandbox: %s does not exist (set CRYENGINE_ENGINE to the engine folder)" % SANDBOX)
        return
    proc = subprocess.Popen([SANDBOX], cwd=PROJECT,
                            creationflags=0x8 | 0x200)   # DETACHED | NEW_PROCESS_GROUP
    log("launched Sandbox (pid %d)" % proc.pid)
    try:
        st = read_state()
        st["sandbox_pid"] = proc.pid
        write_state(st)
    except Exception:
        pass
    reopen_level(asset=asset)


def kill():
    # NOTE: this ends an editor that stopped answering WITHOUT saving; unsaved work in it is lost. It kills the editor this watchdog launched (its pid, from the state file)
    # when that one is still running, else every Sandbox.exe (an editor started by hand has no recorded pid).
    pid = read_state().get("sandbox_pid")
    cmd = ["taskkill", "/F", "/IM", "Sandbox.exe"]
    if isinstance(pid, int) and pid > 0:
        alive = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True).stdout
        if "Sandbox.exe" in alive:
            cmd = ["taskkill", "/F", "/PID", str(pid)]
    subprocess.run(cmd, capture_output=True, text=True)
    time.sleep(4)
    log("killed hung Sandbox")


def deploy_in_progress():
    """Stale locks are ignored: a deploy that died mid-way must not disable the
    watchdog forever."""
    if not os.path.isfile(DEPLOY_LOCK):
        return False
    age = time.time() - os.path.getmtime(DEPLOY_LOCK)
    if age > DEPLOY_LOCK_MAX_AGE:
        log("ignoring deploy lock -- %d s old, treating it as stale" % int(age))
        return False
    return True


RUN_LOCK = os.path.join(SRC, "claude_watchdog_running")
RUN_LOCK_MAX_AGE = 600.0


def _take_lock():
    """One recovery at a time: the scheduled check and a tool that asks for the editor must never both launch Sandbox. A lock older than RUN_LOCK_MAX_AGE belongs to a run that died."""
    try:
        if os.path.exists(RUN_LOCK) and time.time() - os.path.getmtime(RUN_LOCK) > RUN_LOCK_MAX_AGE:
            os.remove(RUN_LOCK)
        fd = os.open(RUN_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def _drop_lock():
    try:
        os.remove(RUN_LOCK)
    except OSError:
        pass


def editor_closing_job_running():
    """True while a job that deliberately closes the editor (package, shoot, tests ...) is running: the editor must stay closed for it"""
    folder = os.path.join(_paths.ROOT, "job_runs")
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return False
    for n in sorted(names)[-8:]:
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("state") == "running" and (rec.get("closes_editor") or rec.get("launches_game")):
            return True
    return False


def level_loaded():
    """True when the editor has a level (the bridge's terrain info reports a height; an editor with no level reports zeros)"""
    try:
        info = json.loads(json.loads(send("bridge.terrain.info", 15.0)).get("output", "{}"))
        return float(info.get("heightmap", {}).get("elevationMax", 0)) > 0
    except Exception:
        return False


def _trigger_watchdog_task():
    """Run the scheduled watchdog check NOW. Sandbox has to be started from the Task Scheduler's own context: a Sandbox started by a tool's process (a Popen from the MCP server or a test) dies when that
    process tree is torn down - an editor started by a tool call dies within a minute of the call ending, while editors started by the build task or the watchdog survive -> True when the task started"""
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def recover(timeout=300.0):
    """On demand, for a TOOL that finds the editor gone: do what the scheduled check would do, now, instead of in up to five minutes.
    Not running, or running and silent with an error.log written after it started (a crash) -> run the SCHEDULED watchdog task (which starts or replaces Sandbox from the Task Scheduler's own context, so it
    outlives the tool); only when that task cannot be run is Sandbox launched directly. Running and silent without a crash report -> a long load or a hang: wait, never kill (the scheduled strikes decide
    that). A deploy or an editor-closing job in progress -> wait. -> {"state": "healthy" | "recovered" | "failed", "action", "seconds"}"""
    t0 = time.time()
    action = "none"
    last_trigger = None
    pause = paused()
    if pause and not (sandbox_running() and bridge_healthy()):
        return {"state": "failed", "action": "the watchdog is %s (%s): an editor is not started or replaced" % (pause, PAUSE_FILE), "seconds": 0.0}
    while time.time() - t0 < timeout:
        if bridge_healthy():
            # An editor we had to bring back answers the bridge long before it is USABLE: the level reopens and the environment is re-applied after that. Wait for the run that is doing it
            # (it holds the lock) and for a loaded level, or the tool that asked finds "no object named ..." in an empty editor.
            if action == "none" or (level_loaded() and not os.path.exists(RUN_LOCK)):
                return {"state": "healthy" if action == "none" else "recovered", "action": action, "seconds": round(time.time() - t0, 1)}
        if deploy_in_progress() or editor_closing_job_running():
            action = "waited for a deploy or an editor-closing job"
            time.sleep(5)
            continue
        mark = deliberately_closed()
        if mark and not sandbox_running():
            return {"state": "failed", "action": "the editor was closed ON PURPOSE (%s, level %s): not reopened. `python bridge/safe_close.py release` or deleting %s allows it"
                    % (mark.get("reason") or "no reason given", mark.get("level"), MARKER), "seconds": round(time.time() - t0, 1)}
        gone = not sandbox_running()
        crashed = (not gone) and crash_report_after(sandbox_started()) is not None
        if (gone or crashed) and (last_trigger is None or time.time() - last_trigger > 60):
            last_trigger = time.time()
            log("recover (a tool asked for the editor): Sandbox %s -- asking the scheduled watchdog to deal with it" % ("is not running" if gone else "wrote error.log after it started (a crash)"))
            if _trigger_watchdog_task():
                action = "asked the scheduled watchdog to %s (it starts Sandbox outside the tool's process, so the editor survives the tool)" % ("start Sandbox and reopen the level" if gone else "replace the crashed Sandbox")
            elif _take_lock():
                try:                                           # the task cannot be run (disabled, deleted): launch directly - it works while this process lives
                    if crashed:
                        kill()
                    launch()
                    action = "the watchdog task could not be run: launched Sandbox directly (it lives only as long as this process)"
                finally:
                    _drop_lock()
        time.sleep(5)                                          # the scheduled check (or another tool) may be recovering it: wait and poll
    return {"state": "failed", "action": action, "seconds": round(time.time() - t0, 1)}


def check():
    """The scheduled check, serialised with recover() by a lock"""
    if not _take_lock():
        log("another recovery is running -- standing down this cycle")
        return 0
    try:
        return _check_locked()
    finally:
        _drop_lock()


def _check_locked():
    st = read_state()

    pause = paused()
    if pause:
        log("%s (%s) -- standing down this cycle" % (pause, PAUSE_FILE))
        return 0

    if deploy_in_progress():
        log("deploy in progress -- standing down this cycle")
        st["strikes"] = 0
        write_state(st)
        return 0

    mark = deliberately_closed()
    if not sandbox_running():
        if mark:
            log("Sandbox was closed ON PURPOSE (%s, level %s) -- not reopening it; the marker %s ends at %s or when its writer finishes"
                % (mark.get("reason") or "no reason given", mark.get("level"), MARKER, time.strftime("%H:%M:%S", time.localtime(float(mark.get("until") or 0)))))
            st["strikes"] = 0
            write_state(st)
            return 0
        log("Sandbox not running -- starting it")
        launch()
        st["strikes"] = 0
        st["restarts"] = st.get("restarts", 0) + 1
        write_state(st)
        return 1

    if os.path.exists(MARKER):
        clear_marker()                                # an editor is running: whoever closed it on purpose has started it again
    if bridge_healthy():
        if st.get("strikes"):
            log("bridge healthy again after %d strike(s)" % st["strikes"])
        st["strikes"] = 0
        remember_level(st)
        write_state(st)
        return 0

    st["strikes"] = st.get("strikes", 0) + 1
    log("Sandbox is running but the bridge did not answer (strike %d of %d)"
        % (st["strikes"], STRIKES_BEFORE_KILL))

    # A crash is not a slow level load: the crash handler has written error.log since this Sandbox started, so the second strike (five more minutes of a dead editor) is not needed.
    crashed = crash_report_after(sandbox_started())
    if crashed is not None and st["strikes"] < STRIKES_BEFORE_KILL:
        log("error.log was written at %s, after this Sandbox started: a crash, not a long load -- not waiting for the second strike" % time.strftime("%H:%M:%S", time.localtime(crashed)))
        st["strikes"] = STRIKES_BEFORE_KILL

    if st["strikes"] >= STRIKES_BEFORE_KILL:
        log("killing an editor that stopped answering; the last healthy check saw level %s with %s objects (%s)" % (st.get("level"), st.get("objects"), st.get("level_seen")))
        kill()
        launch()
        st["strikes"] = 0
        st["restarts"] = st.get("restarts", 0) + 1

    write_state(st)
    return 1


def install():
    here = os.path.abspath(__file__)
    action = '"%s" "%s"' % (sys.executable, here)
    rc = subprocess.call(["schtasks", "/Create", "/TN", TASK, "/TR", action,
                          "/SC", "MINUTE", "/MO", "5", "/F"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("install %s: %s" % (TASK, "ok" if rc == 0 else "FAILED rc=%d" % rc))
    return rc


def uninstall():
    rc = subprocess.call(["schtasks", "/Delete", "/TN", TASK, "/F"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("uninstall %s: %s" % (TASK, "ok" if rc == 0 else "FAILED rc=%d" % rc))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return check()


if __name__ == "__main__":
    sys.exit(main())
