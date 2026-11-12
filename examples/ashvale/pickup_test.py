#!/usr/bin/env python3
"""Bread and coins as real items in the world: found, taken with E, saved, restored, put back by a new game.

    pickup_test.py <build>

Console-driven on a package (ash_goto stands the player facing an entity, ash_use is what E does), with the game's own trace as the
witness and a picture before and after for the one item the player looks at.
  - the level holds the pickups (ash_pickups), all lying there at the start of a game (the control)
  - E on coins adds them to the purse; E on bread adds a loaf; a second E on the same spot adds nothing (the control)
  - the taken item is GONE from the picture (a frame before and one after differ where it lay; two frames before do not)
  - an item out of reach cannot be taken (the control: the purse does not move)
  - a save keeps what was taken, a load puts the world back to that state (an item taken after the save lies there again)
  - a new game puts every item back and the purse at 3 coins, 1 bread
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import subprocess
import sys
import threading
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
BUILD = os.path.abspath(sys.argv[1])
# The test starts from a clean slate: the build's Ashvale save slots / settings are deleted only with --clean-saves (or in a build that carries RELEASE_OK.json).
CLEAN_SAVES = "--clean-saves" in sys.argv[2:] or os.path.isfile(os.path.join(BUILD, "RELEASE_OK.json"))
sys.argv = [sys.argv[0], BUILD]
import night_test as N
from window_capture import game_hwnd, capture, save_png

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def trace(cmd, pattern, wait=0.8):
    """Run a console command and return the match of `pattern` in the game's trace lines it produced."""
    o = N.log_size()
    N.c(cmd, wait)
    m = re.findall(pattern, N.log_since(o))
    return m[-1] if m else None


def purse():
    m = trace("ash_status", r"coins (\d+), bread (\d+)")
    return (int(m[0]), int(m[1])) if m else (-1, -1)


def pickups():
    m = trace("ash_pickups", r"pickups: (\d+) in the level, (\d+) still lying there")
    return (int(m[0]), int(m[1])) if m else (-1, -1)


def region_difference(a, b, box):
    """Pixels in `box` (x0, y0, x1, y1) whose colour moved by more than 40 between two frames."""
    w, h, ra = a
    _, _, rb = b
    x0, y0, x1, y1 = box
    n = 0
    for y in range(y0, min(y1, h)):
        for x in range(x0, min(x1, w)):
            d = abs(ra[y][3 * x] - rb[y][3 * x]) + abs(ra[y][3 * x + 1] - rb[y][3 * x + 1]) + abs(ra[y][3 * x + 2] - rb[y][3 * x + 2])
            if d > 40:
                n += 1
    return n


