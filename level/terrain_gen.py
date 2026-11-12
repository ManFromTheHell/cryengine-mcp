#!/usr/bin/env python3
"""A heightmap generator for ANY level (no editor needed): hills, mountains, an island or flat ground, with flat building platforms and optional erosion, in the layout the engine's heightmap really has.

LAYOUT (measured in the editor): the raw heightmap that `bridge.terrain.get_heights_raw` / `set_heights_raw` move is float32 little-endian, width*height values in metres, and the FIRST index is the
world X: `h.reshape(width, height)[x_index, y_index]`, world position = index * unitSize. The Ashvale fence brush at world (482.5, 302.5) stands at z 35.999 and h[482, 302] is 35.999 while h[302, 482] is 133.3.
Everything here is indexed [x, y]. (make_heightmap.py, the Ashvale script, builds its picture as [row, column] and so its landscape is the transpose of what it describes: harmless there, the level was judged by
how it looked, but not what a caller who says 'x = 100' wants.)

The noise, the domain warp and the droplet erosion are make_heightmap.py's (value noise, ridged multifractal, warping, droplet erosion: see that file for why each is there); this module only sets their grid size
and seed, composes the styles, and cuts the platforms.

    python level/terrain_gen.py [style] [size] [seed]       # prints the statistics of one generated map
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import make_heightmap as MH                  # noqa: E402  (module-level state: N, SEED, rng; set per call below)

STYLES = ("flat", "hills", "mountains", "island")
RELIEF = {"flat": 0.0, "hills": 0.06, "mountains": 0.16, "island": 0.10}      # default height range as a fraction of the terrain's extent (Ashvale: 148 m over 1,024 m = 0.145, mountains)


class TerrainGenError(ValueError):
    pass


def _norm01(a):
    return MH.norm01(a)


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _field(style, n, seed):
    """a 0..1 shape field of n x n, symmetric noise (so [x, y] or [y, x] does not matter) except the range's direction, which is built in [x, y] here"""
    ix, iy = np.meshgrid(np.arange(n, dtype=np.float32), np.arange(n, dtype=np.float32), indexing="ij")     # axis 0 = x
    hills = _norm01(MH.fbm(5, 4, gain=0.5, seed=1)) * 0.75 + _norm01(MH.fbm(4, 12, gain=0.45, seed=2)) * 0.25
    if style == "flat":
        return _norm01(MH.fbm(3, 6, seed=3)) * 0.04                    # a few centimetres of ripple
    if style == "hills":
        return _norm01(hills)
    if style == "island":
        d = np.hypot(ix - 0.5 * n, iy - 0.5 * n) / (0.5 * n)
        mask = (1.0 - _smoothstep((d - 0.45) / 0.55)) ** 1.2
        return _norm01(hills * 0.55 + 0.45) * mask
    # mountains: one bent range authored in [x, y], decorated with ridged noise, warped so the crest wanders
    pts = np.array([(0.10, 0.92), (0.30, 0.70), (0.52, 0.66), (0.72, 0.50), (0.95, 0.36)], np.float32) * n
    dense = []
    for a, b in zip(pts[:-1], pts[1:]):
        for t in np.linspace(0, 1, max(int(np.hypot(*(b - a)) / 2.0), 2), endpoint=False):
            dense.append(a + (b - a) * t)
    dense.append(pts[-1])
    d2 = np.full((n, n), np.inf, np.float32)
    for px, py in np.asarray(dense, np.float32):
        np.minimum(d2, (ix - px) ** 2 + (iy - py) ** 2, out=d2)
    massif = np.clip(np.exp(-((np.sqrt(d2) / (0.20 * n)) ** 2)), 0.0, 1.0) ** (1.0 / 1.6)
    dx, dy = MH.warp(11, 70.0 * n / 1024.0, 3)
    massif = MH.sample_warped(massif, dx, dy)
    dx2, dy2 = MH.warp(21, 55.0 * n / 1024.0, 4)
    ridged = _norm01(MH.sample_warped(MH.fbm(5, 4, ridged=True, seed=31), dx2, dy2)) ** 1.3
    fine = _norm01(MH.fbm(4, 16, gain=0.45, seed=33)) - 0.5
    return _norm01(massif * (0.35 + 0.65 * ridged) + _norm01(hills) * (0.10 + 0.22 * massif) + fine * (0.012 + 0.05 * massif))


def slope_fraction(h, unit, threshold_deg=30.0):
    gx, gy = np.gradient(h, unit)
    return float((np.degrees(np.arctan(np.hypot(gx, gy))) > threshold_deg).mean())


