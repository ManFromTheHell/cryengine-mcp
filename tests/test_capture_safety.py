#!/usr/bin/env python3
"""No script of this project may copy a screen rectangle without proving the game is the window in front of it.

2026-10-14: the Undercroft playthrough's own screenshot function copied the screen rectangle blindly; with another program's window in front of the game it saved pictures of THAT window
(a private chat), which were deleted. window_capture.capture_screen refuses unless the game owns the centre and the four corners of its client area at the expected size. This test fails if
any script outside window_capture.py asks Windows for a screen copy itself (CopyFromScreen, BitBlt from the screen DC, ImageGrab, PrintWindow-less desktop grabs), and checks that the
scripts that take pictures of the game use capture_screen.

    test_capture_safety.py        # exit 1 on any failure; editor-free, instant
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys

HERE = _paths.ROOT
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def main():
    risky = re.compile(r"CopyFromScreen|ImageGrab|BitBlt\(")
    offenders = []
    every = []
    for d in [_paths.ROOT] + _paths.FOLDERS:
        if os.path.isdir(d):
            every += [(f, os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".py")]
    for f, fp in every:
        if f in ("window_capture.py", "test_capture_safety.py"):
            continue
        text = open(fp, encoding="utf-8", errors="replace").read()
        for m in risky.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append("%s:%d %s" % (f, line, m.group(0)))
    check("no script outside window_capture.py copies the screen itself (CopyFromScreen / ImageGrab / BitBlt)", not offenders, offenders[:3])
    for f in ("undercroft_playthrough.py", "release_playthrough.py", "transition_frames.py", "loading_screen_test.py"):
        path = _paths.script(f)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        check("%s takes its pictures through capture_screen (the on-top and size check)" % f, "capture_screen" in text, "")
    wc = open(_paths.script("window_capture.py"), encoding="utf-8", errors="replace").read()
    check("window_capture.capture_screen refuses when the game is not on top at the centre and the four corners", "WindowFromPoint" in wc and "is not on top" in wc, "")
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
