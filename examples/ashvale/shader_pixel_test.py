#!/usr/bin/env python3
"""Do the project's shaders really DRAW in a packaged build?

`ash_shader` only proves a shader parsed. This boots the build's own GameLauncher (Sandbox closed,
watchdog held off), starts a game, puts a sphere 3 m in front of the camera with each material
(`ash_shader_probe`), captures lossless .tga screenshots through the game bridge and counts pixels:

  none              (no probe)                     control: no cyan, no magenta
  ash_refmagenta    stock ReferenceImage + magenta  control: the probe + screenshot chain works
  ash_flat          AshFlat (PS returns cyan)       exact cyan (0,255,255), drawn after HDR
  ash_illum         AshIllum (deferred, albedo cyan) lit cyan: G,B >> R

Each probe is captured 3 s and 10 s after it appears (combinations compile asynchronously).

    shader_pixel_test.py <build folder> [--label X]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

BUILD = sys.argv[1]
LABEL = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else os.path.basename(BUILD)
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                if os.path.isfile(p))
# Needs the Ashvale Game.dll (ash_* commands, game bridge on port 37777).
# CRYENGINE_NPC_LOG = the game's NPC log (default: claude_npc.log in the active project folder).
LOG = os.environ.get("CRYENGINE_NPC_LOG") or os.path.join(__import__("project_root").PROJECT, "claude_npc.log")
import safe_close
LOCK = _paths.workspace("claude_deploy_in_progress").replace("\\", "/")      # the watchdog's deploy lock
SHOTS = os.path.join(BUILD, "shader_shots")
PROBES = [("none", None), ("ash_refmagenta", "materials/ashvale/ash_refmagenta"),
          ("ash_flat", "materials/ashvale/ash_flat"), ("ash_illum", "materials/ashvale/ash_illum")]


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


def read_tga(path):
    """Uncompressed 24/32-bit TGA -> (width, height, list of (r, g, b))."""
    data = open(path, "rb").read()
    idlen, cmtype, imtype = data[0], data[1], data[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if imtype != 2 or cmtype != 0 or bpp not in (24, 32):
        raise ValueError("unsupported TGA (type %d, %d bpp)" % (imtype, bpp))
    step = bpp // 8
    px = data[18 + idlen:18 + idlen + w * h * step]
    return w, h, [(px[i + 2], px[i + 1], px[i]) for i in range(0, len(px), step)]


def count(path):
    w, h, pix = read_tga(path)
    exact_cyan = sum(1 for r, g, b in pix if r == 0 and g == 255 and b == 255)
    exact_magenta = sum(1 for r, g, b in pix if r == 255 and g == 0 and b == 255)
    cyanish = sum(1 for r, g, b in pix if g > 40 and b > 40 and r * 3 < min(g, b) and abs(g - b) * 3 < max(g, b))
    magentaish = sum(1 for r, g, b in pix if r > 40 and b > 40 and g * 3 < min(r, b) and abs(r - b) * 3 < max(r, b))
    return {"size": "%dx%d" % (w, h), "exact_cyan": exact_cyan, "exact_magenta": exact_magenta,
            "cyanish": cyanish, "magentaish": magentaish}


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        with open(LOCK, "w") as f:
            f.write("shader_pixel_test %s\n" % LABEL)
        stop.wait(60)


if __name__ == "__main__":
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    threading.Thread(target=keep_lock, daemon=True).start()
    # the editor is saved and closed, or this refuses: never taskkill /F Sandbox
    res = safe_close.close_editor("shader_pixel_test", hold_minutes=180)
    if not res["ok"]:
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        raise SystemExit("editor not closed: " + "; ".join(res["refused"]))
    time.sleep(3)
    launcher = subprocess.Popen([LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720"],
                                cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
    results = {}
    try:
        ready = False
        for _ in range(60):
            time.sleep(3)
            try:
                off = os.path.getsize(LOG)
                send("ash_status", 10)
                time.sleep(0.5)
                with open(LOG, "rb") as f:
                    f.seek(off)
                    if b"status: ticks" in f.read():
                        ready = True
                        break
            except Exception:
                pass
        print("ready:", ready, flush=True)
        if not ready:
            sys.exit(1)
        send("ash_skip_menu 1")
        send("ash_newgame")
        time.sleep(10)
        for name, material in PROBES:
            send("ash_shader_probe %s 3" % material if material else "ash_shader_probe off")
            for wait in (3, 7):
                time.sleep(wait)
                shot = os.path.join(SHOTS, "%s_%s_%ds.tga" % (LABEL, name, 3 if wait == 3 else 10)).replace("\\", "/")
                r = send("bridge.screenshot %s" % shot, 40)
                time.sleep(1.5)
                try:
                    c = count(shot)
                except Exception as e:
                    c = {"error": "%s (%s)" % (e, r)}
                results[(name, 3 if wait == 3 else 10)] = c
                print("%-15s %2d s  %s" % (name, 3 if wait == 3 else 10, c), flush=True)
    finally:
        try:
            send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
        safe_close.release()                                  # the watchdog may reopen the editor now
    # Verdicts, each against its control.
    none = results.get(("none", 10), {})
    ref = results.get(("ash_refmagenta", 10), {})
    flat = results.get(("ash_flat", 10), {})
    illum = results.get(("ash_illum", 10), {})
    checks = [
        ("control: no probe -> no cyan/magenta", none.get("exact_cyan", 1) == 0 and none.get("exact_magenta", 1) == 0),
        ("control: stock ReferenceImage probe draws magenta", ref.get("exact_magenta", 0) > 500),
        ("AshFlat draws exact cyan", flat.get("exact_cyan", 0) > 500),
        ("AshIllum draws lit cyan", illum.get("cyanish", 0) > 500 + none.get("cyanish", 0)),
    ]
    for text, ok in checks:
        print("CHECK|%s|%s" % ("PASS" if ok else "FAIL", text))
    print("RESULT %s: %d of %d" % (LABEL, sum(ok for _, ok in checks), len(checks)))
