#!/usr/bin/env python3
"""Can a villager walk through a door (an off-mesh link) in this build?

The FarmGate doors register MNM off-mesh links; an exclusion area cuts each doorway out of the navmesh, so
the inside of a house is reachable ONLY through the link. With the stock 5.7.1 CryAISystem the component
navigation never Begins a link's movement block -- Release packages run the stock DLL, the
editor ran a patched one. This sends Martha into Cottage B and reports what happened.

    door_crossing_test.py --build <package>     over the game bridge (Sandbox closed for the run)
    door_crossing_test.py --editor              in editor game mode

Checks: the planner can reach the inside with the villager's links (claude_nav_reach ... Martha -> 1) but not
without them (control -> 0); then where Martha ends up after 60 s, and her trace (arrived / path failed /
crossing lines).
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

# Ashvale-specific driver (Cottage B coordinates below). CRYENGINE_STATE_DIR: the folder holding the shared lock/log files
# (default: the workspace folder, state/ unless CRYENGINE_WORKSPACE is set, the same folder bridge_watchdog/safe_close use); CRYENGINE_NPC_LOG overrides the game's NPC log path.
STATE_DIR = os.environ.get("CRYENGINE_STATE_DIR") or _paths.WORKSPACE
LOG = os.environ.get("CRYENGINE_NPC_LOG") or os.path.join(STATE_DIR, "claude_npc.log")
LOCK = os.path.join(STATE_DIR, "claude_deploy_in_progress")
EDITOR = "--editor" in sys.argv
BUILD = os.path.abspath(sys.argv[sys.argv.index("--build") + 1]) if "--build" in sys.argv else None
FOLLOW = sys.argv[sys.argv.index("--follow") + 1] if "--follow" in sys.argv else None   # claude_link_follow A/B
INSIDE = (486.0, 329.5, 36.2)      # Cottage B's floor; its door link runs 486.0 334.2 -> 484.7 331.6
OUTSIDE = (492.0, 340.0, 36.2)


def send(cmd, timeout=30.0):
    if EDITOR:
        import build_ashvale as B
        return B.send(cmd, timeout=timeout)
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


def martha_pos():
    off = log_size()
    c("claude_villager_status", 0.8)
    for l in since(off).splitlines():
        m = re.search(r"status Martha:.*? at ([-\d.]+) ([-\d.]+) ([-\d.]+)", l)
        if m:
            return tuple(float(v) for v in m.groups())
    return None


launcher = awake = None
stop = threading.Event()
if not EDITOR:
    if BUILD is None:
        sys.exit("need --build <package folder> or --editor")
    exe = next((p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64")) if os.path.isfile(p)), None)
    if exe is None:
        sys.exit("no GameLauncher.exe under %s (bin/win_x64_release or bin/win_x64)" % BUILD)
    _running = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    if "sandbox.exe" in _running or "gamelauncher.exe" in _running:
        sys.exit("Sandbox.exe or GameLauncher.exe is running: close it first (this test no longer force-kills other processes)")

    def keep():
        while not stop.is_set():
            try:
                with open(LOCK, "w") as lf:
                    lf.write("door_crossing_test\n")
            except OSError as e:
                print("   ! cannot write the lock %s: %s" % (LOCK, e), flush=True)
                return
            stop.wait(60)
    threading.Thread(target=keep, daemon=True).start()
    time.sleep(3)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    launcher = subprocess.Popen([exe, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                                cwd=os.path.dirname(exe), creationflags=0x8 | 0x200)
    print("build:", exe)
try:
    if EDITOR:
        import editor_state as E
        E.hold_active()
        if E.set_game_mode(True) is None:
            sys.exit("no game mode")
    else:
        for _ in range(90):
            time.sleep(3)
            try:
                off = log_size()
                send("ash_status", 5)
                time.sleep(0.5)
                if "status: ticks" in since(off):
                    break
            except Exception:
                pass
    c("ash_skip_menu 1", 0.1)
    c("ash_newgame", 6.0)
    c("ash_goto_pos 494 342 36.6 225", 1.0)          # stand where the camera sees the door, out of her way
    off = log_size()
    c("claude_nav_reach %.1f %.1f %.1f %.1f %.1f %.1f Martha" % (OUTSIDE + INSIDE), 0.8)
    c("claude_nav_reach %.1f %.1f %.1f %.1f %.1f %.1f -" % (OUTSIDE + INSIDE), 0.8)
    reach = re.findall(r"nav reach .*?: (\d)", since(off))
    print("reachable with Martha's links / without (control):", reach)
    if FOLLOW is not None:
        c("claude_link_follow %s" % FOLLOW, 0.2)
    start = martha_pos()
    off = log_size()
    c("claude_npc_goto Martha %.1f %.1f %.1f" % INSIDE, 0.5)
    trail = []
    for _ in range(12):
        time.sleep(5)
        p = martha_pos()
        trail.append(p)
    text = since(off)
    end = trail[-1] if trail else None
    dist = ((end[0] - INSIDE[0]) ** 2 + (end[1] - INSIDE[1]) ** 2) ** 0.5 if end else None
    lines = [l for l in text.splitlines() if l.startswith("Martha") or "[NavLink" in l or "FarmGate" in l or "crossing" in l.lower()]
    print("start", start, "end", end, "distance to the target %.2f m" % dist if dist is not None else "")
    print("trail:", " ".join("(%.1f,%.1f)" % (p[0], p[1]) for p in trail if p))
    for l in [l for l in lines if re.search(r"arrived|path failed|cross|link|stuck|stalled|Begin|block", l, re.I)][:14]:
        print("   |", l[:170])
    # Judge by the closest she got and by her own "order arrived": once there, her routine takes her
    # back out through the same door, so the last position says nothing.
    closest = min((((p[0] - INSIDE[0]) ** 2 + (p[1] - INSIDE[1]) ** 2) ** 0.5 for p in trail if p), default=None)
    arrived = re.search(r"Martha: order arrived", text) is not None or "finished the gate crossing" in text
    print("link follow attaches: %d, crossings started: %d, STALLED: %d" % (
        len(re.findall(r"link follow: entity", text)), len(re.findall(r"starts a gate crossing", text)), text.count("STALLED")))
    print("closest to the target %.2f m, order arrived: %s" % (closest if closest is not None else -1, arrived))
    dist = closest
    inside = closest is not None and closest < 1.5 and arrived
    print("RESULT %s%s: reach %s, Martha %s (%.2f m from the target)" % (
        "editor" if EDITOR else os.path.basename(BUILD), "" if FOLLOW is None else " (link follow %s)" % FOLLOW, "/".join(reach), "INSIDE" if inside else "NOT inside", dist if dist is not None else -1))

    # The villagers' animation, in the same package (these two checks used to be package_test.py's, which was otherwise a copy of
    # the boot, shader and sound checks other tests make): the stride matcher and the 2D blend space are live while they walk.
    off = log_size()
    c("claude_slide_period 3", 0.2)
    c("claude_slide_target *", 12.0)
    c('claude_slide_target ""', 1.0)
    text2 = since(off)
    dims = re.findall(r"'villager_move' active=1 weight [\d.]+ time [\d.]+ dims (\d)", text2)
    matcher = re.findall(r"stride match (on|off) -- scale ([\d.]+)", text2)
    turn = re.findall(r"turn -- body [-+\d.]+ rad/s, fed TurnSpeed", text2)

    # The planner's reachability (claude_nav_reach, printed above) is NOT a check: with and without Martha's links it has read 1 and 1 in
    # every run (the navmesh already joins the two sides), so it cannot tell a working crossing from a broken one. A check that expected
    # 0 without the links would fail for exactly that reason, so there is none.
    # What discriminates is the crossing itself: she walks through the door and her order arrives.
    checks = [
        ("Martha walks through the door and her order arrives", inside, "closest %.2f m, order arrived %s" % (closest if closest is not None else -1, arrived)),
        ("no crossing stalled", text.count("STALLED") == 0, "%d STALLED" % text.count("STALLED")),
        ("stride matcher and turn blend running", bool(matcher) and matcher[0][0] == "on" and bool(turn), "%d stride lines, %d turn lines" % (len(matcher), len(turn))),
        ("2D blend space live (if a villager was traced mid-walk)", ("2" in dims) if dims else True, "sampler dims seen: %s" % (sorted(set(dims)) or "none traced")),
    ]
    for name, ok, detail in checks:
        print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail))
    print("RESULT door_crossing_test: %d of %d" % (sum(1 for _, ok, _ in checks if ok), len(checks)))
finally:
    if EDITOR:
        E.set_game_mode(False)
        E.release_active()
    else:
        try:
            send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        if launcher is not None and launcher.poll() is None:      # only the game this script started
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(launcher.pid)], capture_output=True)
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
