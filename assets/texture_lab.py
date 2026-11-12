#!/usr/bin/env python3
"""Procedural, tileable surface textures for Ashvale, compiled by CRYENGINE's own Resource Compiler.

    texture_lab.py [name ...] [--out Assets/textures/ashvale] [--size 512] [--sheet]

For each surface it writes the three maps the Illum shader uses, as TIFFs named the way rc.ini's presets expect
    <name>_diff.tif   albedo, sRGB      (preset Albedo: BC1 + mipmaps)
    <name>_ddna.tif   normal map, gloss in the alpha channel (preset NormalsWithSmoothness: BC5s + BC4)
and runs rc.exe on them (the engine's compiler, not our own) so Assets/textures/ashvale holds the .dds files. The TIFFs stay in
texture_src/. Every texture is TILEABLE (all noise is built in the frequency domain, periodic by construction), so
a face mapped in metres (mesh_fbx.py) repeats seamlessly; TILE_METRES says how many metres one texture covers.

Surfaces: plaster, timber, thatch, stone, slate, planks, cobble, dirtroad, cloth, bread/gold are flat colours (materials only).
No image library is needed: numpy builds the pictures and a 60-line writer makes baseline TIFFs.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# the engine install: env CRYENGINE_ENGINE_DIR (or CRYENGINE_ENGINE), else the Launcher's default 5.7.1 folder
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
RC = os.path.join(ENGINE, "Tools", "rc", "rc.exe")
SRC = os.path.join(HERE, "texture_src")
OUT = os.path.join(PROJECT, "Assets", "textures", "ashvale")
SIZE = 512
rng = np.random.default_rng(1348)

# how many metres one tile covers (the mesh UVs are metres / tile)
TILE_METRES = {"plaster": 2.0, "timber": 1.0, "thatch": 1.5, "stone": 1.5, "slate": 1.2, "planks": 1.0, "cobble": 2.0, "dirtroad": 3.0, "cloth": 1.0, "bark": 1.0}


# ---------------------------------------------------------------- noise (periodic)
def noise(n, octaves=5, persistence=0.55, lo=2, aniso=(1.0, 1.0), seed_offset=0):
    """Tileable fractal noise in [0, 1]: random-phase spectrum with a 1/f^k falloff, inverse FFT."""
    fy = np.fft.fftfreq(n)[:, None] * n / aniso[0]
    fx = np.fft.fftfreq(n)[None, :] * n / aniso[1]
    r = np.sqrt(fx * fx + fy * fy)
    r[0, 0] = 1.0
    amp = np.zeros_like(r)
    for o in range(octaves):
        f = lo * (2 ** o)
        amp += (persistence ** o) * np.exp(-((r - f) ** 2) / (2 * (0.45 * f) ** 2))
    phase = rng.uniform(0, 2 * np.pi, (n, n))
    spec = amp * np.exp(1j * phase)
    spec[0, 0] = 0
    img = np.fft.ifft2(spec).real
    img -= img.min()
    img /= max(img.max(), 1e-9)
    return img


def smooth(img, k=1):
    for _ in range(k):
        img = (np.roll(img, 1, 0) + np.roll(img, -1, 0) + np.roll(img, 1, 1) + np.roll(img, -1, 1) + 4 * img) / 8
    return img


def voronoi(n, cells, jitter=0.9):
    """Tileable Voronoi: (distance to the nearest point, id of the nearest point, distance to the second nearest)."""
    gy, gx = np.mgrid[0:n, 0:n] / n
    pts = []
    for j in range(cells):
        for i in range(cells):
            pts.append(((i + 0.5 + (rng.random() - 0.5) * jitter) / cells, (j + 0.5 + (rng.random() - 0.5) * jitter) / cells))
    best = np.full((n, n), 9.0)
    second = np.full((n, n), 9.0)
    bid = np.zeros((n, n), np.int32)
    for k, (px, py) in enumerate(pts):
        dx = np.abs(gx - px)
        dx = np.minimum(dx, 1 - dx)
        dy = np.abs(gy - py)
        dy = np.minimum(dy, 1 - dy)
        d = np.sqrt(dx * dx + dy * dy)
        closer = d < best
        second = np.where(closer, best, np.minimum(second, d))
        bid = np.where(closer, k, bid)
        best = np.where(closer, d, best)
    return best, bid, second


def normal_from_height(h, strength, size=None):
    """Tangent-space normal (x, y, z in [-1, 1]) from a height map, periodic differences. `size`: the picture's side in px (default SIZE)."""
    size = size or SIZE
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5 * size * strength
    dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5 * size * strength
    n = np.dstack([-dx, dy, np.ones_like(h)])      # +y of the picture is down; the engine's V runs up: flip
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n


