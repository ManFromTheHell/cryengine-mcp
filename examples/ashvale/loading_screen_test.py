#!/usr/bin/env python3
"""Is the loading page on screen while the level loads?

    loading_screen_test.py <build> [out dir]

Launches the game windowed 1280x720 and grabs the window's pixels from the screen (BitBlt: PrintWindow needs the window's
thread, which is busy loading) as fast as it can from the
moment it exists until the main menu page is built. The game's own trace (claude_npc.log) gives the events:
'level load starts', 'loading page shown', 'level load progress N', 'level load complete', 'loading page hidden',
"page 'main' built". The PICTURE is judged on the frames: the movie's loader page (showLoader) is pale with the CRYENGINE eye
and LOADING in dark strokes; the main menu is the dimmed valley with light text. The loader must show while the level loads
and be gone once the menu shows. Frames are saved when the picture changes, so the sequence can be looked at.
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
from window_capture import game_hwnd, capture_screen as capture, save_png, user32

BUILD = OUT = N = None        # set in main(): night_test reads sys.argv when it is imported, so the arguments are handled there

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def light_share(frame, box):
    w, h, rows = frame
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    n = hit = 0
    for y in range(y0, y1, 2):
        r = rows[y]
        for x in range(x0, x1, 2):
            n += 1
            hit += min(r[3 * x], r[3 * x + 1], r[3 * x + 2]) > 170
    return hit / max(1, n)


def dark_share(frame):
    """Share of the frame's pixels that are dark strokes: the logo and LOADING of the movie's loader page on its pale ground."""
    w, h, rows = frame
    n = hit = 0
    for y in range(0, h, 3):
        r = rows[y]
        for x in range(0, w, 3):
            n += 1
            hit += max(r[3 * x], r[3 * x + 1], r[3 * x + 2]) < 140
    return hit / max(1, n)


def mean_brightness(frame):
    w, h, rows = frame
    total = n = 0
    for y in range(0, h, 12):
        r = rows[y]
        for x in range(0, w, 12):
            total += (r[3 * x] + r[3 * x + 1] + r[3 * x + 2]) / 3
            n += 1
    return total / max(1, n)


def signature(frame):
    w, h, rows = frame
    return tuple(rows[y][3 * x] // 32 for y in range(0, h, 90) for x in range(0, w, 160))


def _running(image):
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + image, "/NH"], capture_output=True, text=True)
    return image.lower() in r.stdout.lower()


def main():
    global BUILD, OUT, N
    if len(sys.argv) < 2:
        sys.exit("usage: loading_screen_test.py <build> [out dir]")
    BUILD = os.path.abspath(sys.argv[1])
    OUT = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.dirname(HERE)), "ui_loading")
    sys.argv = [sys.argv[0], BUILD]
    import night_test
    N = night_test
    for image in ("Sandbox.exe", "GameLauncher.exe"):            # never kill a program by image name: it may be the editor with unsaved work or another game
        if _running(image):
            sys.exit("%s is running: close it first (this test does not kill programs it did not start)" % image)
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if re.fullmatch(r"\d{3}_[\d.]+s\.png", f):               # only this test's own frames
            os.remove(os.path.join(OUT, f))
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("loading_screen_test\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    off = N.log_size()
    t0 = time.time()
    extra = os.environ.get("ASH_EXTRA_ARGS", "").split()          # e.g. "+gfx_uiaction_log 1" (Profile only)
    p = subprocess.Popen([N.LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"] + extra,
                         cwd=os.path.dirname(N.LAUNCHER), creationflags=0x8 | 0x200)
    frames = []            # (seconds, title light share, buttons light share, saved name)
    last_sig = None
    saved = 0
    try:
        hwnd = None
        text = ''
        not_on_top = 0
        built_at = None
        while time.time() - t0 < 150:
            time.sleep(0.02)                 # as fast as PrintWindow allows: the whole load is about a second
            text = N.log_since(off) if len(frames) % 8 == 0 else text
            if hwnd is None:
                hwnd = game_hwnd(p.pid)
                if hwnd is None:
                    continue
                # On top of everything for the run: a screen grab reads whatever is in front of the game.
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)       # HWND_TOPMOST, NOMOVE|NOSIZE|SHOWWINDOW
            try:
                f = capture(hwnd, (1280, 720))
            except RuntimeError as e:          # the game is not the window on top (or not sized yet): take no pixels
                not_on_top += 1
                if not_on_top == 1:
                    print("   (%s)" % e, flush=True)
                continue
            except Exception:
                continue
            title = light_share(f, (0.17, 0.08, 0.42, 0.17))
            buttons = light_share(f, (0.17, 0.20, 0.30, 0.52))
            mean = mean_brightness(f)
            dark = dark_share(f)
            sig = signature(f)
            name = ""
            if sig != last_sig and saved < 120:
                name = "%03d_%.1fs.png" % (saved, time.time() - t0)
                save_png(os.path.join(OUT, name), *f)
                saved += 1
                last_sig = sig
            frames.append((time.time() - t0, title, buttons, mean, dark, name))
            if "page 'main' built" in text:
                # keep photographing for a while: does the engine's splash ever give way to the menu?
                built_at = built_at or time.time()
                if time.time() - built_at > 4:
                    break
        text = N.log_since(off)
        lines = [l for l in text.splitlines() if "[AshvaleUI]" in l]
        for l in lines[:20]:
            print("   " + l[:160], flush=True)
        check("the game logged the level load starting", "level load starts" in text)
        check("...and the loading page being shown", "loading page shown" in text)
        check("...and the level load completing", "level load complete" in text)
        check("...and the page being hidden again", "loading page hidden" in text)
        check("the main menu page follows", "page 'main' built" in text)
        # Frames captured between 'shown' and 'hidden' cannot be timed from the log (no timestamps there), so use the
        # picture: a loading frame has the title text and an empty button column.
        print("   timeline (s, title-area light share, button-column light share, mean brightness, dark-stroke share):", flush=True)
        last = None
        for fr in frames:
            row = (round(fr[1], 2), round(fr[2], 2), round(fr[3]), round(fr[4], 3))
            if fr[0] > 1.0 and (row != last or fr[5]):          # only when the picture changes
                print("     %5.2f  %.3f  %.3f  %5.1f  %.3f  %s" % fr, flush=True)
            last = row
        # The loading page is the movie's own loader (showLoader): a PALE full-screen page with the CRYENGINE eye and LOADING
        # in dark strokes. (The first version of this test looked for a dark page with the ASHVALE caption and so called a
        # working page a failure; the wrong expectation, found from the frames.) The menu: a dark, dimmed valley with light
        # text in the title area and the button column.
        loader = [fr for fr in frames if fr[3] > 190 and fr[4] > 0.004]
        menu = [fr for fr in frames if fr[3] < 120 and fr[1] > 0.01 and fr[2] > 0.005]
        check("the loader page (CRYENGINE eye, LOADING) was on screen while the level loaded", len(loader) >= 3,
              "%d loader frames, %d menu frames of %d taken (%d skipped: the game was not on top)" % (len(loader), len(menu), len(frames), not_on_top))
        check("the main menu is on screen afterwards", len(menu) >= 3)
        if loader and menu:
            check("the loader is gone once the menu shows (no loader frame after the first menu frame)",
                  all(fr[0] < menu[0][0] for fr in loader),
                  "loader %.1f-%.1f s, menu from %.1f s" % (loader[0][0], loader[-1][0], menu[0][0]))
        print("RESULT loading_screen_test: %d of %d (frames in %s)" % (sum(checks), len(checks), OUT), flush=True)
    finally:
        if p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)       # only the launcher this run started
        stop.set()
        try:
            os.remove(N.LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
