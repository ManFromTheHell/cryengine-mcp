#!/usr/bin/env python3
"""The Sandbox editor's OWN window size, for the tests that take pictures of its viewport.

The picture tests (debug_views, decal_lab, lods_live, pfx_author, render_tour ...) were tuned on an editor window of 1018 x 453: `debug_views.VIEWPORT_CROP` cuts the Perspective pane out of the window capture
for exactly that size and returns any other size WHOLE (with the editor's panels in it), and the noise floors and difference boxes of the tests follow from the pane's pixel size. A restarted editor comes back at
its SAVED window size and monitor (for example 1018 x 763 on a second monitor after a watchdog restart, against 1018 x 453 before): three picture tests failed for that alone ('the pictures differ
in size', a decal control, a LOD box at 1.75 against a noise of 0.88).

So a picture is only taken after `normalize()`: it finds the editor's window (title 'CRYENGINE Sandbox ... Project '...'), sets its SIZE to 1018 x 453 and leaves its position (and monitor) alone, and tells the
caller the size it had. Only that one window is touched, with SetWindowPos (no focus, no z-order change); nothing else about the machine.

    python bridge/editor_window.py            # show the editor window's rectangle
    python bridge/editor_window.py --normalize
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import ctypes.wintypes as wt
import sys
import time

KNOWN_SIZE = (1018, 453)
SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x4, 0x10
SW_RESTORE = 9


def _user32():
    u = ctypes.windll.user32
    try:
        u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))     # per-monitor v2: sizes in physical pixels, as the engine's own window list reports them
    except Exception:                                           # noqa: BLE001 (older Windows)
        pass
    return u


def find():
    """the editor's top-level window handle, or None"""
    u = _user32()
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _l):
        n = u.GetWindowTextLengthW(hwnd)
        if n and u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "CRYENGINE Sandbox" in buf.value and "Project '" in buf.value:
                found.append(hwnd)
        return True
    u.EnumWindows(proc(cb), 0)
    return found[0] if len(found) == 1 else None


def rect():
    """(left, top, right, bottom) of the editor window or None"""
    h = find()
    if not h:
        return None
    r = wt.RECT()
    _user32().GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def size():
    r = rect()
    return None if r is None else (r[2] - r[0], r[3] - r[1])


def set_size(width, height, settle=2.0):
    """Set the editor window's size, position unchanged -> the size it has afterwards (None when there is no window)"""
    h = find()
    if not h:
        return None
    u = _user32()
    if u.IsZoomed(h):
        u.ShowWindow(h, SW_RESTORE)
    u.SetWindowPos(h, 0, 0, 0, int(width), int(height), SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(settle)                                          # the dock layout and the viewport re-settle
    return size()


def normalize(target=KNOWN_SIZE, settle=2.5):
    """Make the window the size the picture tests were tuned on -> {"before": (w, h) | None, "after": (w, h) | None, "changed": bool}"""
    before = size()
    if before is None:
        return {"before": None, "after": None, "changed": False}
    if before == tuple(target):
        return {"before": before, "after": before, "changed": False}
    after = set_size(target[0], target[1], settle)
    return {"before": before, "after": after, "changed": after != before}


_VIEWPORT_QUERY = r'''
import json
from PySide2 import QtWidgets, QtCore
tops = [w for w in QtWidgets.QApplication.topLevelWidgets() if w.isVisible() and "CRYENGINE Sandbox" in w.windowTitle() and "Project '" in w.windowTitle()]
out = {}
if tops:
    m = tops[0]
    # the Perspective pane: the first visible QFrame called Perspective; the 3D surface is the innermost widget of its chain (below the pane's header row)
    frames = [w for w in m.findChildren(QtWidgets.QFrame) if w.objectName() == "Perspective" and w.isVisible() and w.width() > 100]
    if frames:
        w = frames[0]
        while True:
            kids = [k for k in w.children() if isinstance(k, QtWidgets.QWidget) and k.isVisible() and k.width() > 100 and k.height() > 100]
            if not kids:
                break
            w = max(kids, key=lambda k: k.width() * k.height())
        g = w.mapToGlobal(QtCore.QPoint(0, 0))
        out = {"x": g.x(), "y": g.y(), "w": w.width(), "h": w.height(), "frame": [m.frameGeometry().x(), m.frameGeometry().y(), m.frameGeometry().width(), m.frameGeometry().height()]}
print(json.dumps(out))
'''


def viewport_box():
    """The 3D viewport's rectangle inside the editor window capture (left, top, right, bottom in the capture's pixels), asked of the editor's own Qt (the Perspective pane's render surface), or None.
    The capture is of the window rectangle (GetWindowRect), so the box is the surface's global position minus the window's top-left corner."""
    r = rect()
    if r is None:
        return None
    try:
        import json
        import bridge_py
        log = (bridge_py.run_source(_VIEWPORT_QUERY, 30).get("log") or "").strip().splitlines()
        q = json.loads(log[-1]) if log else {}
    except Exception:                                           # noqa: BLE001
        return None
    if not q:
        return None
    x0, y0 = q["x"] - r[0], q["y"] - r[1]
    return (x0, y0, x0 + q["w"], y0 + q["h"])


def main():
    if "--box" in sys.argv:
        print("window", rect(), "viewport box", viewport_box())
        return 0
    if "--normalize" in sys.argv:
        print(normalize())
    else:
        print("editor window:", rect(), size())
    return 0


if __name__ == "__main__":
    sys.exit(main())
