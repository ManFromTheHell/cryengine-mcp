#!/usr/bin/env python3
"""Pictures of the Undercroft from the editor viewport, eye and target in the dungeon's LOCAL metres (undercroft_kit.O is added).

    uc_shots.py <out dir> [name ...] [--sheets]   names from VIEWS (default: all); writes <name>.png and prints the mean luminance; --sheets also makes contact sheets of four

The viewport is the editor's own (bridge.viewport.screenshot); a dark room has a low saturation, so the shooter accepts frames by a low bar and the result
is judged by the picture and its numbers (mean, share of near-black and of clipped pixels), not by the shooter's word.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import subprocess
import sys

import debug_views as DV
import undercroft_kit as K
import shutil

HERE = _paths.ROOT
DV.MIN_STRUCTURE = 2.0           # a dark corner is legitimately nearly flat

VIEWS = {
    "hall": ((1.5, 1.5, 1.7), (8.0, 8.0, 1.5)),
    "stair": ((2.0, 5.0, 1.7), (9.0, 8.7, 3.2)),
    "gallery": ((11.5, 5.0, 1.7), (28.0, 5.0, 0.9)),
    "cells": ((17.5, 5.0, 1.6), (17.5, 0.5, 1.0)),
    "alcove": ((15.0, 5.5, 1.6), (15.0, 9.0, 0.8)),
    "shoring": ((23.0, 5.0, 0.7), (29.5, 5.0, 0.4)),
    "cavern": ((30.5, 5.0, 0.95), (44.0, 6.0, -1.3)),
    "cavern_back": ((47.0, 5.0, -0.5), (33.0, 8.0, 0.0)),
    "passage": ((48.5, 5.0, -0.55), (53.0, 5.0, -1.8)),
    "bones": ((54.5, 5.0, -1.3), (70.0, 6.0, -2.5)),
    "pit": ((58.0, 6.0, -1.3), (65.0, 6.0, -4.0)),
    "coffins": ((62.0, 0.5, -1.3), (58.0, 12.5, -2.4)),
    "tunnel": ((64.5, 15.5, -1.3), (64.5, 20.0, -1.5)),
    "bend": ((68.0, 20.5, -1.3), (72.0, 20.5, -1.5)),
    "hall_g": ((64.5, 23.5, -1.3), (64.5, 38.0, -1.0)),
    "balcony": ((55.0, 25.0, -1.3), (55.5, 36.0, -0.2)),
    "dais": ((60.0, 33.0, -1.3), (74.5, 32.0, -0.3)),
    "vault_door": ((64.5, 35.0, -1.3), (64.5, 41.5, -1.5)),
    "vault": ((64.5, 43.5, -1.3), (64.5, 52.0, -2.2)),
    "vault_b": ((60.0, 44.0, -1.3), (66.0, 52.0, -2.4)),
}


def sheets(out, names):
    """Contact sheets of 4 views each (2 x 2, the order of `names`): fewer pictures to look at."""
    import shot_compare, window_capture
    for s in range(0, len(names), 4):
        imgs = [shot_compare.load(os.path.join(out, n + ".png")) for n in names[s:s + 4]]
        h, w = imgs[0].shape[:2]
        imgs = [im for im in imgs if im.shape[:2] == (h, w)]
        rows = [bytearray(2 * w * 3) for _ in range(2 * h)]
        for k, im in enumerate(imgs):
            ox, oy = (k % 2) * w, (k // 2) * h
            for y in range(h):
                rows[oy + y][ox * 3:(ox + w) * 3] = bytes(im[y, :, :3].astype("uint8").tobytes())
        path = os.path.join(out, "sheet_%d.png" % (s // 4))
        window_capture.save_png(path, 2 * w, 2 * h, [bytes(r) for r in rows])
        print("SHEET|%s|%s" % (path, ", ".join(names[s:s + 4])))


def main():
    out = sys.argv[1]
    names = [a for a in sys.argv[2:] if a in VIEWS] or list(VIEWS)
    os.makedirs(out, exist_ok=True)
    for n in names:
        eye, tgt = VIEWS[n]
        e = [K.O[i] + eye[i] for i in range(3)]
        t = [K.O[i] + tgt[i] for i in range(3)]
        pic = DV.shoot("uc_" + n, e, t, 0.2)
        png = os.path.join(out, n + ".png")
        shutil.copyfile(pic if isinstance(pic, str) else pic["picture"], png)
        import numpy as np
        import shot_compare
        a = shot_compare.load(png).astype(float)
        lum = a.mean(axis=2)
        print("%-14s mean luma %.1f, std %.1f, share under 6: %.0f%%, over 245: %.1f%%  %s" % (n, lum.mean(), lum.std(), 100 * (lum < 6).mean(), 100 * (lum > 245).mean(), png))
    if "--sheets" in sys.argv:
        sheets(out, names)


if __name__ == "__main__":
    main()
