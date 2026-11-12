#!/usr/bin/env python3
"""The Undercroft's surfaces: nine more procedural, tileable textures for texture_lab.py's pipeline (numpy pictures, the engine's own rc.exe).

    texture_lab_crypt.py [name ...] [--size 512] [--sheet <out.bmp>]

ashlar      dressed stone in courses, the joints staggered, each block its own tone: the old crypt's walls
brick       small red-brown bricks with pale mortar: vaults and the gallery
flagstone   big square slabs, light and dark, a few cracked: floors of the halls
rockface    bedrock in strata with deep fractures: the diggers' cavern
cavefloor   damp dark earth with pebbles and glossy puddles: the cavern and the gallery's floor
mossy       ashlar gone green and wet: the lower passages
bones       a wall of stacked long bones with skulls set in it: the ossuary
marble      pale veined stone with a polish: the vault, the tomb
ironplate   riveted plates, scratched and rusting: doors, bars, braces

They register themselves in texture_lab.SURFACES, so `texture_lab.make` / `compile_rc` treat them like the village's. Output: Assets/textures/ashvale/<name>_diff/_ddna(/_displ).dds.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

import numpy as np

import texture_lab as TL

noise, voronoi, tint, smooth = TL.noise, TL.voronoi, TL.tint, TL.smooth


def _grid(n):
    yy = np.arange(n)[:, None].repeat(n, 1)
    xx = np.arange(n)[None, :].repeat(n, 0)
    return yy, xx


def _courses(n, rows, lo, hi, stagger=True):
    """Per-row block boundaries: -> (row index per pixel, block id per pixel, px to the nearest vertical joint, px to the nearest course line, block-local u in 0..1)."""
    yy, xx = _grid(n)
    rowh = n // rows
    row = np.minimum(yy // rowh, rows - 1)
    ly = yy - row * rowh
    jy = np.maximum(np.minimum(ly, rowh - ly), 0).astype(float)
    block = np.zeros((n, n), np.int32)
    jx = np.zeros((n, n))
    lu = np.zeros((n, n))
    next_id = 0
    for r in range(rows):
        widths, total = [], 0
        while total < n - int(lo * n):
            w = int(TL.rng.uniform(lo, hi) * n)
            widths.append(w)
            total += w
        widths[-1] += n - sum(widths)
        if widths[-1] > hi * n * 1.4:                       # the filler too long: split it
            half = widths[-1] // 2
            widths[-1:] = [half, widths[-1] - half]
        edges = np.concatenate([[0], np.cumsum(widths)])
        off = int(TL.rng.uniform(0, n)) if stagger else 0
        sel = row == r
        x = (xx[sel] - off) % n
        k = np.searchsorted(edges, x, side="right") - 1
        k = np.clip(k, 0, len(widths) - 1)
        w = np.array(widths)[k].astype(float)
        start = edges[k].astype(float)
        u = (x - start) / w
        block[sel] = next_id + k
        jx[sel] = np.minimum(u * w, (1 - u) * w)
        lu[sel] = u
        next_id += len(widths)
    return row, block, jx, jy, lu


def surf_ashlar(n):
    row, block, jx, jy, lu = _courses(n, 8, 0.17, 0.40)
    edge = np.clip(np.minimum(jx, jy) / 3.5, 0, 1)
    per = TL.rng.uniform(-0.075, 0.075, 512)[block % 512]
    stain = noise(n, 4, 0.6, 3)
    pit = noise(n, 4, 0.8, 26)
    chip = noise(n, 3, 0.9, 40)
    base = np.array([0.52, 0.50, 0.46])
    rgb = tint(base, per + (stain - 0.5) * 0.12 + (pit - 0.5) * 0.07)
    # a darker band under each course joint and soot streaks falling from the top of the tile
    rgb *= (0.9 + 0.1 * np.clip(np.minimum(jx, jy) / 14.0, 0, 1))[..., None]
    mortar = np.array([0.30, 0.29, 0.26])
    rgb = rgb * edge[..., None] + mortar * (1 - edge[..., None])
    chipped = np.clip(1.0 - (np.minimum(jx, jy) / 9.0) + (chip - 0.5) * 1.4, 0, 1) * 0.35
    h = edge * 0.62 + pit * 0.22 + (1.0 - chipped) * 0.16
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.12 + 0.07 * pit, 3.6


def surf_brick(n):
    rows = 16
    yy, xx = _grid(n)
    rowh = n // rows
    row = np.minimum(yy // rowh, rows - 1)
    ly = yy - row * rowh
    per_row = 8
    bw = n // per_row
    off = (row % 2) * (bw // 2)
    x = (xx + off) % n
    col = x // bw
    lx = x - col * bw
    jx = np.minimum(lx, bw - lx).astype(float)
    jy = np.maximum(np.minimum(ly, rowh - ly), 0).astype(float)
    edge = np.clip(np.minimum(jx / 2.2, jy / 1.8), 0, 1)
    brick_id = (row * per_row + col) % 512
    tone = TL.rng.uniform(-0.07, 0.07, 512)[brick_id]
    burnt = (TL.rng.random(512)[brick_id] > 0.86) * -0.10
    pit = noise(n, 4, 0.8, 30)
    stain = noise(n, 3, 0.6, 3)
    base = np.array([0.43, 0.23, 0.17])
    rgb = tint(base, tone + burnt + (pit - 0.5) * 0.08 + (stain - 0.5) * 0.06)
    rgb[..., 1] *= 0.95
    mortar = np.array([0.52, 0.49, 0.43])
    rgb = rgb * edge[..., None] + mortar * (1 - edge[..., None])
    h = edge * 0.75 + pit * 0.2 + tone * 0.2
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.08 + 0.05 * pit, 3.4


def surf_flagstone(n):
    slabs = 4
    yy, xx = _grid(n)
    sw = n // slabs
    i = xx // sw
    j = yy // sw
    lx = xx - i * sw
    ly = yy - j * sw
    jd = np.minimum(np.minimum(lx, sw - lx), np.minimum(ly, sw - ly)).astype(float)
    edge = np.clip(jd / 3.0, 0, 1)
    sid = (i + j * slabs) % 64
    check = ((i + j) % 2).astype(float)
    per = TL.rng.uniform(-0.05, 0.05, 64)[sid]
    wear = noise(n, 4, 0.6, 5)
    grain = noise(n, 3, 0.8, 30)
    d1, _, d2 = voronoi(n, 3, 1.0)
    cracked = (TL.rng.random(64)[sid] > 0.7)
    crack = np.clip(1.0 - (d2 - d1) * 90.0, 0, 1) * cracked * 0.5
    light, dark = np.array([0.56, 0.54, 0.50]), np.array([0.36, 0.35, 0.33])
    base = light[None, None, :] * (1 - check[..., None]) + dark[None, None, :] * check[..., None]
    rgb = np.clip(base + (per + (wear - 0.5) * 0.12 + (grain - 0.5) * 0.06)[..., None], 0, 1)
    rgb = rgb * edge[..., None] * (1 - 0.6 * crack[..., None]) + np.array([0.18, 0.17, 0.15]) * (1 - edge[..., None])
    h = edge * 0.55 + grain * 0.2 + wear * 0.15 - crack * 0.5 + 0.1
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.16 + 0.12 * wear, 2.6


def surf_rockface(n):
    yy, xx = _grid(n)
    warp = noise(n, 3, 0.6, 3)
    warp2 = noise(n, 3, 0.6, 5, aniso=(1.0, 0.3))
    saw = (yy / n * 9.0 + warp * 1.6 + warp2 * 0.5) % 1.0          # each stratum: from its foot (0) up to its lip (1), then the next one begins
    ledge = saw ** 1.6                                               # most of the height is a gentle face, the lip a sharp step
    d1, ids, d2 = voronoi(n, 6, 1.0)
    crack = np.clip(1.0 - (d2 - d1) * 38.0, 0, 1) * (noise(n, 3, 0.6, 4) > 0.42)      # fractures, not everywhere
    big = noise(n, 4, 0.6, 2)
    fine = noise(n, 4, 0.8, 28)
    per = TL.rng.uniform(-0.05, 0.05, 64)[ids % 64]
    base = np.array([0.33, 0.30, 0.26])
    rgb = tint(base, per + (big - 0.5) * 0.12 + (fine - 0.5) * 0.09)
    rgb[..., 0] += 0.02 * (1 - saw)
    rgb *= (0.55 + 0.45 * ledge)[..., None] * (1 - 0.7 * crack[..., None])
    h = ledge * 0.5 + big * 0.18 + fine * 0.22 - crack * 0.3
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.07 + 0.08 * fine, 4.8


def surf_cavefloor(n):
    tone = noise(n, 5, 0.6, 3)
    fine = noise(n, 3, 0.8, 40)
    pb, pid, _ = voronoi(n, 13, 1.0)
    peb = (pb < 0.02) & (TL.rng.random(256)[pid % 256] > 0.5)
    low = noise(n, 3, 0.6, 2)
    puddle = np.clip((0.34 - low) * 9.0, 0, 1)                          # the low places hold water
    base = np.array([0.22, 0.18, 0.14])
    rgb = tint(base, (tone - 0.5) * 0.12 + (fine - 0.5) * 0.06)
    rgb[peb] = rgb[peb] * 0.5 + np.array([0.42, 0.40, 0.36]) * 0.5
    rgb *= (1.0 - 0.55 * puddle[..., None])
    h = fine * 0.35 + tone * 0.35 + peb * 0.4 - puddle * 0.3
    gloss = 0.06 + 0.05 * fine + puddle * 0.85
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), np.clip(gloss, 0, 1), 2.2


def surf_mossy(n):
    rgb, h, gloss, bump = surf_ashlar(n)
    m = noise(n, 4, 0.6, 3)
    fluff = noise(n, 3, 0.8, 22)
    moss = np.clip((m - 0.47) * 7.0, 0, 1)
    green = np.array([0.16, 0.25, 0.09]) + (fluff[..., None] - 0.5) * np.array([0.05, 0.08, 0.03])
    rgb = rgb * (1 - 0.88 * moss[..., None]) * 0.85 + green * (0.88 * moss[..., None])
    h = np.clip(h + moss * fluff * 0.25, 0, 1)
    return np.clip(rgb, 0, 1), h, np.clip(gloss + moss * 0.18, 0, 1), 3.2


def surf_bones(n):
    rows = 12
    yy, xx = _grid(n)
    rowh = n // rows
    row = np.minimum(yy // rowh, rows - 1)
    fy = np.clip((yy - row * rowh + 0.5) / rowh, 0.0, 1.0)
    profile = np.sin(np.pi * fy) ** 0.7
    shade = np.zeros((n, n))
    rod = np.zeros((n, n))
    tone = np.zeros((n, n))
    for r in range(rows):
        sel = row == r
        cursor = int(TL.rng.uniform(0, n * 0.3))
        while cursor < n + n // 2:
            ln = int(TL.rng.uniform(0.34, 0.62) * n)
            gap = int(TL.rng.uniform(1, 5))
            x = (xx[sel] - cursor) % n
            inside = x < ln
            tx = np.minimum(x, ln - x).astype(float) / (rowh * 0.55)
            cap = np.clip(tx, 0, 1) ** 0.5
            t = TL.rng.uniform(-0.06, 0.06)
            sub = np.where(inside, cap, 0.0)
            cur = rod[sel]
            take = sub > cur
            cur = np.where(take, sub, cur)
            rod[sel] = cur
            tone_sel = tone[sel]
            tone_sel[take] = t
            tone[sel] = tone_sel
            cursor += ln + gap
    rod = rod * profile
    # skulls: a few round ones set into the wall
    skull = np.zeros((n, n))
    eyes = np.zeros((n, n))
    for _ in range(7):
        cx, cy = TL.rng.uniform(0, n), TL.rng.uniform(0, n)
        r0 = n * 0.058
        dx = np.minimum(np.abs(xx - cx), n - np.abs(xx - cx))
        dy = np.minimum(np.abs(yy - cy), n - np.abs(yy - cy))
        cran = ((dx / r0) ** 2 + (((cy - yy) if True else 0) / (r0 * 1.05)) ** 2) < 1.0
        cran = (dx * dx + dy * dy < r0 * r0) & (yy - cy < r0 * 0.35)
        jaw = (dx < r0 * 0.55) & (yy - cy >= r0 * 0.35) & (yy - cy < r0 * 0.95)
        skull = np.maximum(skull, (cran | jaw).astype(float))
        for sx in (-1, 1):
            ex = np.minimum(np.abs(xx - (cx + sx * r0 * 0.38)), n - np.abs(xx - (cx + sx * r0 * 0.38)))
            eyes = np.maximum(eyes, ((ex * ex + (yy - (cy - r0 * 0.05)) ** 2) < (r0 * 0.22) ** 2).astype(float))
        nose = (dx < r0 * 0.1) & (np.abs(yy - (cy + r0 * 0.25)) < r0 * 0.2)
        eyes = np.maximum(eyes, nose.astype(float))
    fine = noise(n, 3, 0.8, 34)
    solid = np.maximum(rod, skull * 0.95) * (1 - eyes)
    cream = np.array([0.72, 0.67, 0.54])
    rgb = tint(cream, tone + (fine - 0.5) * 0.10) * (0.55 + 0.45 * np.clip(solid * 1.2, 0, 1))[..., None]
    void = np.array([0.045, 0.038, 0.032])
    cov = np.clip(solid * 3.0, 0, 1)
    rgb = rgb * cov[..., None] + void * (1 - cov[..., None])
    h = solid * 0.8 + fine * 0.1
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.2 + 0.1 * fine, 3.8


def surf_marble(n):
    yy, xx = _grid(n)
    t = smooth(noise(n, 2, 0.5, 2), 3)
    t2 = smooth(noise(n, 2, 0.5, 3), 3)
    s1 = np.sin(2 * np.pi * (1.0 * xx / n + 1.0 * yy / n) + t * 5.0)                 # long swirling veins: thin lines where the sine crosses zero
    s2 = np.sin(2 * np.pi * (2.0 * xx / n - 1.0 * yy / n) + t2 * 4.0)
    vein = np.clip(np.exp(-(s1 * s1) * 55.0) + 0.55 * np.exp(-(s2 * s2) * 90.0), 0, 1)
    cloud = noise(n, 3, 0.5, 2)
    base = np.array([0.80, 0.79, 0.75])
    rgb = tint(base, (cloud - 0.5) * 0.07)
    rgb = rgb * (1 - 0.7 * vein[..., None]) + np.array([0.38, 0.39, 0.43]) * (0.7 * vein[..., None])
    h = 0.5 + (cloud - 0.5) * 0.1 - vein * 0.05
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.65 + 0.2 * (1 - vein), 0.4


def surf_ironplate(n):
    cols, rows = 2, 3
    yy, xx = _grid(n)
    pw, ph = n // cols, n // rows
    i, j = xx // pw, yy // ph
    lx, ly = xx - i * pw, yy - j * ph
    jd = np.minimum(np.minimum(lx, pw - lx), np.minimum(ly, ph - ly)).astype(float)
    edge = np.clip(jd / 3.0, 0, 1)
    pid = (i + j * cols) % 16
    per = TL.rng.uniform(-0.03, 0.03, 16)[pid]
    rivets = np.zeros((n, n))
    r0 = n * 0.012
    for ci in range(cols):
        for rj in range(rows):
            for fx in (0.10, 0.5, 0.90):
                for fy in (0.12, 0.88):
                    cx, cy = ci * pw + fx * pw, rj * ph + fy * ph
                    dx, dy = xx - cx, yy - cy
                    rivets = np.maximum(rivets, np.clip(1.0 - np.sqrt(dx * dx + dy * dy) / r0, 0, 1))
    scratch = noise(n, 4, 0.7, 40, aniso=(1.0, 0.04))
    rustm = noise(n, 4, 0.6, 5)
    rust = np.clip((rustm - 0.56) * 6.0, 0, 1)
    base = np.array([0.15, 0.15, 0.17])
    rgb = tint(base, per + (scratch - 0.5) * 0.06)
    rgb = rgb * (1 - rust[..., None]) + np.array([0.36, 0.17, 0.07]) * rust[..., None]
    rgb *= (0.55 + 0.45 * edge)[..., None]
    rgb += rivets[..., None] * 0.07
    h = edge * 0.4 + rivets * 0.5 + scratch * 0.08 + rust * 0.05
    gloss = 0.40 * (1 - rust) + 0.05 * rust
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), gloss, 3.0


NEW = {"ashlar": surf_ashlar, "brick": surf_brick, "flagstone": surf_flagstone, "rockface": surf_rockface, "cavefloor": surf_cavefloor, "mossy": surf_mossy,
       "bones": surf_bones, "marble": surf_marble, "ironplate": surf_ironplate}
TILES = {"ashlar": 2.4, "brick": 1.8, "flagstone": 2.4, "rockface": 3.0, "cavefloor": 3.0, "mossy": 2.4, "bones": 1.6, "marble": 2.0, "ironplate": 1.2}
SMOOTH = {"ashlar": 2, "brick": 2, "flagstone": 2, "rockface": 2, "cavefloor": 2, "mossy": 2, "bones": 2, "marble": 4, "ironplate": 1}
STRENGTH = {"ashlar": 2.4, "brick": 2.2, "flagstone": 1.4, "rockface": 3.0, "cavefloor": 1.4, "mossy": 2.2, "bones": 2.4, "marble": 0.2, "ironplate": 1.6}
HEIGHT_MAPS = ("ashlar", "brick", "flagstone", "rockface", "cavefloor", "mossy", "bones", "ironplate")

TL.SURFACES.update(NEW)
TL.TILE_METRES.update(TILES)
TL.BUMP_SMOOTH.update(SMOOTH)
TL.BUMP_STRENGTH.update(STRENGTH)
TL.DISPL_SURFACES = tuple(TL.DISPL_SURFACES) + tuple(n for n in HEIGHT_MAPS if n not in TL.DISPL_SURFACES)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    size = 512
    if "--size" in sys.argv:
        size = int(sys.argv[sys.argv.index("--size") + 1])
        args = [a for a in args if a != str(size)]
    sheet = None
    if "--sheet" in sys.argv:
        sheet = sys.argv[sys.argv.index("--sheet") + 1]
        args = [a for a in args if a != sheet]
    names = [a for a in args if a in NEW] or list(NEW)
    TL.SIZE = size
    if sheet:
        TL.contact_sheet(names, sheet)
        print("contact sheet:", sheet)
        return
    for nm in names:
        TL.make(nm, size)
        print("made", nm, "(%d px), tile %.1f m" % (size, TILES[nm]))
    TL.compile_rc(names, TL.OUT, displ=[n for n in names if n in HEIGHT_MAPS])


if __name__ == "__main__":
    main()