def main():
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("pickup_test\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    import safe_close                                     # save and close the editor, or refuse: never taskkill /F every Sandbox.exe
    closed = safe_close.close_editor("pickup_test", hold_minutes=120)
    if not closed["ok"]:
        raise SystemExit("the editor was not closed, nothing was run: " + "; ".join(closed["refused"] or ["it did not close"]))
    time.sleep(4)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    for f in (("ashvale_slot1.xml", "ashvale_slot2.xml", "ashvale_slot3.xml", "ashvale_settings.cfg") if CLEAN_SAVES else ()):
        try:
            os.remove(os.path.join(BUILD, "user", f))
        except OSError:
            pass
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
        N.c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        hwnd = game_hwnd(p.pid)
        N.c("ash_skip_menu 1", 0.1)
        N.c("ash_newgame", 1.0)
        for _ in range(40):
            if N.status_phase() == "Playing":
                break
            time.sleep(0.5)
        N.c("claude_tod 12 0", 0.3)
        N.c("ash_soundscape_oneshots 0", 0.2)
        time.sleep(2.0)

        total, remaining = pickups()
        check("the level holds the pickups", total >= 14, "%d pickups" % total)
        check("control: every one lies there at the start of a game", remaining == total, "%d of %d" % (remaining, total))
        coins0, bread0 = purse()
        check("a new game's purse is 3 coins and 1 bread", (coins0, bread0) == (3, 1), "%d coins, %d bread" % (coins0, bread0))

        # --- out of reach: stand 6 m from the coins and press E
        N.c("ash_goto Coins_Start 6.0", 1.0)
        N.c("ash_use", 0.6)
        check("control: out of reach nothing is taken", purse() == (coins0, bread0) and pickups()[1] == remaining, str(purse()))

        # --- coins: take them, and press E again
        N.c("ash_goto Coins_Start 1.3", 1.5)
        N.c("ash_use", 0.8)
        coins1, _ = purse()
        check("E on 2 coins adds them to the purse", coins1 == coins0 + 2, "%d -> %d" % (coins0, coins1))
        N.c("ash_use", 0.8)
        check("control: a second E on the same spot adds nothing", purse()[0] == coins1, str(purse()))
        check("the pickup count went down by one", pickups()[1] == remaining - 1, str(pickups()))

        # --- bread: a picture before and after. Seen from 3.5 m (closer than about 2.5 m a loaf on the ground is below the
        # bottom of the picture: the eye is 1.6 m up), then taken from 1.3 m, then seen again from where it was seen.
        out_dir = os.path.join(os.path.dirname(os.path.dirname(HERE)), "pickup_frames")
        os.makedirs(out_dir, exist_ok=True)
        N.c("ash_goto Bread_Start 3.5", 1.5)
        time.sleep(4.0)                                 # the view keeps easing for about 2.5 s after the move (compass 339 -> 340, horizon shifted): 23645 pixels "moved" in the control
        f1 = capture(hwnd)
        time.sleep(0.7)
        f2 = capture(hwnd)
        N.c("ash_goto Bread_Start 1.3", 1.2)
        N.c("ash_use", 1.0)
        N.c("ash_goto Bread_Start 3.5", 1.5)
        time.sleep(2.5)                                 # the notice fades
        f3 = capture(hwnd)
        for tag, fr in (("bread_before_a", f1), ("bread_before_b", f2), ("bread_after", f3)):
            save_png(os.path.join(out_dir, tag + ".png"), *fr)
        box = (500, 470, 780, 700)                      # where a loaf 3.5 m ahead on the ground is (below the crosshair)
        control = region_difference(f1, f2, box)
        gone = region_difference(f2, f3, box)
        _, bread1 = purse()
        check("E on a loaf adds a loaf", bread1 == bread0 + 1, "%d -> %d" % (bread0, bread1))
        check("control: two frames before the take are alike where the loaf lies", control < 120, "%d pixels moved" % control)
        check("the loaf is gone from the picture", gone > control + 120, "%d pixels moved (control %d)" % (gone, control))

        # --- a table in a house
        N.c("ash_goto Bread_ReeveTable 1.0", 1.5)
        N.c("ash_use", 0.8)
        check("a loaf on a table in a house can be taken", purse()[1] == bread1 + 1, str(purse()))

        # --- save, take another, load: the world is put back to the save
        n_after_two = pickups()[1]
        N.c("ash_pause", 1.5)
        N.c("ash_menu save:slot1", 1.5)
        save_purse = purse()
        N.c("ash_menu resume", 1.5)
        N.c("ash_goto Coins_Well 1.3", 1.5)
        N.c("ash_use", 0.8)
        check("another pickup after the save is taken", pickups()[1] == n_after_two - 1 and purse()[0] == save_purse[0] + 4, "%s %s" % (str(pickups()), str(purse())))
        N.c("ash_pause", 1.5)
        N.c("ash_menu load:slot1", 3.0)
        check("a load puts the purse back to the save", purse() == save_purse, "%s vs %s" % (str(purse()), str(save_purse)))
        check("a load puts the pickups back to the save (the late one lies there again)", pickups()[1] == n_after_two, str(pickups()))

        # --- a new game puts everything back
        N.c("ash_mainmenu", 1.5)
        N.c("ash_newgame", 2.0)
        for _ in range(40):
            if N.status_phase() == "Playing":
                break
            time.sleep(0.5)
        check("a new game puts every pickup back", pickups() == (total, total), str(pickups()))
        check("...and the purse at 3 coins, 1 bread", purse() == (3, 1), str(purse()))
        print("RESULT pickup_test: %d of %d" % (sum(checks), len(checks)), flush=True)
    finally:
        try:
            N.send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)       # only the launcher this test started
        stop.set()
        try:
            if closed.get("was_running"):
                safe_close.release()                      # let the watchdog reopen the editor again
        except Exception:
            pass
        try:
            os.remove(N.LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
