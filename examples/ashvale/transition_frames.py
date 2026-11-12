#!/usr/bin/env python3
"""What the player SEES during a level trip: the packaged game, the hatch, and a burst of screen frames (BitBlt, as fast as it goes) from just before E until the new level is in play.
Each frame is classed black / loader (the movie's pale loading page with dark strokes) / scene, and the sequence is printed with the game's own trace events, so "is the loading page on screen,
for how long, and is there a black gap before or after it" is a measurement. Both directions: down the hatch, and up the stair. No display keep-awake request (see undercroft_playthrough.py).

    transition_frames.py <build> [out dir] [--tour]      --tour: also a picture from each of the 20 standard viewpoints inside the Undercroft
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

HERE = _paths.ROOT
sys.path.insert(0, HERE)
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BUILD = os.path.abspath(ARGS[0])
OUT = os.path.abspath(ARGS[1]) if len(ARGS) > 1 else os.path.join(BUILD, "transition_frames")
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64")) if os.path.isfile(p))
# Ashvale-specific driver. CRYENGINE_STATE_DIR: folder of the shared lock/log files (default: the workspace folder, state/ unless CRYENGINE_WORKSPACE is set, as bridge_watchdog/safe_close);
# CRYENGINE_NPC_LOG: the game's NPC log; CRYENGINE_GAME_PORT: the packaged game's bridge port (default 37777; the editor bridge is 37778).
STATE_DIR = os.environ.get("CRYENGINE_STATE_DIR") or _paths.WORKSPACE
LOG = os.environ.get("CRYENGINE_NPC_LOG") or os.path.join(STATE_DIR, "claude_npc.log")
LOCK = os.path.join(STATE_DIR, "claude_deploy_in_progress")
GAME_PORT = int(os.environ.get("CRYENGINE_GAME_PORT", "37777"))
STAND = json.load(open(os.path.join(HERE, "uc_stand.json"), encoding="utf-8"))
from window_capture import game_hwnd, capture_screen, save_png  # noqa: E402


def send(cmd, timeout=30.0):
    with socket.create_connection(("127.0.0.1", GAME_PORT), timeout) as s:
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


def status2():
    off = log_size()
    c("ash_status", 0.7)
    line = [l for l in since(off).splitlines() if "status2:" in l]
    return line[-1] if line else ""


def stats(frame):
    w, h, rows = frame
    total = dark = n = 0
    for y in range(0, h, 12):
        r = rows[y]
        for x in range(0, w, 12):
            m = (r[3 * x] + r[3 * x + 1] + r[3 * x + 2]) / 3
            total += m
            dark += m < 140
            n += 1
    mean = total / max(1, n)
    return mean, dark / max(1, n)


def classify(mean, dark_share):
    if mean < 8:
        return "black"
    if mean > 170 and 0.0 < dark_share < 0.25:
        return "loader"
    return "scene"


def burst(hwnd, stop, frames, tag):
    t0 = time.time()
    i = 0
    last = None
    while not stop.is_set():
        try:
            f = capture_screen(hwnd, (1280, 720))
        except RuntimeError:
            time.sleep(0.02)
            continue
        mean, dark = stats(f)
        kind = classify(mean, dark)
        frames.append((round(time.time() - t0, 2), round(mean, 1), round(dark, 2), kind, log_size()))
        if kind != last:
            save_png(os.path.join(OUT, "%s_%02d_%s.png" % (tag, i, kind)), *f)
            i += 1
            last = kind
    return frames


def tour(hwnd):
    """Stand at the 20 standard dungeon viewpoints (uc_shots.VIEWS: eye and target in local metres) and take a real-renderer picture of each."""
    import math
    import undercroft_kit as K
    from uc_shots import VIEWS
    tdir = os.path.join(OUT, "tour")
    os.makedirs(tdir, exist_ok=True)
    for name, (eye, tgt) in VIEWS.items():
        x, y = eye[0], eye[1]
        z = K.floor_z(x, y) if hasattr(K, "floor_z") else eye[2] - 1.6
        yaw = math.degrees(math.atan2(-(tgt[0] - x), tgt[1] - y))
        c("ash_goto_pos %g %g %g %g" % (K.O[0] + x, K.O[1] + y, K.O[2] + z, yaw), 1.4)
        try:
            f = capture_screen(hwnd, (1280, 720))
            mean, dark = stats(f)
            save_png(os.path.join(tdir, name + ".png"), *f)
            print("TOUR|%-12s mean %.0f" % (name, mean), flush=True)
        except RuntimeError as e:
            print("TOUR|%-12s no frame (%s)" % (name, str(e)[:60]), flush=True)
    off = log_size()
    c("ash_perf 4 dungeon", 5.0)
    m = re.search(r"perf 'dungeon': (\d+) frames .* median ([\d.]+) ms .* worst ([\d.]+) ms", since(off))
    print("PERF|dungeon (at the last viewpoint)|%s" % ("median %s ms, worst %s ms" % (m.group(2), m.group(3)) if m else "no result"), flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):          # only this script's own burst frames (down_NN_<kind>.png / up_NN_<kind>.png)
        if re.match(r"^(down|up)_\d+_.+\.png$", f):
            os.remove(os.path.join(OUT, f))
    running = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    if "sandbox.exe" in running or "gamelauncher.exe" in running:
        sys.exit("Sandbox.exe or GameLauncher.exe is running: close it first (this script no longer force-kills other processes)")
    stop_lock = threading.Event()

    def keep_lock():
        while not stop_lock.is_set():
            try:
                with open(LOCK, "w") as lf:
                    lf.write("transition_frames\n")
            except OSError as e:
                print("   ! cannot write the lock %s: %s" % (LOCK, e), flush=True)
                return
            stop_lock.wait(30)
    threading.Thread(target=keep_lock, daemon=True).start()
    time.sleep(3)
    p = subprocess.Popen([LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"], cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
    try:
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
        c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        c("ash_skip_menu 0", 0.1)
        c("ash_newgame", 2.0)
        c("ash_select", 1.0)
        time.sleep(3.0)
        c("ash_flag wat_dead 1", 0.5)
        hwnd = None
        for _ in range(20):
            hwnd = game_hwnd(p.pid)
            if hwnd:
                break
            time.sleep(0.5)
        for direction, setup, use_cmd, want in (("down", "ash_goto TowerHatch 1.4", "ash_use", "underground 1"),
                                                ("up", None, "ash_use", "underground 0")):
            if direction == "up":
                s = STAND["UndercroftExit"]
                c("ash_goto_pos %g %g %g %g" % (s["x"], s["y"], s["z"], s["yaw"]), 1.0)
            else:
                c(setup, 1.0)
            time.sleep(1.5)
            off = log_size()
            frames = []
            stop = threading.Event()
            th = threading.Thread(target=burst, args=(hwnd, stop, frames, direction), daemon=True)
            th.start()
            time.sleep(0.6)
            c(use_cmd, 0.0)
            t_use = time.time()
            t0 = time.time()
            while want not in status2() and time.time() - t0 < 60:
                time.sleep(0.3)
            time.sleep(1.5)
            stop.set()
            th.join(5)
            text = since(off)
            print("=== %s: frames %d over %.1f s" % (direction, len(frames), frames[-1][0] if frames else 0), flush=True)
            runs = []
            for t, mean, dark, kind, _ls in frames:
                if runs and runs[-1][0] == kind:
                    runs[-1][2] = t
                    runs[-1][3] += 1
                else:
                    runs.append([kind, t, t, 1, mean])
            for kind, a, b, n, mean in runs:
                print("   %-7s %5.2f - %5.2f s (%d frames, first mean %.0f)" % (kind, a, b, n, mean), flush=True)
            pos, events = 0, []
            for line in text.splitlines(keepends=True):
                pos += len(line.encode("utf-8"))
                if any(k in line for k in ("go: ", "loading page shown", "level load starts", "level load complete", "loading page hidden", "arrived in")):
                    first = next((f for f in frames if f[4] - off >= pos), None)
                    events.append("%5.2f s  %s" % (first[0] if first else -1, line.strip().split("] ", 1)[-1][:60]))
            print("   events (time of the first frame taken after the line was written):", flush=True)
            for e in events:
                print("      " + e, flush=True)
            ev = []
            print("   trace: " + " | ".join(e.split("] ", 1)[-1][:46] for e in ev), flush=True)
            kinds = [r[0] for r in runs]
            print("RESULT|%s|loader frames: %d, black runs: %s, order: %s" % (direction, sum(r[3] for r in runs if r[0] == "loader"), [round(r[2] - r[1], 2) for r in runs if r[0] == "black"], "->".join(kinds)), flush=True)
            time.sleep(3.0)
            if direction == "down" and "--tour" in sys.argv:
                tour(hwnd)
        c("ash_mainmenu", 1.0)
        c("ash_choose 3", 0.5)
    finally:
        if p.poll() is None:
            try:
                send("quit", 5)
            except Exception:
                pass
            time.sleep(3)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)      # only the game this script started
        stop_lock.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
