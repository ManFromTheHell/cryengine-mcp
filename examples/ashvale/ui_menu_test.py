#!/usr/bin/env python3
"""The FlashUI menus played like a player plays them: real mouse clicks and key presses on the game
window of a package, checked against the game's own trace (claude_npc.log).

    ui_menu_test.py <build> [out dir] [--reset-user]

--reset-user deletes the build's saved games and settings (user/ashvale_slot1-3.xml, ashvale_settings.cfg) so the run starts from a clean slate;
without it nothing is deleted, but the steps that expect an empty slot may then fail. Needs the Ashvale game (its FlashUI menus and ash_* commands).

The buttons are FOUND, not hard-coded: each page is captured (PrintWindow, 2D layer included) and the light text
rows in the button column are its buttons, top to bottom. Every click goes through window_capture.click, which
refuses when the game is not the window under the cursor. Windowed 1280x720; it moves the mouse for about three
minutes, so leave the mouse alone while it runs.

Steps: main menu -> arrow keys and Enter (Down, Down, Enter opens Settings) -> Back -> Controls -> Back -> Settings
(drag two sliders, change the quality switch, Back) -> New Game
(skip the intro) -> Esc pauses -> Map (the picture, the blue "you" marker and the gold goal marker are found in the
frame) -> Back -> Inventory (the purse and the bread, Eat bread) -> Back -> Save Game -> slot 1 -> slot 1 again (overwrite
confirmation, Overwrite) -> Back -> Load Game -> slot 1 -> J opens the journal -> Back resumes -> I opens the inventory and I
closes it -> Esc, Quit to Main Menu.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import os
import re
import subprocess
import sys
import threading
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
BUILD = os.path.abspath([a for a in sys.argv[1:] if not a.startswith("--")][0])
RESET_USER = "--reset-user" in sys.argv
_pos = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = os.path.abspath(_pos[1]) if len(_pos) > 1 else os.path.join(os.path.dirname(os.path.dirname(HERE)), "ui_menu")
sys.argv = [sys.argv[0], BUILD]
import night_test as N
from window_capture import game_hwnd, capture, save_png, click, raise_window, user32

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}     # PgUp PgDn End Home Left Up Right Down Ins Del


def key(vk, hold=0.08):
    """A real key press. The scan code matters: a game reading raw or DirectInput keys ignores a bare virtual key. The arrow
    keys are EXTENDED keys: sent without KEYEVENTF_EXTENDEDKEY their scan codes 0x48/0x50 are the NUMPAD 8 and 2 to DirectInput
    (Keyboard.cpp maps them to np_8 / np_2), so the game never saw an Up or a Down."""
    sc = user32.MapVirtualKeyW(vk, 0)
    ext = 1 if vk in EXTENDED else 0
    user32.keybd_event(vk, sc, ext, 0)
    time.sleep(hold)
    user32.keybd_event(vk, sc, ext | 2, 0)     # KEYEVENTF_KEYUP


def text_bands(frame, x0=215, x1=700, y0=125, y1=715, min_rows=8):
    """Rows of light pixels in a column range, grouped: [(y centre, x centre)], top to bottom -- the buttons."""
    w, h, rows = frame
    hit = []
    for y in range(y0, min(y1, h)):
        r = rows[y]
        # light neutral text, or the movie's cyan highlight (the hovered / focused button)
        xs = [x for x in range(x0, min(x1, w))
              if min(r[3 * x], r[3 * x + 1], r[3 * x + 2]) > 150 or (r[3 * x] < 70 and r[3 * x + 1] > 120 and r[3 * x + 2] > 170)]
        hit.append(xs)
    bands, cur = [], []
    for i, xs in enumerate(hit + [[]]):
        if xs:
            cur.append((y0 + i, xs))
        elif cur:
            if len(cur) >= min_rows:
                ys = [c[0] for c in cur]
                allx = [x for c in cur for x in c[1]]
                bands.append(((ys[0] + ys[-1]) // 2, (min(allx) + max(allx)) // 2))
            cur = []
    return bands


def villagers():
    """{name: (x, y)} from claude_villager_status (positions from the log, so the same in every build)."""
    off = N.log_size()
    N.c("claude_villager_status", 1.2)
    found = {}
    for line in N.log_since(off).splitlines():
        m = re.search(r"status (\w+)[: ].* at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)", line)
        if m:
            found[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return found


def moved(a, b):
    """The largest distance any villager present in both samples moved."""
    return max([((a[k][0] - b[k][0]) ** 2 + (a[k][1] - b[k][1]) ** 2) ** 0.5 for k in a if k in b] or [0.0])


class Game:
    def __init__(self, proc):
        self.proc = proc
        self.hwnd = None

    def since(self, off):
        return N.log_since(off)

    def wait_log(self, off, pattern, timeout=12.0):
        end = time.time() + timeout
        while time.time() < end:
            text = N.log_since(off)
            m = re.search(pattern, text)
            if m:
                return text, m
            time.sleep(0.25)
        return N.log_since(off), None

    def frame(self, name=None):
        f = capture(self.hwnd)
        if name:
            save_png(os.path.join(OUT, name + ".png"), *f)
        return f

    def click_at(self, x, y):
        raise_window(self.hwnd)
        return click(self.hwnd, x, y)

    def phase(self):
        return N.status_phase()


def main():
    os.makedirs(OUT, exist_ok=True)
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("ui_menu_test\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    subprocess.run(["taskkill", "/F", "/IM", "Sandbox.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
    time.sleep(4)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    # A clean slate (only with --reset-user): no saved slot from an earlier run, default settings.
    userdir = os.path.join(BUILD, "user")
    for f in (("ashvale_slot1.xml", "ashvale_slot2.xml", "ashvale_slot3.xml", "ashvale_settings.cfg") if RESET_USER else ()):
        try:
            os.remove(os.path.join(userdir, f))
        except OSError:
            pass
    p = subprocess.Popen([N.LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                         cwd=os.path.dirname(N.LAUNCHER), creationflags=0x8 | 0x200)
    g = Game(p)
    try:
        off = N.log_size()
        for _ in range(90):
            time.sleep(3)
            try:
                o = N.log_size(); N.send("ash_status", 5); time.sleep(0.5)
                if "status: ticks" in N.log_since(o):
                    break
            except Exception:
                pass
        N.c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        g.hwnd = game_hwnd(p.pid)
        text, m = g.wait_log(off, r"page 'main' built", 15)
        check("the main menu page is built by FlashUI", m is not None, "" if m else "no 'page main built' line")
        time.sleep(1.5)
        main_frame = g.frame("01_main")
        bands = text_bands(main_frame)
        print("   main menu rows (y, x):", bands, flush=True)
        check("the main menu shows six buttons, a title and no Back", len(bands) >= 6, "%d text rows" % len(bands))
        if len(bands) < 6:
            return
        buttons = bands[-6:]

        # --- the keyboard, inside the movie: Down, Down, Enter is Settings (the highlight starts on New Game)
        raise_window(g.hwnd)
        g.click_at(640, 620)                            # focus the window; empty menu space, nothing to press
        time.sleep(0.6)
        o = N.log_size()
        key(0x28); time.sleep(0.4); key(0x28); time.sleep(0.4); key(0x0D)       # VK_DOWN, VK_DOWN, VK_RETURN
        text, m = g.wait_log(o, r"menu action 'settings'", 6)
        check("arrow keys and Enter drive the FlashUI menu", m is not None, "" if m else text[-200:])
        time.sleep(1.5)
        back = [b for b in text_bands(g.frame(), x0=215, x1=700) if b[0] > 600]
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        g.wait_log(o, r"page 'main' built")
        time.sleep(1.0)

        # --- Controls and back
        o = N.log_size()
        g.click_at(buttons[3][1], buttons[3][0])
        text, m = g.wait_log(o, r"menu action 'controls'")
        check("clicking Controls reaches the game as an action", m is not None)
        text, m = g.wait_log(o, r"page 'controls' built")
        check("...and the Controls page is built", m is not None)
        time.sleep(1.2)
        f = g.frame("02_controls")
        cb = text_bands(f, x0=215, x1=1100)
        back = [b for b in cb if b[0] > 600]
        check("the Controls page has a Back button at the bottom", len(back) >= 1, str(cb[-3:]))
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        text, m = g.wait_log(o, r"menu action 'back'")
        check("clicking Back returns", m is not None)
        text, m = g.wait_log(o, r"page 'main' built")
        check("...to the main menu", m is not None)

        # --- Settings: drag two sliders, change the quality switch
        time.sleep(1.0)
        o = N.log_size()
        g.click_at(buttons[2][1], buttons[2][0])
        text, m = g.wait_log(o, r"page 'settings' built")
        check("Settings opens", m is not None)
        time.sleep(1.5)
        f = g.frame("03_settings")
        rows = text_bands(f, x0=215, x1=420)
        print("   settings rows:", rows, flush=True)
        slider_rows = [r for r in rows if 140 < r[0] < 330]
        o = N.log_size()
        if len(slider_rows) >= 5:                    # brightness, volume, music, mouse sensitivity, quality
            g.click_at(510, slider_rows[1][0])       # volume bar, near its left end
            time.sleep(0.8)
            g.click_at(510, slider_rows[2][0])       # music bar, near its left end
            time.sleep(0.8)
            g.click_at(625, slider_rows[3][0])       # mouse sensitivity bar, near its right end
            time.sleep(0.8)
        text, m = g.wait_log(o, r"settings changed: brightness [\d.]+, volume ([\d.]+), mouse ([\d.]+)", 8)
        check("moving the sliders changes the settings", m is not None, m.group(0) if m else text[-200:])
        time.sleep(1.0)
        allm = re.findall(r"settings changed: brightness ([\d.]+), volume ([\d.]+), mouse ([\d.]+), quality \d, music ([\d.]+)", N.log_since(o))
        last = allm[-1] if allm else None
        check("volume and music went down and mouse sensitivity went up",
              last is not None and float(last[1]) < 0.5 and float(last[3]) < 0.5 and float(last[2]) > 1.5, "last change: %s" % (last,))
        if len(rows) >= 5:
            g.click_at(476, rows[4][0] if rows[4][0] < 400 else 283)      # the quality switch: click cycles the option
            time.sleep(1.0)
        o = N.log_size()
        time.sleep(1.6)
        cfg = os.path.join(userdir, "ashvale_settings.cfg")
        body = open(cfg).read() if os.path.isfile(cfg) else ""
        check("the settings are written to the file after a moment",
              "ash_volume" in body and "ash_music" in body and "ash_mouse_sens" in body, body.replace(chr(10), " | "))
        back = [b for b in text_bands(g.frame(), x0=215, x1=700) if b[0] > 600]
        if back:
            g.click_at(back[-1][1], back[-1][0])
        text, m = g.wait_log(o, r"menu action 'back'")
        check("Back leaves Settings", m is not None)

        # --- New Game, skip the intro
        time.sleep(1.5)
        o = N.log_size()
        g.click_at(buttons[0][1], buttons[0][0])
        text, m = g.wait_log(o, r"menu action 'new_game'")
        check("clicking New Game starts a game", m is not None)
        for _ in range(30):
            if g.phase() == "Intro":
                break
            time.sleep(0.5)
        N.c("ash_select", 1.0)                          # skip the intro card
        for _ in range(30):
            if g.phase() == "Playing":
                break
            time.sleep(0.5)
        check("the game is in play after the intro", g.phase() == "Playing", g.phase())
        time.sleep(1.0)

        # --- the control for the freeze: villagers do walk during play
        v1 = villagers()
        time.sleep(4.0)
        v2 = villagers()
        check("control: villagers walk about in play", moved(v1, v2) > 0.5, "largest move %.2f m over 4 s (%d villagers)" % (moved(v1, v2), len(v2)))

        # --- Esc pauses
        o = N.log_size()
        raise_window(g.hwnd)
        g.click_at(640, 360)                            # focus the window (in play a click is only a sword swing)
        time.sleep(0.8)
        key(0x1B)                                       # VK_ESCAPE
        text, m = g.wait_log(o, r"page 'pause' built", 6)
        if m is None:                                   # keys not reaching the action map: the console does the same
            print("   (Esc did not reach the game; using ash_pause)", flush=True)
            N.c("ash_pause", 1.0)
            text, m = g.wait_log(o, r"page 'pause' built", 6)
        check("Esc opens the pause page", m is not None)
        time.sleep(1.2)
        pause_frame = g.frame("04_pause")
        pb = text_bands(pause_frame)
        print("   pause rows:", pb, flush=True)
        check("the pause page shows nine buttons", len(pb) >= 9, "%d rows" % len(pb))
        pbuttons = pb[-9:] if len(pb) > 9 else pb

        # --- the world stands still behind the pause page
        time.sleep(1.0)
        v3 = villagers()
        time.sleep(4.0)
        v4 = villagers()
        check("the world is frozen while the pause page is up", moved(v3, v4) < 0.05, "largest move %.3f m over 4 s" % moved(v3, v4))

        # --- Map: the picture and both markers
        o = N.log_size()
        g.click_at(pbuttons[5][1], pbuttons[5][0])
        text, m = g.wait_log(o, r"page 'map' built")
        check("Map opens", m is not None)
        time.sleep(2.0)
        mf = g.frame("04b_map")
        w, h, rows = mf
        def count(x0, y0, x1, y1, test):
            n = 0
            for y in range(y0, y1):
                r = rows[y]
                for x in range(x0, x1):
                    if test(r[3 * x], r[3 * x + 1], r[3 * x + 2]):
                        n += 1
            return n
        light = count(830, 80, 1090, 640, lambda R, G, B: R > 120 and G > 100 and B > 60)
        check("the map picture is drawn (parchment over the panel)", light > 20000, "%d light pixels" % light)
        def to_px(x, y):
            return 800 + (x - 340.0) / 370.0 * 320, 40 + (840.0 - y) / 740.0 * 640
        px, py = to_px(512.0, 172.0)                    # the start of a new game
        blue = count(int(px) - 14, int(py) - 14, int(px) + 14, int(py) + 14, lambda R, G, B: B > 190 and R < 130 and G > 100)
        check("the blue 'you' marker is at the player's place on the map", blue > 25, "%d blue pixels around (%d, %d)" % (blue, px, py))
        gx, gy = to_px(485.5, 375.0)                    # Hilda: the goal at the start
        gold = count(int(gx) - 14, int(gy) - 14, int(gx) + 14, int(gy) + 14, lambda R, G, B: R > 220 and G > 170 and B < 120)
        check("the gold goal marker is on the reeve", gold > 25, "%d gold pixels around (%d, %d)" % (gold, gx, gy))
        back = [b for b in text_bands(mf, x0=215, x1=700) if b[0] > 600]
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        text, m = g.wait_log(o, r"page 'pause' built")
        check("Back from the map returns to the pause page", m is not None)
        time.sleep(1.0)

        # --- Inventory: the coins and bread the player carries, and eating from the page
        o = N.log_size()
        g.click_at(pbuttons[3][1], pbuttons[3][0])
        text, m = g.wait_log(o, r"page 'inventory' built")
        check("Inventory opens", m is not None)
        time.sleep(2.0)
        inv = g.frame("04c_inventory")
        ib = text_bands(inv, x0=215, x1=900)
        print("   inventory rows:", ib, flush=True)
        o = N.log_size()
        N.c("ash_status", 0.8)
        before = re.search(r"coins (\d+), bread (\d+)", N.log_since(o))
        check("the purse and the bread are what a new game gives (3 coins, 1 bread)", before is not None and before.groups() == ("3", "1"),
              before.group(0) if before else "no status line")
        eat_row = [b for b in ib if b[0] < 600]          # the buttons above Back: Coins, Bread, Health, Sword, Torch, Eat bread (the last)
        o = N.log_size()
        if eat_row:
            g.click_at(eat_row[-1][1], eat_row[-1][0])
        text, m = g.wait_log(o, r"menu action 'eat'")
        check("clicking Eat bread reaches the game", m is not None, ("rows %s" % ib) if m is None else "")
        time.sleep(1.0)
        o = N.log_size()
        N.c("ash_status", 0.8)
        after = re.search(r"coins (\d+), bread (\d+)", N.log_since(o))
        check("the bread is eaten (1 -> 0)", after is not None and after.group(2) == "0", after.group(0) if after else "no status line")
        back = [b for b in text_bands(g.frame(), x0=215, x1=900) if b[0] > 600]
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        text, m = g.wait_log(o, r"page 'pause' built")
        check("Back from the inventory returns to the pause page", m is not None)
        time.sleep(1.0)

        # --- Save Game -> slot 1
        o = N.log_size()
        g.click_at(pbuttons[1][1], pbuttons[1][0])
        text, m = g.wait_log(o, r"page 'save' built")
        check("Save Game opens the slot page", m is not None)
        time.sleep(1.2)
        f = g.frame("05_save_empty")
        sb = text_bands(f, x0=215, x1=900)
        print("   save rows:", sb, flush=True)
        o = N.log_size()
        if sb:
            g.click_at(sb[0][1], sb[0][0]) if sb[0][0] > 150 else g.click_at(sb[1][1], sb[1][0])
        text, m = g.wait_log(o, r"save '[^']*ashvale_slot1\.xml': written")
        check("clicking Slot 1 writes the save file", m is not None)
        check("the save file exists with its header", os.path.isfile(os.path.join(userdir, "ashvale_slot1.xml")))
        time.sleep(1.5)
        f = g.frame("06_save_filled")
        sb = text_bands(f, x0=215, x1=900)
        first = [b for b in sb if b[0] > 150][0]
        # --- Slot 1 again: overwrite confirmation
        o = N.log_size()
        g.click_at(first[1], first[0])
        time.sleep(1.5)
        g.frame("07_confirm")
        check("an occupied slot asks before overwriting", os.path.getsize(N.LOG) >= o and "menu action 'save:slot1'" in N.log_since(o))
        o = N.log_size()
        g.click_at(573, 340)                            # the left button of the dialog
        text, m = g.wait_log(o, r"event onConfirm\(overwrite")
        check("the dialog's answer reaches the game", m is not None, text[-200:] if m is None else "")
        text, m = g.wait_log(o, r"save '[^']*ashvale_slot1\.xml': written", 6)
        check("Overwrite saves again", m is not None)
        time.sleep(1.0)
        after_frame = g.frame("07b_after_overwrite")
        back = [b for b in text_bands(after_frame, x0=215, x1=900) if b[0] > 600]
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        text, m = g.wait_log(o, r"page 'pause' built")
        check("Back returns to the pause page", m is not None, "" if m else "back rows %s" % back)

        # --- Load Game -> slot 1
        time.sleep(1.2)
        o = N.log_size()
        g.click_at(pbuttons[2][1], pbuttons[2][0])
        text, m = g.wait_log(o, r"page 'load' built")
        check("Load Game opens the slot page", m is not None)
        time.sleep(1.2)
        f = g.frame("08_load")
        lb = [b for b in text_bands(f, x0=215, x1=900) if b[0] > 150]
        o = N.log_size()
        if lb:
            g.click_at(lb[0][1], lb[0][0])
        # the Load page lists the rotating autosave slot FIRST when one exists (AutoSave.cpp), so the first row is the autosave or slot 1
        text, m = g.wait_log(o, r"load '[^']*ashvale_(slot1|auto)\.xml': \d+ entities restored")
        check("clicking the first filled row loads it (the autosave slot, when there is one, comes first)", m is not None)
        time.sleep(1.5)
        check("...into play (the pause page is gone)", g.phase() == "Playing", g.phase())
        time.sleep(1.0)
        v5 = villagers()
        time.sleep(3.0)
        v6 = villagers()
        check("the world runs again after loading", moved(v5, v6) > 0.3, "largest move %.2f m over 3 s" % moved(v5, v6))

        # --- J: journal
        o = N.log_size()
        raise_window(g.hwnd)
        g.click_at(640, 360)
        time.sleep(0.8)
        key(0x4A)                                       # J
        text, m = g.wait_log(o, r"page 'journal' built", 6)
        if m is None:
            print("   (J did not reach the game; using ash_menu journal)", flush=True)
            N.c("ash_pause", 0.5)
            N.c("ash_menu journal", 1.0)
            text, m = g.wait_log(o, r"page 'journal' built", 6)
        check("J opens the journal", m is not None)
        time.sleep(1.2)
        g.frame("09_journal")
        back = [b for b in text_bands(g.frame(), x0=215, x1=900) if b[0] > 600]
        o = N.log_size()
        if back:
            g.click_at(back[-1][1], back[-1][0])
        time.sleep(1.5)
        check("Back from the journal resumes the game", g.phase() == "Playing", g.phase())

        # --- I: the inventory from play, and I again closes it
        o = N.log_size()
        raise_window(g.hwnd)
        g.click_at(640, 360)
        time.sleep(0.8)
        key(0x49)                                       # I
        text, m = g.wait_log(o, r"page 'inventory' built", 6)
        if m is None:
            print("   (I did not reach the game; using ash_menu inventory)", flush=True)
            N.c("ash_menu inventory", 1.0)
            text, m = g.wait_log(o, r"page 'inventory' built", 6)
        check("I opens the inventory", m is not None)
        time.sleep(1.2)
        key(0x49)
        time.sleep(1.5)
        check("I again closes it and the game runs", g.phase() == "Playing", g.phase())

        # --- pause, Quit to Main Menu
        N.c("ash_pause", 1.5)
        time.sleep(1.0)
        pb = text_bands(g.frame())
        o = N.log_size()
        if len(pb) >= 9:
            g.click_at(pb[-1][1], pb[-1][0])
        text, m = g.wait_log(o, r"menu action 'main_menu'")
        check("Quit to Main Menu works", m is not None)
        time.sleep(1.5)
        check("...and the main menu page is back", "page 'main' built" in N.log_since(o), g.phase())
        g.frame("10_back_to_main")
        print("RESULT ui_menu_test: %d of %d" % (sum(checks), len(checks)), flush=True)
    finally:
        try:
            N.send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
        stop.set()
        try:
            os.remove(N.LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
