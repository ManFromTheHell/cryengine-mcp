#!/usr/bin/env python3
"""Capture what a PLAYER sees in every UI state of a packaged build.

The engine's own screenshot (bridge.screenshot / r_getscreenshot) is taken before the 2D overlays, so it
never contains a menu, dialogue or HUD; this captures the game window's pixels instead, with
PrintWindow(PW_RENDERFULLCONTENT) -- the window's own content even when another window (a firewall prompt)
is on top of it. Windowed 1280x720.

    ui_screens.py <build> <out dir>

States: the intro (title card), play (HUD), a dialogue, the death screen, the ending (the menu pages are
ui_menu_test.py's). Each is also checked for text: the fraction of near-white/cream pixels in the region
the game draws that state's text (a blank overlay reads ~0).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from ctypes import wintypes

# same variables as night_test.py: the game's trace file and the watchdog lock
LOG = os.environ.get("CRYENGINE_GAME_LOG") or _paths.workspace("claude_npc.log")
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
user32.SetProcessDPIAware()


def send(cmd, timeout=20.0):
    with socket.create_connection(("127.0.0.1", 37777), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            d = s.recv(65536)
            if not d:
                break
            buf += d
    return buf


def c(cmd, wait=0.5):
    try:
        send(cmd)
    except Exception as e:
        print("   !", cmd, e)
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


from window_capture import game_hwnd, capture, save_png, text_fraction

def main():
    if len(sys.argv) < 3:
        sys.exit("usage: ui_screens.py <build> <out dir>")
    BUILD = os.path.abspath(sys.argv[1])
    OUT = os.path.abspath(sys.argv[2])
    LAUNCHER = next((p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                     if os.path.isfile(p)), None)
    if LAUNCHER is None:
        sys.exit("no GameLauncher.exe under %s (bin/win_x64_release or bin/win_x64)" % BUILD)
    stop = threading.Event()


    def keep():
        while not stop.is_set():
            open(LOCK, "w").write("ui_screens\n")
            stop.wait(60)


    threading.Thread(target=keep, daemon=True).start()
    os.makedirs(OUT, exist_ok=True)
    for image in ("Sandbox.exe", "GameLauncher.exe"):             # never kill a program by image name: it may be the editor with unsaved work or another game
        if image in subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + image, "/NH"], capture_output=True, text=True).stdout:
            stop.set()
            sys.exit("%s is running: close it first (this script does not kill programs it did not start)" % image)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    p = subprocess.Popen([LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                         cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
    results = []
    belief = []
    try:
        for _ in range(60):
            time.sleep(3)
            try:
                off = log_size()
                send("ash_status")
                time.sleep(0.5)
                if "status: ticks" in since(off):
                    break
            except Exception:
                pass
        c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        hwnd = game_hwnd(p.pid)

        def state(name, box):
            w, h, rows = capture(hwnd)
            save_png(os.path.join(OUT, name + ".png"), w, h, rows)
            f = text_fraction(w, h, rows, box)
            results.append((name, f))
            print("SCREEN|%-14s|text pixels %.2f%% in the region it is drawn" % (name, 100 * f), flush=True)

        def music():
            """What the game believes it is playing (Soundscape::Status writes the music line to the trace file)."""
            off = log_size()
            c("ash_soundscape", 0.6)
            found = re.findall(r"\[Ashvale\] music: (.*)", since(off))
            return found[-1] if found else ""

        c("ash_skip_menu 0", 0.1)
        c("ash_mainmenu", 3.0)
        line = music()
        belief.append(("the menu music plays in the main menu", "playing menu 1 day 0 night 0" in line, line[-110:]))
        # The main menu, Controls and pause pages are FlashUI now and ui_menu_test.py checks them with real clicks and stricter tests
        # (their buttons found in the picture): not repeated here. This test keeps the screens the game draws itself.
        c("ash_choose 1", 3.0)
        state("intro_card", (0.1, 0.3, 0.9, 0.55))
        for _ in range(40):
            off = log_size()
            c("ash_status", 0.5)
            if "phase Playing" in since(off):
                break
        state("play_hud", (0.0, 0.0, 0.45, 0.1))
        c("ash_goto Hilda 1.6", 0.8)
        c("ash_use", 1.5)
        state("dialogue", (0.05, 0.55, 0.95, 1.0))
        line = music()
        belief.append(("a spoken line starts when the dialogue opens", bool(re.search(r"voices started [1-9]\d*, speaking ash_vox_\w+_stop", line)), line[-70:]))
        c("ash_choose 1", 0.6)
        c("ash_choose 1", 0.6)
        c("ash_choose 1", 0.6)
        c("ash_pause", 1.2)
        c("ash_choose 1", 0.6)
        c("ash_flag has_blade 1", 0.2)
        c("ash_goto BanditGateW 1.5", 0.5)
        for _ in range(60):
            off = log_size()
            c("ash_status", 0.5)
            if "phase Dead" in since(off):
                break
        time.sleep(1.5)
        state("death_screen", (0.1, 0.3, 0.9, 0.7))
        c("ash_select", 1.5)
        c("ash_flag tithe_returned 1", 0.3)
        for _ in range(10):
            off = log_size()
            c("ash_status", 0.5)
            if "phase Ending" in since(off):
                break
        time.sleep(1.0)
        state("ending", (0.1, 0.25, 0.9, 0.75))
        time.sleep(3.5)                                       # the calm track fades out over 3 s
        line = music()
        belief.append(("the ending music plays at the ending, the calm tracks are gone", "playing menu 0 day 0 night 0 danger 0 end 1" in line, line[-110:]))
    finally:
        try:
            send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        if p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)       # only the launcher this run started
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass
    # A state passes when text covers more than 0.15 % of the region it is drawn in (a blank overlay reads ~0; the lowest seen on a
    # working build is the death card at 0.31 %). The earlier version only printed the numbers and the suite called any run "ok".
    EXPECTED = ["intro_card", "play_hud", "dialogue", "death_screen", "ending"]
    ok = 0
    for want in EXPECTED:
        f = dict(results).get(want)
        good = f is not None and f > 0.0015
        ok += good
        print("CHECK|%s|%s has its text on screen|%s" % ("PASS" if good else "FAIL", want, "not reached" if f is None else "%.2f %%" % (100 * f)))
    for name, good, detail in belief:
        ok += bool(good)
        print("CHECK|%s|%s|%s" % ("PASS" if good else "FAIL", name, detail))
    print("RESULT ui_screens: %d of %d (%s)" % (ok, len(EXPECTED) + len(belief), ", ".join("%s %.2f%%" % (n, 100 * f) for n, f in results)))


if __name__ == "__main__":
    main()
