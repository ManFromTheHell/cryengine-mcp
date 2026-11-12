#!/usr/bin/env python3
"""Take pictures of the packaged game from chosen places (a general version of house_views.py).

    view_shots.py <build> <out dir> <shots file | -> [--tod 12]

One shot per line: `label x y yaw [z [pitch]]`; a line starting with `!` is a console command run before the next shot; `?cvar value` sets a console variable and VERIFIES it by reading it back (the run stops if the engine disagrees). z is the player's FEET (default: the terrain's height at x,y plus 0.1); the camera is 1.6 m above
them. yaw 0 looks north (+y), 90 west (-x), -90 east (+x), 180 south (-y). `house:<Name>:<S|N|W|E>:<metres>` as the label's place makes the shot
from that side of a house, that many metres from its wall, looking at it. The pictures are the game window's own pixels (window_capture).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
BUILD = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
SHOTS = sys.argv[3]
TOD = sys.argv[sys.argv.index("--tod") + 1] if "--tod" in sys.argv else "12"
sys.argv = [sys.argv[0], BUILD]
import night_test as N
import build_ashvale as B
from window_capture import game_hwnd, capture, save_png

SIDES = {"S": (0.0, -1.0, 0.0), "N": (0.0, 1.0, 180.0), "W": (-1.0, 0.0, -90.0), "E": (1.0, 0.0, 90.0)}
HOUSE = {n: (cx, cy, w, d) for n, cx, cy, w, d, door, _ in B.BUILDINGS}
HOUSE.update({"Smithy": (522.0, 344.0, 7.0, 6.0), "Barn": (457.0, 392.0, 10.0, 8.0)})


def parse(line):
    parts = line.split()
    label = parts[0]
    m = re.match(r"house:(\w+):([SNWE]):([\d.]+)", " ".join(parts[1:2])) if len(parts) > 1 and parts[1].startswith("house:") else None
    if m:
        name, side, dist = m.group(1), m.group(2), float(m.group(3))
        cx, cy, w, d = HOUSE[name]
        ox, oy, yaw = SIDES[side]
        reach = (w if side in ("W", "E") else d) / 2 + dist
        x, y = cx + ox * reach, cy + oy * reach
        extra = parts[2:]
        return label, x, y, yaw, (float(extra[0]) if extra else None)
    x, y, yaw = float(parts[1]), float(parts[2]), float(parts[3])
    return label, x, y, yaw, (float(parts[4]) if len(parts) > 4 else None)


def main():
    os.makedirs(OUT, exist_ok=True)
    lines = [l.strip() for l in (sys.stdin if SHOTS == "-" else open(SHOTS)) if l.strip() and not l.startswith("#")]
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("view_shots\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    import safe_close                                                 # saves the level and closes the editor, or refuses
    closed = safe_close.close_editor("view_shots", hold_minutes=120)
    if not closed["ok"]:
        raise SystemExit("editor not closed: " + "; ".join(closed["refused"] or ["it did not close"]))
    time.sleep(3)
    p = subprocess.Popen([N.LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                         cwd=os.path.dirname(N.LAUNCHER), creationflags=0x8 | 0x200)
    try:
        for _ in range(90):
            time.sleep(3)
            try:
                o = N.log_size(); N.send("ash_status", 5); time.sleep(0.5)
                if "status: ticks" in N.log_since(o):
                    break
            except Exception:
                pass
        else:
            raise SystemExit("the game did not become ready (no ash_status reply)")
        hwnd = game_hwnd(p.pid)
        if hwnd is None:
            raise SystemExit("no game window found for pid %d" % p.pid)
        N.c("ash_skip_menu 1", 0.1)
        N.c("ash_newgame", 1.0)
        for _ in range(40):
            if N.status_phase() == "Playing":
                break
            time.sleep(0.5)
        N.c("claude_tod %s 0" % TOD, 0.3)
        N.c("ash_soundscape_oneshots 0", 0.2)
        for line in lines:
            if line.startswith("!"):                                  # a console command, then a pause for it to take effect
                N.c(line[1:], 3.0)
                continue
            if line.startswith("~"):                                  # a console command with no pause (an action that lasts a second or two)
                N.c(line[1:], 0.1)
                continue
            if line.startswith("?"):                                  # a VERIFIED setting: `?cvar value` sets it and reads it back from the engine; a mismatch ends the run
                name, _, want = line[1:].strip().partition(" ")
                got = ""
                try:
                    reply = N.send("bridge.cvar_set %s %s" % (name, want.strip()), 10)
                    got = str(json.loads(reply.get("output") or "{}").get("value", ""))
                except Exception as exc:                          # noqa: BLE001 - reported below as a failed setting
                    got = "error: %s" % exc
                try:
                    ok = abs(float(got) - float(want)) < 1e-6
                except ValueError:
                    ok = got == want.strip()
                print("CVAR %-24s wanted %-6s engine says %-6s %s" % (name, want.strip(), got, "verified" if ok else "NOT SET"), flush=True)
                if not ok:
                    raise SystemExit("the setting %s=%s did not take effect (the engine reports %r): the pictures would not be comparable" % (name, want.strip(), got))
                time.sleep(0.2)
                continue
            if line.startswith("="):                                  # a picture of where the player is now, no teleport: `=label`
                time.sleep(0.15)
                save_png(os.path.join(OUT, line[1:].strip() + ".png"), *capture(hwnd))
                print("%-24s (where he stands now)" % line[1:].strip(), flush=True)
                continue
            label, x, y, yaw, z = parse(line)
            if z is None:
                z = B.ground(x, y) + 0.1
            fast = label.startswith("fast_")                          # teleport and capture within half a second
            N.c("ash_goto_pos %.2f %.2f %.2f %.1f" % (x, y, z, yaw), 0.25 if fast else 1.6)
            time.sleep(0.1 if fast else 1.0)
            path = os.path.join(OUT, label + ".png")
            save_png(path, *capture(hwnd))
            print("%-24s from (%.1f, %.1f, %.1f) yaw %4.0f -> %s" % (label, x, y, z, yaw, os.path.basename(path)), flush=True)
    finally:
        try:
            N.send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)   # only the game this script started
        stop.set()
        try:
            os.remove(N.LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
