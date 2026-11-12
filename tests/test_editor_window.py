#!/usr/bin/env python3
"""Self-test of editor_window.py: the editor window's size and the viewport box the picture tests crop to (the box comes from the editor's own Qt, so it follows whatever layout and size a restarted editor
comes back with). Needs the editor window; it never resizes it (normalize() is only called with the size the window already has).

    test_editor_window.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import struct
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_window as W

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def main():
    r = W.rect()
    if r is None:
        print("  SKIPPED: no editor window (this test needs the open editor); no checks were run")
        sys.exit(0)
    sz = W.size()
    check("the editor window is found and its size is its rectangle's width and height", sz == (r[2] - r[0], r[3] - r[1]) and sz[0] > 600 and sz[1] > 300, (r, sz))
    before = W.rect()
    res = W.normalize(target=sz, settle=0.1)
    check("normalize to the size the window already has changes nothing (no resize, no move)", res["changed"] is False and W.rect() == before, res)
    box = W.viewport_box()
    check("the viewport box is asked of the editor's own Qt and lies inside the window capture", box is not None and 0 <= box[0] < box[2] <= sz[0] and 0 <= box[1] < box[3] <= sz[1], (box, sz))
    if box is None:
        print("\n%d checks, %d failed" % (len(results), results.count(False)))
        sys.exit(1)
    check("the box is a 3D view: wide and tall enough, and leaves room for the editor's own menu and toolbars above it", box[2] - box[0] > 300 and box[3] - box[1] > 200 and box[1] > 60, box)
    import debug_views as D
    p = D.shoot("zz_mcp_window_probe")
    try:
        w, h = struct.unpack(">II", open(p, "rb").read()[16:24])
        known = D.VIEWPORT_CROP.get(sz)
        want = known if known else box
        check("a viewport picture taken through debug_views.shoot is cropped to exactly that box (%dx%d)" % (want[2] - want[0], want[3] - want[1]), (w, h) == (want[2] - want[0], want[3] - want[1]), (w, h))
    finally:
        for f in (os.listdir(os.path.join(HERE, "job_runs")) if os.path.isdir(os.path.join(HERE, "job_runs")) else []):
            if f.startswith("zz_mcp_window_probe"):
                os.remove(os.path.join(HERE, "job_runs", f))
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
