#!/usr/bin/env python3
"""A light cookie (the texture a Projector Light throws) made and compiled the engine's way.

    light_cookie.py [name]            default pg_window_cookie -> Assets/textures/lights/<name>.dds

The manual ('Light Entities', row Texture): 512x512, no alpha channel, RC preset LightProjector (rc.ini: maxtexturesize 512, BC4, power of two, mipmaps, sRGB). RC chooses that preset by the
file-name masks only for some names; this image is none of them, so the preset is written INTO the TIFF (CryTIF's special-instructions block, texture_lab.crytif_preset) and Crytek's own rc.exe
compiles it. The picture is a window: a 2 x 2 pane cross with a dark frame and mullions, bright inside, soft edges, so a projector throws a bright window with a dark cross on the floor.
The compiled format is read back from the DDS header (BC4 expected: fourcc 'BC4U' or DXGI 80).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import struct
import subprocess
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import texture_lab as T  # noqa: E402

OUT = os.path.join(T.PROJECT, "Assets", "textures", "lights")


def window(n=512):
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    u, v = x / (n - 1), y / (n - 1)
    # panes: the unit square inset by a frame, split by a cross; soft edges (a few px) so the projected pattern is not aliased
    def band(c, half, soft):
        return np.clip((half - np.abs(c)) / soft + 0.5, 0.0, 1.0)
    inside = band(u - 0.5, 0.40, 0.012) * band(v - 0.5, 0.40, 0.012)
    cross = 1.0 - np.maximum(band(u - 0.5, 0.035, 0.012), band(v - 0.5, 0.035, 0.012))
    return inside * cross


def main(name="pg_window_cookie"):
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise SystemExit("name must be a bare identifier (letters, digits, underscore): it becomes a file name")
    os.makedirs(T.SRC, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    img = T.to8(window())
    rgb = np.dstack([img, img, img])
    tif = os.path.join(T.SRC, name + ".tif")
    T.write_tiff(tif, rgb, None, preset="LightProjector")
    dds = os.path.join(T.SRC, name + ".dds")
    if os.path.isfile(dds):
        os.remove(dds)
    proc = subprocess.run([T.RC, name + ".tif", "/refresh"], cwd=T.SRC, capture_output=True, text=True, errors="replace")
    errors = [l for l in proc.stdout.splitlines() if l.startswith("[E]")]
    if not os.path.isfile(dds) or proc.returncode != 0:
        raise SystemExit("rc.exe failed: %s" % "; ".join(errors)[:300])
    data = open(dds, "rb").read()
    fourcc = data[84:88]
    dxgi = struct.unpack("<I", data[128:132])[0] if fourcc == b"DX10" else None
    w, h = struct.unpack("<I", data[16:20])[0], struct.unpack("<I", data[12:16])[0]
    mips = struct.unpack("<I", data[28:32])[0]
    import shutil
    shutil.copyfile(dds, os.path.join(OUT, name + ".dds"))
    print("wrote", os.path.join(OUT, name + ".dds"), len(data), "bytes; %dx%d, mips %d, fourcc %r, dxgi %s (80 = BC4_UNORM)" % (w, h, mips, fourcc, dxgi))
    return os.path.join(OUT, name + ".dds")


if __name__ == "__main__":
    main(*(sys.argv[1:2]))
