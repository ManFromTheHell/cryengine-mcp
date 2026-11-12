#!/usr/bin/env python3
"""Colour-grading chart (a 16x16x16 lookup table stored as a 256x16 texture) made from the ENGINE'S OWN identity chart.

    grading_chart.py <name> [--saturation S] [--tint R,G,B] [--gamma G] [--lift L] [--out folder]

The engine ships the identity chart as a plain uncompressed 256x16 DDS (`EngineAssets/Textures/default_cch.dds`, 16,512 bytes, X8R8G8B8, inside engine.pak). Pixel (x, y) holds the input colour
R = x mod 16, G = y, B = x div 16 (each step 17 of 255). A graded chart is the same file whose pixel values went through a colour function: the renderer looks the scene colour up in it
(CColorGradingStage, 'CombineColorGradingWithColorChart'). So this tool reads the identity chart from the pak, applies the function to the colour each pixel stands for and writes the
result with the SAME header. The manual's way (paste RGBChart.bmp into a screenshot, grade it in Photoshop, RC's ColorChart preset extracts it) needs an attachment that is not in the offline
manual; the result is the same kind of file. Nothing is downloaded; the tool is read-only on the engine.

The output goes to Assets/<out>/<name>_cch.dds (default out textures/grading). The renderer reads it with `r_ColorGradingChartImage <path>` or the flow node Image:ColorGradient
(tex_TexturePath, TransitionTime) and needs r_ColorGrading / r_ColorGradingCharts on.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import os
import struct
import sys

import numpy as np

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
sys.path.insert(0, HERE)
import pakread  # noqa: E402

# CRYENGINE_ENGINE_DIR (or CRYENGINE_ENGINE): the installed engine folder; CRYENGINE_ENGINE_PAK points straight at its engine.pak
_ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
ENGINE_PAK = os.environ.get("CRYENGINE_ENGINE_PAK") or os.path.join(_ENGINE, "engine", "engine.pak")
IDENTITY = "EngineAssets/Textures/default_cch.dds"


def identity():
    """(header bytes, 16 x 256 x 3 float array of the identity chart's colours R,G,B in 0..1)."""
    if not os.path.isfile(ENGINE_PAK):
        raise SystemExit("engine.pak not found at %s: set CRYENGINE_ENGINE_DIR to the installed engine folder (or CRYENGINE_ENGINE_PAK)" % ENGINE_PAK)
    data = pakread.read(ENGINE_PAK, IDENTITY)
    if data[:4] != b"DDS " or len(data) != 128 + 256 * 16 * 4:
        raise RuntimeError("the engine's identity chart is not the expected 256x16 X8R8G8B8 DDS (%d bytes)" % len(data))
    px = np.frombuffer(data[128:], dtype=np.uint8).reshape(16, 256, 4)
    rgb = px[:, :, [2, 1, 0]].astype(np.float64) / 255.0           # file order is B, G, R, A
    return data[:128], rgb


def grade(rgb, saturation=1.0, tint=(1.0, 1.0, 1.0), gamma=1.0, lift=0.0):
    lum = (rgb * np.array([0.2126, 0.7152, 0.0722])).sum(axis=2, keepdims=True)
    out = lum + (rgb - lum) * saturation
    out = out * np.array(tint)
    out = np.clip(out, 0.0, 1.0) ** gamma
    out = lift + out * (1.0 - lift)
    return np.clip(out, 0.0, 1.0)


def write(path, header, rgb):
    px = np.zeros((16, 256, 4), dtype=np.uint8)
    q = np.rint(rgb * 255.0).astype(np.uint8)
    px[:, :, 0], px[:, :, 1], px[:, :, 2], px[:, :, 3] = q[:, :, 2], q[:, :, 1], q[:, :, 0], 255
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header + px.tobytes())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--saturation", type=float, default=1.0)
    ap.add_argument("--tint", default="1,1,1")
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--lift", type=float, default=0.0)
    ap.add_argument("--out", default="textures/grading")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing chart of that name")
    a = ap.parse_args(argv)
    header, rgb = identity()
    tint = tuple(float(v) for v in a.tint.split(","))
    graded = grade(rgb, a.saturation, tint, a.gamma, a.lift)
    if not a.name or any(c in a.name for c in "/\\:") or ".." in a.name:
        raise SystemExit("name must be a bare file name, no path separators")
    assets = os.path.realpath(os.path.join(PROJECT, "Assets"))
    path = os.path.realpath(os.path.join(assets, a.out.replace("/", os.sep), a.name + "_cch.dds"))
    if os.path.commonpath([assets, path]) != assets:
        raise SystemExit("--out must stay under the project's Assets folder")
    if os.path.exists(path) and not a.overwrite:
        raise SystemExit("exists: %s (pass --overwrite to replace it)" % path)
    write(path, header, graded)
    # read back: the corners of the cube (black, white, pure red, pure blue) and the mean
    back = np.frombuffer(open(path, "rb").read()[128:], dtype=np.uint8).reshape(16, 256, 4)
    corner = lambda x, y: tuple(int(v) for v in back[y, x][[2, 1, 0]])
    print("wrote", path, os.path.getsize(path), "bytes")
    print("black", corner(0, 0), "white", corner(255, 15), "red", corner(15, 0), "blue", corner(240, 0),"mean RGB", tuple(round(float(v), 1) for v in back[:, :, [2, 1, 0]].reshape(-1, 3).mean(axis=0)))
    return path


if __name__ == "__main__":
    main()
