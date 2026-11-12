#!/usr/bin/env python3
"""See a material's texture and measure how glossy it is, without a renderer: decode the diffuse map (BC1) and the gloss (the alpha of the _ddna map, BC4) from the .dds files the engine
reads, and read the material's own numbers. A render in the editor viewport depends on streaming, shadow and sun; this does not.

    python screens/texture_preview.py <out dir> <material> [<material> ...]       a material path under Assets, with or without .mtl (e.g. materials/generic/brick/brick_old)
    python screens/texture_preview.py <out dir> @list.txt

Writes <out dir>/sheet_<n>.png (20 materials per sheet, 5 across, left to right, top to bottom, 256 px tiles) and prints one line per material:
index, name, the average diffuse colour (sRGB), its contrast (standard deviation of luminance, 0-100), the material's Shininess and Specular, the mean of the gloss channel and the RESULTING
smoothness (Shininess/255 x gloss: pbs_check's rule: the engine multiplies them), and the material's tiling. Stone and masonry that read as natural sit around smoothness 0.05-0.30 and a
contrast of 8-25; a plastic look is high smoothness or a contrast far above that.

The decoder reads the largest mip it finds whose size matches 512 x 512 (BC1/BC4: 128 KB); a texture without such a part is skipped with a note.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import window_capture  # noqa: E402

ASSETS = __import__("project_root").ASSETS
SIZES_BC = [(n, (n // 4) ** 2 * 8) for n in (512, 256, 1024, 2048)]      # the side and byte count of a BC1 / BC4 mip, in the order of preference


def _single(path, alpha=False, want=512):
    """The `want` x `want` mip of a plain (unsplit) .dds that carries its whole mip chain (the project's own textures), or None. A normal map (BC5, 16 bytes per block) keeps its gloss
    as a second chain of BC4 mips (8 bytes per block) after the BC5 chain: alpha=True reads that one."""
    if not os.path.isfile(path):
        return None
    d = open(path, "rb").read()
    if d[:4] != b"DDS ":
        return None
    h, w, mips = struct.unpack_from("<I", d, 12)[0], struct.unpack_from("<I", d, 16)[0], struct.unpack_from("<I", d, 28)[0]
    four = d[84:88]
    off = 148 if four == b"DX10" else 128
    dxgi = struct.unpack_from("<I", d, 128)[0] if four == b"DX10" else None
    bpb = 16 if (four in (b"DXT5", b"ATI2") or dxgi in (98, 99, 83, 84, 77, 78)) else 8
    if alpha:
        if bpb != 16:
            return None
        chain = 0
        n = w
        for _ in range(mips):
            chain += max(1, n // 4) * max(1, n // 4) * 16
            n = max(1, n // 2)
        off += chain
        bpb = 8
    cw = w
    for _ in range(mips):
        size = max(1, cw // 4) * max(1, cw // 4) * bpb
        if bpb == 8 and cw == want:
            chunk = d[off:off + size]
            return chunk if len(chunk) == size else None          # a split set's base file holds only the header: its parts are the mips
        off += size
        cw = max(1, cw // 2)
    return None


def _part(base, alpha=False):
    """The bytes of the mip of a .dds that is 512 px a side, else 256, else 1024 (base = the .dds path without a part suffix), or None."""
    for want in (512, 256, 1024):
        one = _single(base, alpha, want)
        if one is not None:
            return one
        found = _split_part(base, alpha, want)
        if found is not None:
            return found
    return None


def _split_part(base, alpha, want):
    for p in sorted(glob.glob(base + ".*")):
        suffix = p[len(base) + 1:]
        if alpha != suffix.endswith("a") or not re.match(r"^\d+a?$", suffix):
            continue
        if os.path.getsize(p) == (want // 4) ** 2 * 8:
            return open(p, "rb").read()
    return None


def _side(data):
    """Pixels per side of a square BC1/BC4 mip from its byte count (8 bytes per 4 x 4 block)."""
    return int(round((len(data) / 8.0) ** 0.5)) * 4


def bc1(data, w=None, h=None):
    w = h = _side(data)
    b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 8)
    c0 = b[:, 0].astype(np.uint16) | (b[:, 1].astype(np.uint16) << 8)
    c1 = b[:, 2].astype(np.uint16) | (b[:, 3].astype(np.uint16) << 8)

    def rgb(c):
        return np.stack([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], axis=1).astype(np.int32)
    p0, p1 = rgb(c0), rgb(c1)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four, (p0 + 2 * p1) // 3, np.zeros_like(p0))
    palette = np.stack([p0, p1, p2, p3], axis=1)                       # (n, 4, 3)
    idx = (b[:, 4].astype(np.uint32) | (b[:, 5].astype(np.uint32) << 8) | (b[:, 6].astype(np.uint32) << 16) | (b[:, 7].astype(np.uint32) << 24))
    sel = np.stack([(idx >> (2 * i)) & 3 for i in range(16)], axis=1)  # (n, 16)
    px = np.take_along_axis(palette, sel[:, :, None], axis=1)          # (n, 16, 3)
    bw, bh = w // 4, h // 4
    img = px.reshape(bh, bw, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h, w, 3)
    return img.astype(np.uint8)


def bc4(data, w=None, h=None):
    w = h = _side(data)
    b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 8)
    a0, a1 = b[:, 0].astype(np.int32), b[:, 1].astype(np.int32)
    bits = np.zeros(len(b), dtype=np.uint64)
    for i in range(6):
        bits |= b[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    pal = np.zeros((len(b), 8), dtype=np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    for i in range(1, 7):
        pal[:, 1 + i] = np.where(a0 > a1, ((7 - i) * a0 + i * a1) // 7, np.where(i < 5, ((5 - i) * a0 + i * a1) // 5, 0))
    pal[:, 6] = np.where(a0 > a1, pal[:, 6], 0)
    pal[:, 7] = np.where(a0 > a1, pal[:, 7], 255)
    sel = np.stack([((bits >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int64) for i in range(16)], axis=1)
    px = np.take_along_axis(pal, sel, axis=1)
    bw, bh = w // 4, h // 4
    return px.reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(h, w).astype(np.float32) / 255.0


def mtl_info(path, sub=None):
    root = ET.parse(path).getroot()
    if sub:
        node = next(m for m in root.iter("Material") if m.get("Shader") and m.get("Name") == sub)
    else:
        node = root if root.get("Shader") else next((m for m in root.iter("Material") if m.get("Shader")), root)
    tex = {t.get("Map"): t.get("File") for t in node.iter("Texture")}
    mod = next((t.find("TexMod") for t in node.iter("Texture") if t.get("Map") == "Diffuse" and t.find("TexMod") is not None), None)
    tile = (float(mod.get("TileU", 1)), float(mod.get("TileV", 1))) if mod is not None else (1.0, 1.0)
    return {"shininess": float(node.get("Shininess", 0)), "specular": node.get("Specular", ""), "diffuse": node.get("Diffuse", ""), "tex": tex, "tile": tile, "shader": node.get("Shader")}


def lum(img):
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def analyse(material):
    material, _, sub = material.partition("#")
    rel = material.replace("\\", "/")
    if not rel.lower().endswith(".mtl"):
        rel += ".mtl"
    path = os.path.join(ASSETS, *rel.split("/"))
    info = mtl_info(path, sub or None)
    out = {"name": os.path.basename(rel)[:-4] + ("#" + sub if sub else ""), "info": info, "diffuse": None, "gloss": None}
    d = info["tex"].get("Diffuse")
    if d:
        base = os.path.join(ASSETS, *re.sub(r"\.(tif|tga|png)$", ".dds", d.replace("\\", "/")).split("/"))
        data = _part(base)
        out["diffuse"] = bc1(data) if data else None
    n = info["tex"].get("Bumpmap")
    if n:
        base = os.path.join(ASSETS, *re.sub(r"\.(tif|tga|png)$", ".dds", n.replace("\\", "/")).split("/"))
        data = _part(base, alpha=True)
        out["gloss"] = bc4(data) if data else None
    return out


def main():
    out_dir = os.path.abspath(sys.argv[1])
    mats = []
    for a in sys.argv[2:]:
        mats += [l.strip() for l in open(a[1:], encoding="utf-8") if l.strip() and not l.startswith("#")] if a.startswith("@") else [a]
    os.makedirs(out_dir, exist_ok=True)
    rows, tiles = [], []
    for i, m in enumerate(mats):
        try:
            r = analyse(m)
        except Exception as exc:                                   # noqa: BLE001
            print("%2d  %-34s  not read (%s)" % (i, m, str(exc)[:60]))
            tiles.append(np.zeros((256, 256, 3), np.uint8))
            continue
        img = r["diffuse"]
        if img is not None:
            mean = img.reshape(-1, 3).mean(axis=0)
            contrast = float(lum(img.astype(np.float32)).std() / 255.0 * 100 * 2)
            step = max(1, img.shape[0] // 256)
            tiles.append(img[::step, ::step][:256, :256])
        else:
            mean, contrast = np.zeros(3), 0.0
            tiles.append(np.zeros((256, 256, 3), np.uint8))
        gloss = float(r["gloss"].mean()) if r["gloss"] is not None else None
        shin = r["info"]["shininess"]
        smooth = (shin / 255.0 * gloss) if gloss is not None else None
        print("%2d  %-30s  rgb %3d %3d %3d  contrast %4.1f  shininess %5.1f  gloss %s  -> smoothness %s  spec %s  tile %s" % (
            i, r["name"], mean[0], mean[1], mean[2], contrast, shin, "%.2f" % gloss if gloss is not None else " n/a", "%.2f" % smooth if smooth is not None else " n/a",
            r["info"]["specular"][:14], r["info"]["tile"]), flush=True)
    for s in range(0, len(tiles), 20):
        grp = tiles[s:s + 20]
        cols = 5
        rws = (len(grp) + cols - 1) // cols
        sheet = np.zeros((rws * 256, cols * 256, 3), np.uint8)
        for k, t in enumerate(grp):
            y, x = (k // cols) * 256, (k % cols) * 256
            sheet[y:y + 256, x:x + 256] = t[:256, :256]
        p = os.path.join(out_dir, "sheet_%d.png" % (s // 20))
        window_capture.save_png(p, sheet.shape[1], sheet.shape[0], [sheet[y].tobytes() for y in range(sheet.shape[0])])
        print("SHEET|%s|materials %d-%d" % (p, s, s + len(grp) - 1))


if __name__ == "__main__":
    main()
