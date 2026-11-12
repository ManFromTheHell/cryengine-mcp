#!/usr/bin/env python3
"""What a player sees: find a game process's window and capture its pixels, 2D UI included.

The engine's own screenshot is taken before the 2D overlays and never shows a menu; PrintWindow
(PW_CLIENTONLY | PW_RENDERFULLCONTENT) returns the window's own content even with another window on top.
Shared by ui_screens.py and ui_flash_test.py.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
user32.SetProcessDPIAware()


def game_hwnd(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        r = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        if p.value == pid and user32.IsWindowVisible(hwnd) and r.right > 300:
            found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def capture(hwnd):
    """PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT) into a 32-bit DIB; returns (w, h, rows of RGB bytes)."""
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    hdc_win = user32.GetDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, ctypes.c_void_p(hbmp))
    user32.PrintWindow(hwnd, hdc, 1 | 2)
    raw = ctypes.string_at(bits, w * h * 4)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(ctypes.c_void_p(hbmp))
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(hwnd, hdc_win)
    rows = [bytes(v for i in range(y * w * 4, (y + 1) * w * 4, 4) for v in (raw[i + 2], raw[i + 1], raw[i]))
            for y in range(h)]
    return w, h, rows


def save_png(path, w, h, rows):
    data = b"".join(b"\x00" + r for r in rows)
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + ch(b"IDAT", zlib.compress(data, 6)) + ch(b"IEND", b""))


def text_fraction(w, h, rows, box):
    """Share of bright, low-saturation pixels (the UI's cream/white text) in box = (x0, y0, x1, y1) fractions."""
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    n = hits = 0
    for y in range(y0, y1, 2):
        r = rows[y]
        for x in range(x0, x1, 2):
            R, G, B = r[3 * x], r[3 * x + 1], r[3 * x + 2]
            n += 1
            if min(R, G, B) > 175 and max(R, G, B) - min(R, G, B) < 70:
                hits += 1
    return hits / max(1, n)


def raise_window(hwnd):
    """Bring the game's own window to the front (the test launched it)."""
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def click(hwnd, x, y, hover=0.4):
    """A real left click at client coordinates (x, y): the cursor is moved there and pressed with mouse_event, so it
    goes through Windows and the engine's own input (the way a player's mouse does). Returns the screen position.
    The game window must be visible and not covered; it moves the mouse cursor for a moment."""
    import time
    pt = wintypes.POINT(int(x), int(y))
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    # Never click into a window that is not the game's: whatever is on top at that point gets the click otherwise
    # (a blind click can land on another application when the game window is covered).
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.WindowFromPoint.restype = ctypes.c_void_p
    top = user32.WindowFromPoint(wintypes.POINT(pt.x, pt.y))
    if not top or user32.GetAncestor(ctypes.c_void_p(top), 2) != user32.GetAncestor(ctypes.c_void_p(hwnd), 2):
        title = ctypes.create_unicode_buffer(120)
        user32.GetWindowTextW(ctypes.c_void_p(user32.GetAncestor(ctypes.c_void_p(top), 2) if top else 0), title, 120)
        raise RuntimeError("the window at (%d, %d) is '%s', not the game -- no click sent" % (pt.x, pt.y, title.value))
    user32.SetCursorPos(pt.x, pt.y)
    time.sleep(hover)                      # a hover first: Flash buttons highlight on mouse-over
    user32.SetCursorPos(pt.x + 1, pt.y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    return pt.x, pt.y


def capture_screen(hwnd, size=None):
    """The window's client area as the desktop shows it (BitBlt from the screen), (w, h, rows of RGB bytes).

    Unlike capture() this needs no help from the window's thread, so it works while the game is busy loading a level
    (PrintWindow asks the window to draw itself, and a loading window does not answer). The window must be on top;
    otherwise RuntimeError and nothing is copied. `size` = (w, h) the client area is expected to have: a window that
    has just been created is full-desktop size and unpainted, so the desktop behind it shows through (the
    on-top test alone can still copy another application that way)."""
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if size is not None and (w, h) != tuple(size):
        raise RuntimeError("the game window is %dx%d, not the expected %dx%d yet: no screen grab taken" % (w, h, size[0], size[1]))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    # A screen grab reads whatever is in front of the game. Refuse (never copy) unless the game is the window on top at
    # the centre and the four corners of its client area; without this check another application's window can be copied.
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.WindowFromPoint.restype = ctypes.c_void_p
    mine = user32.GetAncestor(ctypes.c_void_p(hwnd), 2)
    for fx, fy in ((0.5, 0.5), (0.02, 0.02), (0.98, 0.02), (0.02, 0.98), (0.98, 0.98)):
        top = user32.WindowFromPoint(wintypes.POINT(pt.x + int(w * fx), pt.y + int(h * fy)))
        if not top or user32.GetAncestor(ctypes.c_void_p(top), 2) != mine:
            raise RuntimeError("the game window is not on top at (%.2f, %.2f): no screen grab taken" % (fx, fy))
    hdc_screen = user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, ctypes.c_void_p(hbmp))
    gdi32.BitBlt(hdc, 0, 0, w, h, hdc_screen, pt.x, pt.y, 0x00CC0020 | 0x40000000)     # SRCCOPY | CAPTUREBLT
    raw = ctypes.string_at(bits, w * h * 4)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(ctypes.c_void_p(hbmp))
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(None, hdc_screen)
    rows = [bytes(v for i in range(y * w * 4, (y + 1) * w * 4, 4) for v in (raw[i + 2], raw[i + 1], raw[i]))
            for y in range(h)]
    return w, h, rows
