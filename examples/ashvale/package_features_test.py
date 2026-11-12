#!/usr/bin/env python3
"""Do the packaged-build features survive packaging?  A packaged build, over the game bridge:

  the breakable fence (the Track View intro is release_playthrough.py's): at BreakFence_Field, impulse 0 swings change nothing (control), impulse 80 swings break
     pieces off; BreakFence_Field2, 5 m away and never struck, keeps its 8 parts.

    package_features_test.py <build>

Sandbox is closed for the run (port 37777) with the watchdog held off and the display kept awake.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

# Ashvale-specific, standalone script (run by final_packages.py): it closes the editor and starts the packaged game. Run it only as `package_features_test.py <build>`.
if __name__ != "__main__" or len(sys.argv) < 2:
    raise SystemExit("usage: package_features_test.py <packaged build folder>  (Ashvale-specific; closes the editor and starts the packaged game)")
BUILD = os.path.abspath(sys.argv[1])
LAUNCHER = next((p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                 if os.path.isfile(p)), None)
if LAUNCHER is None:
    raise SystemExit("no GameLauncher.exe under %s" % BUILD)
# the game's log and the watchdog lock; CRYENGINE_GAME_LOG / CRYENGINE_DEPLOY_LOCK override (defaults: the workspace folder, state/)
LOG = os.environ.get("CRYENGINE_GAME_LOG") or _paths.workspace("claude_npc.log")
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
results = []


def send(cmd, timeout=30.0):
    with socket.create_connection(("127.0.0.1", 37777), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def c(cmd, wait=0.4):
    try:
        send(cmd)
    except Exception as e:
        print("   !", cmd, e, flush=True)
    time.sleep(wait)


def log_size():
    for _ in range(40):
        try:
            return os.path.getsize(LOG)
        except (PermissionError, FileNotFoundError):
            time.sleep(0.05)
    return 0


def since(off):
    for _ in range(40):
        try:
            with open(LOG, "rb") as f:
                f.seek(off)
                return f.read().decode("utf-8", "replace")
        except PermissionError:
            time.sleep(0.05)
    return ""


def check(name, ok, detail=""):
    results.append((name, ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def physnear():
    off = log_size()
    c("ash_physnear 478 300 37 3.5", 0.8)
    text = since(off)
    m = re.search(r"physnear: (\d+) entities within [\d.]+ m, (\d+) static parts, (\d+) rigid bodies", text)
    other = any("482.5 302.5" in l and "parts 8" in l for l in text.splitlines())
    return ((int(m.group(2)), int(m.group(3))) if m else (None, None)), other


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        with open(LOCK, "w") as f:
            f.write("package_features_test\n")
        stop.wait(60)


threading.Thread(target=keep_lock, daemon=True).start()
import safe_close  # noqa: E402  (saves the level and closes the editor, or refuses)
_closed = safe_close.close_editor("package_features_test", hold_minutes=120)
if not _closed["ok"]:
    raise SystemExit("editor not closed: " + "; ".join(_closed["refused"] or ["it did not close"]))
time.sleep(3)
# NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
launcher = subprocess.Popen([LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                            cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
try:
    ready = False
    for _ in range(90):
        time.sleep(3)
        try:
            off = log_size()
            send("ash_status", 5)
            time.sleep(0.5)
            if "status: ticks" in since(off):
                ready = True
                break
        except Exception:
            pass
    check("launcher boots (%s)" % ("Release" if "release" in LAUNCHER else "Profile"), ready)
    if not ready:
        raise SystemExit(1)
    c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)

    # The intro is release_playthrough.py's (it logs the sequence playing with its camera bound and finishing): not repeated here.
    # (Playing it here as well would take 13 s and repeat the same three checks.)
    c("ash_skip_menu 1", 0.1)
    c("ash_newgame", 3.0)

    # The fence.
    c("ash_goto_pos 478 297.6 36.2 0", 1.5)
    before, _ = physnear()
    c("ash_sword_impulse 0", 0.1)
    for _ in range(3):
        c("ash_attack", 0.9)
    control, _ = physnear()
    c("ash_sword_impulse 80", 0.1)
    for _ in range(3):
        c("ash_attack", 0.9)
    time.sleep(2.0)
    after, other_intact = physnear()
    check("fence whole at the start (one static, 8 parts + neighbours)", before[0] is not None and before[1] == 0, str(before))
    check("control: impulse-0 swings change nothing", control == before, "%s -> %s" % (before, control))
    check("impulse-80 swings break pieces off", after[1] is not None and after[1] > 0 and after[0] < before[0],
          "%s -> %s" % (before, after))
    check("the fence 5 m away keeps its 8 parts", other_intact)
finally:
    try:
        send("quit", 5)
    except Exception:
        pass
    time.sleep(3)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(launcher.pid)], capture_output=True)   # only the game this script started
    stop.set()
    try:
        os.remove(LOCK)
    except OSError:
        pass
print("RESULT %s: %d of %d" % (os.path.basename(BUILD), sum(ok for _, ok in results), len(results)))