def tint(base, variation):
    return np.clip(np.dstack([base[0] + variation, base[1] + variation, base[2] + variation]), 0, 1)


# ---------------------------------------------------------------- the surfaces: (albedo rgb [0,1], height [0,1], gloss [0,1], bump strength)
def surf_plaster(n):
    stains = noise(n, 4, 0.6, 2)
    grain = noise(n, 3, 0.8, 24)
    cracks_b, _, cracks_s = voronoi(n, 4, 1.0)
    mask = np.clip((noise(n, 3, 0.6, 4) - 0.55) * 6.0, 0, 1)            # where cracks exist at all, fading in and out
    crack = np.clip(1.0 - (cracks_s - cracks_b) * 70.0, 0, 1) * 0.35 * mask
    base = np.array([0.74, 0.70, 0.60])
    v = (stains - 0.5) * 0.16 + (grain - 0.5) * 0.07
    rgb = tint(base, v) * (1.0 - 0.35 * crack[..., None])
    # damp near the bottom is a property of the wall, not of a tile; leave it to the mesh
    h = grain * 0.5 + stains * 0.25 - crack * 0.6
    return rgb, np.clip(h, 0, 1), 0.10 + 0.05 * grain, 3.2


def surf_timber(n):
    grain = noise(n, 5, 0.6, 3, aniso=(0.06, 1.0))                    # long streaks along the board
    fine = noise(n, 3, 0.7, 40, aniso=(0.05, 1.0))
    boards = 4
    xx = np.arange(n)[None, :].repeat(n, 0)                            # column index: the boards stand side by side
    board = (xx * boards // n)
    seam = np.exp(-(((xx % (n // boards)) - 2.0) ** 2) / 3.0)         # a dark joint at each board edge
    tone = rng.uniform(-0.04, 0.04, boards)[board % boards]
    base = np.array([0.30, 0.20, 0.12])
    v = (grain - 0.5) * 0.22 + (fine - 0.5) * 0.10 + tone
    rgb = tint(base, v) * (1.0 - 0.55 * seam[..., None])
    h = grain * 0.35 + fine * 0.2 - seam * 0.8
    return rgb, np.clip(h, 0, 1), 0.18 + 0.08 * fine, 4.6


def surf_thatch(n):
    straw = noise(n, 4, 0.7, 30, aniso=(0.05, 1.0))                   # strands run down the slope (v): vertical streaks
    clumps = noise(n, 3, 0.6, 6, aniso=(0.25, 1.0))
    bands = 0.5 + 0.5 * np.sin(np.arange(n)[:, None] / n * 2 * np.pi * 6 + clumps * 3)   # layered courses
    base = np.array([0.62, 0.50, 0.24])
    v = (straw - 0.5) * 0.30 + (clumps - 0.5) * 0.16 - 0.06 * bands
    rgb = tint(base, v)
    rgb[..., 2] *= 0.8
    h = straw * 0.5 + bands * 0.3 + clumps * 0.2
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.06 + 0.04 * straw, 4.8


def surf_stone(n):
    d1, ids, d2 = voronoi(n, 5, 0.75)
    edge = np.clip((d2 - d1) * 40.0, 0, 1)                              # mortar between blocks
    per = rng.uniform(-0.09, 0.09, 64)[ids % 64]
    tone = noise(n, 4, 0.6, 5)
    fine = noise(n, 3, 0.8, 30)
    base = np.array([0.46, 0.45, 0.42])
    rgb = tint(base, per + (tone - 0.5) * 0.14 + (fine - 0.5) * 0.08)
    mortar = np.array([0.30, 0.29, 0.26])
    rgb = rgb * edge[..., None] + mortar * (1 - edge[..., None])
    h = edge * 0.6 + fine * 0.2 + (1 - np.clip(d1 * 4, 0, 1)) * 0.2
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.12 + 0.08 * fine, 4.2


def surf_slate(n):
    rows, cols = 8, 6
    yy = np.arange(n)[:, None].repeat(n, 1)
    xx = np.arange(n)[None, :].repeat(n, 0)
    row = yy * rows // n
    offset = (row % 2) * (n // cols // 2)
    cx = ((xx + offset) % n) * cols // n
    local_x = ((xx + offset) % (n // cols)) / (n // cols)
    local_y = (yy % (n // rows)) / (n // rows)
    tile_id = (row * cols + cx) % 128
    per = rng.uniform(-0.05, 0.05, 128)[tile_id]
    edge = np.minimum(np.minimum(local_x, 1 - local_x) * 22, np.minimum(local_y, 1 - local_y) * 12)
    edge = np.clip(edge, 0, 1)
    wear = noise(n, 4, 0.6, 12)
    base = np.array([0.26, 0.28, 0.32])
    rgb = tint(base, per + (wear - 0.5) * 0.10) * (0.55 + 0.45 * edge[..., None])
    h = edge * 0.6 + local_y * 0.25 + wear * 0.1
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.35 + 0.15 * wear, 4.0


def surf_planks(n):
    grain = noise(n, 5, 0.6, 3, aniso=(1.0, 0.06))
    fine = noise(n, 3, 0.7, 40, aniso=(1.0, 0.05))
    boards = 5
    yy = np.arange(n)[:, None].repeat(n, 1)
    board = yy * boards // n
    seam = np.exp(-(((yy % (n // boards)) - 2.0) ** 2) / 3.0)
    tone = rng.uniform(-0.05, 0.05, boards)[board % boards]
    base = np.array([0.44, 0.32, 0.19])
    rgb = tint(base, (grain - 0.5) * 0.2 + (fine - 0.5) * 0.08 + tone) * (1.0 - 0.6 * seam[..., None])
    h = grain * 0.3 + fine * 0.15 - seam * 0.9
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.22 + 0.1 * fine, 4.6


def surf_bark(n):
    """Round logs lying side by side (a log cabin's wall): four to a tile, furrowed along their length, dark in the gaps between them;
    the height map has each log's round profile, so the normal map makes the logs read as cylinders."""
    logs = 4
    yy = np.arange(n)[:, None].repeat(n, 1)
    frac = (yy % (n // logs)) / (n // logs)
    profile = np.sin(np.pi * frac)                                     # 0 in the gap between two logs, 1 along the middle of one
    furrow = noise(n, 5, 0.65, 4, aniso=(1.0, 0.08))
    fine = noise(n, 3, 0.7, 40, aniso=(1.0, 0.10))
    tone = rng.uniform(-0.05, 0.05, logs)[(yy * logs // n) % logs]
    base = np.array([0.31, 0.22, 0.14])
    shade = 0.45 + 0.55 * profile ** 0.55
    rgb = tint(base, (furrow - 0.5) * 0.24 + (fine - 0.5) * 0.08 + tone) * shade[..., None]
    h = profile * 0.55 + furrow * 0.38 + fine * 0.07
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.12 + 0.05 * fine, 4.4


def surf_cobble(n):
    d1, ids, d2 = voronoi(n, 7, 0.8)
    edge = np.clip((d2 - d1) * 34.0, 0, 1)
    per = rng.uniform(-0.08, 0.08, 64)[ids % 64]
    tone = noise(n, 4, 0.6, 6)
    base = np.array([0.40, 0.38, 0.34])
    dirt = np.array([0.24, 0.20, 0.15])
    rgb = tint(base, per + (tone - 0.5) * 0.12) * edge[..., None] + dirt * (1 - edge[..., None])
    h = edge * 0.7 + (1 - np.clip(d1 * 5, 0, 1)) * 0.3
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.10 + 0.1 * edge, 3.4


def surf_dirtroad(n):
    tone = noise(n, 5, 0.6, 3)
    pebbles_b, pid, _ = voronoi(n, 14, 1.0)
    peb = (pebbles_b < 0.018) & (rng.random(64)[pid % 64] > 0.55)
    fine = noise(n, 3, 0.8, 40)
    base = np.array([0.43, 0.34, 0.22])
    rgb = tint(base, (tone - 0.5) * 0.18 + (fine - 0.5) * 0.08)
    rgb[peb] = rgb[peb] * 0.55 + np.array([0.5, 0.47, 0.42]) * 0.45
    h = fine * 0.4 + tone * 0.3 + peb * 0.4
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.05 + 0.05 * fine, 2.5


def surf_cloth(n):
    warp = noise(n, 2, 0.5, 80, aniso=(1.0, 0.02))
    weft = noise(n, 2, 0.5, 80, aniso=(0.02, 1.0))
    folds = noise(n, 3, 0.6, 4)
    weave = (warp + weft) * 0.5
    base = np.array([0.52, 0.30, 0.22])
    rgb = tint(base, (weave - 0.5) * 0.16 + (folds - 0.5) * 0.14)
    h = weave * 0.4 + folds * 0.3
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1), 0.04, 2.0


SURFACES = {"bark": surf_bark, "plaster": surf_plaster, "timber": surf_timber, "thatch": surf_thatch, "stone": surf_stone, "slate": surf_slate,
            "planks": surf_planks, "cobble": surf_cobble, "dirtroad": surf_dirtroad, "cloth": surf_cloth}


# ---------------------------------------------------------------- TIFF writer (baseline, uncompressed, 8 bit)
def crytif_preset(name):
    """The block CryTIF writes into a TIFF so that RC uses a named preset whatever the file name and channels say: a Photoshop image-resource
    (tag 34377) holding an IPTC 'special instructions' record '/preset=<name>' (as found in Crytek's own environmentprobe_cm.tif)."""
    text = ("/preset=" + name).encode("ascii")
    record = bytes.fromhex("1c0228") + struct.pack(">H", len(text)) + text
    return b"8BIM" + bytes.fromhex("04040000") + struct.pack(">I", len(record)) + record + (b"" if len(record) % 2 == 0 else bytes(1))


def write_tiff(path, rgb8, alpha8=None, preset=None):
    h, w = rgb8.shape[:2]
    channels = 3 if alpha8 is None else 4
    data = rgb8.astype(np.uint8)
    if alpha8 is not None:
        data = np.dstack([data, alpha8.astype(np.uint8)])
    pixels = data.tobytes()
    entries = [
        (256, 3, 1, w), (257, 3, 1, h), (258, 3, channels, None), (259, 3, 1, 1), (262, 3, 1, 2),
        (273, 4, 1, None), (277, 3, 1, channels), (278, 3, 1, h), (279, 4, 1, len(pixels)), (284, 3, 1, 1),
    ]
    if alpha8 is not None:
        entries.append((338, 3, 1, 2))                  # ExtraSamples: unassociated alpha
    meta = crytif_preset(preset) if preset else b""
    if meta:
        entries.append((34377, 1, len(meta), None))
    entries.sort()
    ifd_size = 2 + 12 * len(entries) + 4
    bps_off = 8 + ifd_size
    meta_off = bps_off + 8
    pix_off = meta_off + len(meta)
    out = bytearray(b"II*\x00" + struct.pack("<I", 8))
    out += struct.pack("<H", len(entries))
    for tag, typ, count, val in entries:
        if tag == 258:
            out += struct.pack("<HHII", tag, typ, count, bps_off)
        elif tag == 273:
            out += struct.pack("<HHII", tag, typ, count, pix_off)
        elif tag == 34377:
            out += struct.pack("<HHII", tag, typ, count, meta_off)
        else:
            out += struct.pack("<HHI", tag, typ, count) + (struct.pack("<H", val) + b"\0\0" if typ == 3 else struct.pack("<I", val))
    out += struct.pack("<I", 0)
    out += struct.pack("<4H", 8, 8, 8, 8)
    out += meta
    out += pixels
    with open(path, "wb") as f:
        f.write(out)


def to8(x):
    return np.clip(np.round(x * 255.0), 0, 255).astype(np.uint8)


# How the height field becomes a normal map. Strengths of 3.2..4.8 for "height maps" on the timber houses read well by day,
# but at night under a lamp the plaster and thatch sparkled as black-and-white stipple (the fine grain in the height field, lit at a grazing
# angle). Measured: with the plaster's normals flat the stipple under a lamp fell from 16.8 to 7.5 (mean local contrast). Smoother and gentler now: the depth the eye should notice is in the geometry (beams, boards, logs), the normal map only adds grain.
BUMP_SMOOTH = {"plaster": 4, "thatch": 3, "timber": 3, "planks": 3, "stone": 2, "slate": 2, "bark": 3, "cobble": 2, "dirtroad": 2, "cloth": 2}
BUMP_STRENGTH = {"plaster": 0.04, "thatch": 1.6, "timber": 1.4, "planks": 1.4, "stone": 2.2, "slate": 1.6, "bark": 1.8, "cobble": 2.2, "dirtroad": 1.6, "cloth": 1.2}


# Surfaces that also get a height map for the engine's offset bump / parallax occlusion mapping. The manual: the height map is a
# `_displ` texture whose ALPHA channel holds the height (RGB left empty), compiled by RC's Displacement preset (mask *_displ*, BC4).
DISPL_SURFACES = ("stone", "cobble", "thatch", "bark", "slate", "planks", "timber", "dirtroad")


def make(name, size, stem=None, write=True):
    """The surface `name` at `size` px, written as <stem>_diff/_ddna(/_displ).tif (stem defaults to the recipe's name: `--as` renames it). write=False only returns the albedo."""
    stem = stem or name
    albedo, height, gloss, bump = SURFACES[name](size)
    gloss = np.broadcast_to(gloss, height.shape)
    n = normal_from_height(smooth(height, BUMP_SMOOTH.get(name, 1)), BUMP_STRENGTH.get(name, bump), size)
    if not write:
        return albedo
    nrgb = to8((n + 1.0) * 0.5)
    os.makedirs(SRC, exist_ok=True)
    write_tiff(os.path.join(SRC, stem + "_diff.tif"), to8(albedo))
    write_tiff(os.path.join(SRC, stem + "_ddna.tif"), nrgb, to8(np.clip(gloss, 0, 1)))
    if name in DISPL_SURFACES:
        h = smooth(height, 1)
        h = (h - h.min()) / max(h.max() - h.min(), 1e-6)
        write_tiff(os.path.join(SRC, stem + "_displ.tif"), np.zeros(h.shape + (3,), dtype=np.uint8), to8(h), preset="Displacement")
    return albedo


def compile_rc(names, out_dir, displ=None):
    """The engine's own rc.exe, run in the source folder: it writes <name>.dds next to <name>.tif and picks the preset from the file
    name (rc.ini filemasks *_diff* -> Albedo, *_ddna* -> NormalsWithSmoothness). `displ`: the stems that also have a height map
    (default: the names that are recipes with one)."""
    import shutil
    os.makedirs(out_dir, exist_ok=True)
    bad = 0
    displ = set(displ) if displ is not None else set(n for n in names if n in DISPL_SURFACES)
    for name in names:
        for suffix in ("_diff", "_ddna") + (("_displ",) if name in displ else ()):
            tif = name + suffix + ".tif"
            dds = os.path.join(SRC, name + suffix + ".dds")
            if not os.path.isfile(RC):
                sys.exit("rc.exe not found at %s: set CRYENGINE_ENGINE_DIR to the engine folder" % RC)
            if os.path.isfile(dds):
                os.remove(dds)
            # a height map keeps its height in the ALPHA channel, and RC then auto-selects an albedo preset (BC3) whatever the file name says
            # (measured: dxgi 77 against Crytek's own _displ files, 80 = BC4): the preset is named explicitly
            extra = ["/preset=Displacement"] if suffix == "_displ" else []
            proc = subprocess.run([RC, tif, "/refresh"] + extra, cwd=SRC, capture_output=True, text=True, errors="replace")
            errors = [l for l in proc.stdout.splitlines() if l.startswith("[E]")]
            ok = os.path.isfile(dds) and proc.returncode == 0
            if ok:
                shutil.copyfile(dds, os.path.join(out_dir, name + suffix + ".dds"))
            else:
                bad += 1
            print("  %-18s %s %s" % (name + suffix, ("ok, %d bytes" % os.path.getsize(dds)) if ok else "FAILED", "; ".join(errors)[:260]))
    if bad:
        sys.exit("%d texture(s) failed" % bad)


def contact_sheet(names, path):
    """A picture of the albedos, 2 rows, to look at."""
    tiles = []
    for n in names:
        a = make(n, 256, write=False)
        tiles.append(np.tile(a, (2, 2, 1))[:384, :384])
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.zeros((rows * 384, cols * 384, 3))
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[r * 384:(r + 1) * 384, c * 384:(c + 1) * 384] = t
    # a BMP is easiest to look at
    h, w = sheet.shape[:2]
    bgr = to8(sheet)[:, :, ::-1][::-1]
    pad = (4 - (w * 3) % 4) % 4
    rowdata = b"".join(bgr[y].tobytes() + b"\0" * pad for y in range(h))
    hdr = b"BM" + struct.pack("<IHHI", 54 + len(rowdata), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(rowdata), 2835, 2835, 0, 0)
    open(path, "wb").write(hdr + rowdata)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        args = [a for a in args if a != out]
    if "--size" in sys.argv:
        SIZE = int(sys.argv[sys.argv.index("--size") + 1])
        args = [a for a in args if a != str(SIZE)]
    names = args or list(SURFACES)
    if "--as" in sys.argv:                                   # one recipe under another file name: `stone --as castle_wall --out <folder>`
        stem = sys.argv[sys.argv.index("--as") + 1]
        names = list(names)
        names.remove(stem)                                   # the value of --as sits among the positionals (it may equal the surface's name)
        if len(names) != 1 or names[0] not in SURFACES:
            sys.exit("--as takes exactly one surface name (one of %s)" % ", ".join(SURFACES))
        make(names[0], SIZE, stem)
        print("made %s as %s (%d px); %s metres per tile" % (names[0], stem, SIZE, TILE_METRES.get(names[0], 1.0)))
        compile_rc([stem], out, displ=[stem] if names[0] in DISPL_SURFACES else [])
        print("tile_metres %g" % TILE_METRES.get(names[0], 1.0))
        sys.exit(0)
    if "--sheet" in sys.argv:
        # --sheet-out <file.bmp> picks the file; default: the OS temp folder (never the 512 px sources or the project's parent folder)
        sheet_path = sys.argv[sys.argv.index("--sheet-out") + 1] if "--sheet-out" in sys.argv else os.path.join(tempfile.gettempdir(), "texture_sheet.bmp")
        names = [a for a in names if a != sheet_path]
        contact_sheet(names, sheet_path)
        print("contact sheet:", sheet_path)
    else:
        for n in names:
            make(n, SIZE)
            print("made", n, "(%d px)" % SIZE)
        compile_rc(names, out)