def generate(size=256, unit=1.0, seed=1, style="hills", base_height=20.0, max_height=None, flat_zones=None, erosion=0, ceiling=None):
    """-> float32 array [size, size] of metres, indexed [x, y]. size: samples per side (the terrain is square); unit: metres per sample.
    base_height / max_height: the lowest and the highest ground in metres (a flat style uses base_height; max_height defaults to base_height + RELIEF[style] x the terrain's extent, so a small map is not a cliff); flat_zones: [{"x", "y", "radius", "blend" (default radius/2), "height" (default: the ground's mean there)}] in world metres;
    erosion: droplets (0 = none; about 250 per 1,000 samples is a visible drainage, 25,000 droplets take a few seconds at 256 x 256)."""
    if style not in STYLES:
        raise TerrainGenError("style is one of %s" % ", ".join(STYLES))
    size = int(size)
    if not 16 <= size <= 4096:
        raise TerrainGenError("size is 16 to 4096 samples per side")
    if not unit > 0:
        raise TerrainGenError("unit must be positive")
    if max_height is None:
        max_height = float(base_height) + RELIEF[style] * size * unit + 0.05
    if not (0.0 <= base_height < max_height):
        raise TerrainGenError("need 0 <= base_height < max_height (metres)")
    if int(erosion) < 0 or int(erosion) > 2_000_000:
        raise TerrainGenError("erosion is 0 to 2,000,000 droplets")
    saved = (MH.N, MH.SEED, MH.rng)
    MH.N, MH.SEED, MH.rng = size, int(seed), np.random.default_rng(int(seed))
    try:
        field = _field(style, size, int(seed))
        if style == "flat":
            h = (base_height + field * 1.0).astype(np.float32)
        else:
            h = (base_height + field * (max_height - base_height)).astype(np.float32)
        if int(erosion) > 0:
            span = max(max_height - base_height, 1.0)
            h = MH.erode(h, droplets=int(erosion), batch=min(20000, max(int(erosion), 1)), steps=64, max_edit=0.35 * span / 148.0 + 0.02, max_capacity=6.0)
            k = np.array([1.0, 2.0, 1.0], np.float32)
            k /= k.sum()
            h = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, h)
            h = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, h)
    finally:
        MH.N, MH.SEED, MH.rng = saved
    ix, iy = np.meshgrid(np.arange(size, dtype=np.float32), np.arange(size, dtype=np.float32), indexing="ij")
    for z in (flat_zones or []):
        try:
            zx, zy, r = float(z["x"]), float(z["y"]), float(z["radius"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TerrainGenError("a flat zone needs numbers x, y and radius (world metres): %r" % (z,)) from exc
        blend = float(z.get("blend", r * 0.5))
        d = np.hypot(ix * unit - zx, iy * unit - zy)
        inner = d <= r
        if not inner.any():
            raise TerrainGenError("the flat zone at (%g, %g) radius %g lies outside the terrain (0..%g m)" % (zx, zy, r, size * unit))
        target = float(z["height"]) if z.get("height") is not None else float(h[inner].mean())
        w = np.where(d <= r, 1.0, 1.0 - _smoothstep((d - r) / max(blend, 1e-6)))
        h = (h + (target - h) * w).astype(np.float32)
    top = float(ceiling) if ceiling is not None else float(max(max_height, h.max()))
    h = np.clip(h, 0.0, top).astype(np.float32)
    if not np.isfinite(h).all():
        raise TerrainGenError("the generated heights are not finite")
    return h


def stats(h, unit, water_level=None, flat_zones=None):
    """numbers a caller can judge a map by -> dict"""
    out = {"min": round(float(h.min()), 2), "max": round(float(h.max()), 2), "mean": round(float(h.mean()), 2), "steeper_than_30_degrees_percent": round(100 * slope_fraction(h, unit), 1)}
    if water_level is not None:
        out["below_water_percent"] = round(100.0 * float((h < water_level).mean()), 1)
    zs = []
    ix, iy = np.meshgrid(np.arange(h.shape[0]), np.arange(h.shape[1]), indexing="ij")
    for z in (flat_zones or []):
        inner = np.hypot(ix * unit - float(z["x"]), iy * unit - float(z["y"])) <= float(z["radius"])
        if inner.any():
            zs.append({"x": z["x"], "y": z["y"], "radius": z["radius"], "height": round(float(h[inner].mean()), 2), "spread": round(float(h[inner].std()), 3)})
    if zs:
        out["flat_zones"] = zs
    return out


if __name__ == "__main__":
    style = sys.argv[1] if len(sys.argv) > 1 else "mountains"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    hm = generate(n, 1.0, seed, style, 20.0, None, [{"x": n * 0.3, "y": n * 0.3, "radius": n * 0.06}])
    print(style, n, stats(hm, 1.0, 16.0, [{"x": n * 0.3, "y": n * 0.3, "radius": n * 0.06}]))
