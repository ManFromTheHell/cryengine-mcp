#!/usr/bin/env python3
"""Look at materials side by side in the EDITOR viewport (no game): a scratch level, a row of 2 m x 2 m slabs each carrying one material, a picture of each pair, and contact sheets.

A material cannot be judged by its name, its texture file or its numbers: how it reads (glossy or dull, natural or plasticky, too contrasty, the right scale) shows only in a render.

    python screens/swatch_board.py <out dir> <material path> [<material path> ...]       e.g. materials/generic/brick/stonewall_mossy_worn
    python screens/swatch_board.py <out dir> @list.txt                                   one material per line

Writes <out dir>/sheet_<n>.png (up to 8 swatches, left to right, top to bottom, each pair as it was seen) and prints which material is where. Needs the editor; makes the level
zz_mcp_swatches (the name the project's tools may delete), opens the level that was open before and deletes the scratch level again. Each slab is the engine's primitive cube scaled
2 x 0.2 x 2: its front face shows the texture once across 2 m (the real tiling of a material is the mesh's UVs times the material's TileU: look at the texture, not at the scale).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import debug_views as DV      # noqa: E402
import shot_compare           # noqa: E402
import window_capture         # noqa: E402
import world_build as W       # noqa: E402

LEVEL = "zz_mcp_swatches"
CUBE = "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf"
SPACING = 2.7


def main():
    out = os.path.abspath(sys.argv[1])
    mats = []
    for a in sys.argv[2:]:
        if a.startswith("@"):
            mats += [l.strip() for l in open(a[1:], encoding="utf-8") if l.strip() and not l.startswith("#")]
        else:
            mats.append(a)
    os.makedirs(out, exist_ok=True)
    before = W.current_level()["name"]
    created = False
    try:
        W.new_level(LEVEL, resolution=128, unit_size=1.0)
        created = True
        _build(out, mats)
    finally:
        # whatever happened in between, leave the previously open level open again and remove the scratch level (only if it was really created)
        if created:
            if before and before != LEVEL:
                try:
                    W.open_level(before, save_current=False, discard_current=True)
                except Exception as exc:                          # noqa: BLE001
                    print("could not reopen %r: %s" % (before, exc))
            try:
                print("scratch level removed:", W.delete_scratch_level(LEVEL))
            except Exception as exc:                              # noqa: BLE001
                print("scratch level NOT removed:", exc)


def _build(out, mats):
    DV.MIN_STRUCTURE = 2.0
    ground = 32.0
    x0 = 64.0 - SPACING * (len(mats) - 1) / 2.0
    lines = ["import sandbox.general as general", "import sandbox.object as object"]
    for i, m in enumerate(mats):
        x = x0 + i * SPACING
        lines.append("n = general.new_object('Brush', %r, 'sw_%02d', %f, 64.0, %f)" % (CUBE, i, x, ground + 1.0))
        lines.append("object.set_scale(n, 2.0, 0.2, 2.0)")
        lines.append("object.set_custom_material(n, %r)" % m)
    lines.append("print('slabs', %d)" % len(mats))
    print(W._py("\n".join(lines), 600))
    frames = []
    for i in range(0, len(mats), 2):
        xs = [x0 + j * SPACING for j in (i, i + 1) if j < len(mats)]
        cx = sum(xs) / len(xs)
        stem = "swatch_%02d" % i
        pic = DV.shoot(stem, [cx, 64.0 - 4.6, ground + 1.6], [cx, 64.0, ground + 1.0], 0.2)
        frames.append((i, pic if isinstance(pic, str) else pic["picture"]))
        print("pair %d-%d: %s" % (i, min(i + 1, len(mats) - 1), ", ".join(os.path.basename(m) for m in mats[i:i + 2])), flush=True)
    sheets = []
    for s in range(0, len(frames), 4):
        group = frames[s:s + 4]
        imgs = [shot_compare.load(p) for _, p in group]
        h, w = imgs[0].shape[:2]
        sheet = [bytearray(2 * w * 3) for _ in range(2 * h)]
        for k, im in enumerate(imgs):
            ox, oy = (k % 2) * w, (k // 2) * h
            for y in range(h):
                row = bytes(im[y, :, :3].astype("uint8").tobytes())
                sheet[oy + y][ox * 3:(ox + w) * 3] = row
        path = os.path.join(out, "sheet_%d.png" % (s // 4))
        window_capture.save_png(path, 2 * w, 2 * h, [bytes(r) for r in sheet])
        first = group[0][0]
        sheets.append((path, first, first + 2 * len(group) - 1))
        print("SHEET|%s|swatches %d-%d (left to right, top to bottom)" % (path, first, min(first + 2 * len(group) - 1, len(mats) - 1)), flush=True)
    for i, m in enumerate(mats):
        print("  %2d  %s" % (i, m))


if __name__ == "__main__":
    main()
