#!/usr/bin/env python3
"""The Undercroft: the dungeon beneath the old tower (Design/ASHVALE_SCRIPT.md, chapter II), as DATA.

    undercroft_kit.py meshes [--overwrite]   write the material and the meshes (objects/ashvale/undercroft_*_<VERSION>.cgf)
    undercroft_kit.py check                  the offline layout check (test_undercroft_kit.py): enclosure, reachability, the sealed vault
    undercroft_kit.py list                   the entities build_undercroft.py will place

Everything is in a LOCAL frame (metres): x east, y north, z up, the floor of the arrival hall at z = 0. The level places the meshes at O (a corner of an otherwise empty level,
high above its terrain: the Undercroft is sealed, so nothing of the sky or the terrain is seen). The meshes come from the project's own pipeline (pipeline.build_mesh: boxes, bars,
cylinders, frustums, quads and polygons with METRIC UVs, compiled by the engine's rc.exe).

THE PLAN. The descent is 3 m by the gallery's steps, then down the cavern's slope and the passage's steps to the lower halls; every place has its own SHAPE, FLOOR and STONE:

    A  Stair Hall        10 x 10, ashlar walls, a checker flagstone floor, brick coffered ceiling, brick pilasters; the stair to the tower (E at its top)
    B  Brick Gallery     a corridor in red brick with corbelled arches, three barred cells on the south side (Brother Cuthbert is in the middle one), a bone alcove on the north,
                         then three steps down and timber shoring, and a collapse
    C  Diggers' Cavern   a natural cave (an irregular polygon): bedrock, damp earth that slopes 1.5 m down, stalactites, a rubble heap, a campfire, mine timber
    D  Passage           mossy, three steps down
    E  Ossuary           an octagon: the walls of bones on the diagonals, columns of three kinds, a sunken pit with stairs and a skull altar, a cupola over it
    F  Dug tunnel        rough-hewn, an S-bend (Wat's men cut it round the old vault)
    G  Corbin's Hall     a hall of two levels: balconies (timber, with stairs) on both sides, square piers, a marble carpet, a dais, two statues, banners, a timber ceiling
    V  The Vault         a twelve-sided domed tomb in marble, stepped up to a plinth, with the old lord's sarcophagus and strongbox; behind the stone door
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import math
import os
import random
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
from uc_geom import Parts, lerp, smoothstep, regular_ring, scaled, ring_walls, proxies   # noqa: E402

O = (1000.0, 1000.0, 100.0)          # world position of the local origin: a corner of the level, 68 m above its flat terrain
T = 1.0
VERSION = "v3"
MATERIAL_FILE = "materials/ashvale/undercroft_%s" % VERSION       # a new file name per version: the running editor keeps a loose asset it has loaded
MATERIALS = ["ashlar", "brick", "flagstone", "rockface", "cavefloor", "mossy", "bones", "marble", "timber", "iron", "slate", "banner", "gold", "puddle", "beam"]
TILES = {"ashlar": 2.4, "brick": 1.8, "flagstone": 2.4, "rockface": 3.0, "cavefloor": 3.0, "mossy": 2.4, "bones": 1.6, "marble": 2.0, "timber": 1.0, "iron": 1.2, "slate": 1.2,
         "banner": 1.0, "gold": 1.0, "puddle": 1.0, "beam": 1.0}
# Review finding: generated assets looked artificial (unnatural textures, uniform gloss) and the dungeon showed unrelated texture changes: wanted variety within one family, not different looks.
# The first version drew every room with a flagstone floor + beige ashlar walls + RED BRICK ceiling, white marble in the vault, blue slate in the ossuary: fifteen unrelated looks, on
# 512 px procedural textures of high contrast and saturated colour with a uniform gloss. Now ONE family, the grey weathered limestone of the GameSDK sample's own photographic materials
# (copied exactly by write_material's from_material: shader, flags, specular and the gloss in the _ddna alpha stay Crytek's), in a few related variants:
#   ashlar    dark grey cut blocks        walls, stairs, arches          marble   -> the lighter, stained ashlar: the vault, columns, the dais (no marble)
#   brick     dark rubble                 vault ceilings, pilasters      mossy    -> the same stone, damp: the cells
#   flagstone grey slabs                  floors                         slate    -> dark irregular paving: the ossuary
#   rockface / cavefloor                  the cave: dark grey rock, earth with pebbles
# `tiles` = (what the mesh's UVs were baked for, TILES[...]) / (metres one texture really covers): the meshes did not have to be rebuilt.
def _natural(name, source, real_metres, **kw):
    return dict(name=name, from_material=source, tiles=round(TILES[name] / real_metres, 3), **kw)


SUBMATERIALS = [
    _natural("ashlar", "materials/generic/brick/brick_large_worn", 2.4),
    _natural("brick", "materials/generic/brick/brick_old", 2.0),
    _natural("flagstone", "materials/generic/concrete/floor_stone_tiles_a", 2.0),
    _natural("rockface", "objects/natural/rocks/cliff_rocks/cliff_rock_neutral_con", 3.5),
    _natural("cavefloor", "materials/generic/mud/soil_a", 2.0, surface_type="mat_soil"),         # NOT a materials/terrain/* material (Terrain.Layer draws BLACK on a mesh) and not objects/.../erosion (tessellation + vertex colours)
    _natural("mossy", "materials/generic/brick/stonewall_mossy", 3.0),
    _natural("bones", "materials/generic/concrete/plaster_sandstone_a", 1.2, tint="1.0,0.96,0.86"),
    _natural("marble", "materials/generic/brick/brick_bridge", 2.4),
    _natural("timber", "materials/generic/wood/wood_beams_b", 1.5),
    _natural("iron", "materials/generic/metal/metal_iron_b", 1.0),
    _natural("slate", "materials/generic/concrete/floor_stone_paver_a", 2.0),
    _natural("banner", "materials/generic/fabric/cloth_fine_dirty", 1.5, tint="0.62,0.26,0.21", surface_type="mat_fabric"),
    _natural("gold", "materials/generic/metal/metal_gold_a", 1.0, shininess_scale=0.45),
    dict(name="puddle", tint="0.015,0.02,0.025", shininess=250, surface_type="mat_default"),
    _natural("beam", "materials/generic/wood/wood_beams_c", 1.5),
]

BODY_HEIGHT = 1.8
STEP = 0.5

# ----------------------------------------------------------------------------- the levels of the floor
CEIL_B = 3.6                           # the gallery's ceiling (absolute z)
Z_E = -3.0                             # the lower halls' floor
D_FLOOR0 = -2.25                       # where the cavern leaves, and the passage begins
DOOR_H = 3.6                           # a corridor door's height above its floor

A = dict(x0=0.0, y0=0.0, x1=10.0, y1=10.0, fz=0.0, cz=5.5)
GALLERY = dict(a0=11.0, a1=30.0, c=5.0, w=3.0)
CELLS = [(12.0, 15.0), (16.0, 19.0), (24.0, 27.0)]          # the south cells (x ranges), their interiors run y -0.5 .. 3.5
STEPS_B = 21.5                          # three 0.5 m steps down from here
CENTER_C = (39.0, 5.0)
D = dict(a0=48.0, a1=53.0, c=5.0, w=3.0, cz=D_FLOOR0 + DOOR_H)
E_RING = [(54.0, 10.0), (58.0, 14.0), (72.0, 14.0), (76.0, 10.0), (76.0, 2.0), (72.0, -2.0), (58.0, -2.0), (54.0, 2.0)]
E_CZ, E_CUPOLA, E_PIT = 2.0, 3.5, -4.5
E_PIT_RECT = (60.0, 1.5, 70.0, 10.5)
F1 = dict(c=64.5, w=3.0, a0=15.0, a1=22.0)
HF = dict(c=20.5, w=3.0, a0=67.0, a1=73.0)
G = dict(x0=54.0, y0=23.0, x1=76.0, y1=41.0, fz=-3.0, cz=4.5)
W_STAIR_Y0, W_PLAT_Y0 = 25.5, 30.5         # the west balcony: its stair climbs north from y 25.5 to the platform that runs on to the north wall
E_STAIR_Y1, E_PLAT_Y1 = 39.0, 34.0         # the east balcony: its stair climbs south from y 39 to the platform that runs on to the south wall
BALCONY_Z = -0.6
VAULT_C = (64.5, 49.0)
VAULT_APOTHEM = 7.0
GATE = dict(x=64.5, y=41.5, w=2.96, t=0.8, h=2.98)
STAIR = dict(x_start=2.0, run=0.5, rise=0.25, steps=12, y0=7.4, y1=10.0, x_end=10.0)
LANDING_Z = STAIR["rise"] * STAIR["steps"]


# ----------------------------------------------------------------------------- the cave
CAVE = [(30.0, 6.5), (29.8, 9.0), (31.0, 11.4), (33.4, 13.0), (36.2, 14.6), (39.4, 14.0), (42.6, 15.2), (45.4, 13.8), (47.3, 11.2), (48.0, 8.2), (48.0, 6.5),
        (48.0, 3.5), (47.6, 1.0), (46.4, -1.6), (43.8, -3.2), (41.0, -3.9), (38.2, -2.8), (35.2, -4.4), (32.6, -3.1), (30.7, -1.3), (30.0, 1.0), (30.0, 3.5)]
CAVE_MOUTHS = {10: D_FLOOR0 + DOOR_H, 21: CEIL_B}          # edges with no wall: east (to D) and west (from the gallery); the corridor's ceiling is the lintel line


def cave_floor(x, y):
    base = -0.75 + (D_FLOOR0 + 0.75) * min(1.0, max(0.0, (x - 30.0) / 18.0))
    fade = smoothstep(30.0, 34.0, x) * (1.0 - smoothstep(44.5, 48.0, x))
    bumps = 0.12 * math.sin(x * 0.7 + y * 0.4) + 0.07 * math.sin(x * 1.9 - y * 1.3) + 0.05 * math.sin(y * 2.1 + x * 0.5)
    return base + bumps * fade


def b_floor(x):
    if x < STEPS_B:
        return 0.0
    return -0.25 * min(3, int((x - STEPS_B) / 0.5) + 1)


def d_floor(x):
    if x < 50.0:
        return D_FLOOR0
    return D_FLOOR0 - 0.25 * min(3, int((x - 50.0) / 0.5) + 1)


def floor_z(x, y):
    """The floor under (x, y) in the ground storey (not the balconies)."""
    if x < 11.0:
        return 0.0
    if x < 30.0 and y < 16.0:
        return b_floor(x)
    if x < 48.0:
        return cave_floor(x, y)
    if x < 53.5:
        return d_floor(x)
    return Z_E


def _cuts(lo, hi, gaps):
    cuts = sorted((c - w / 2.0, c + w / 2.0) for c, w, _h in (gaps or []))
    out, at = [], lo
    for a, b in cuts:
        if a > at + 1e-6:
            out.append((at, a))
        at = max(at, b)
    if hi > at + 1e-6:
        out.append((at, hi))
    return out


def box_room(P, x0, y0, x1, y1, fz, cz, gaps=None, skip="", floor="flagstone", wall="ashlar", ceil="brick"):
    """A room of boxes: interior x0..x1, y0..y1, floor top fz, ceiling underside cz; gaps = {'W'|'E'|'S'|'N': [(centre, width, ABSOLUTE height of the top)]}."""
    gaps = gaps or {}
    wx0, wx1 = x0 - (0.0 if "W" in skip else T), x1 + (0.0 if "E" in skip else T)
    wy0, wy1 = y0 - (0.0 if "S" in skip else T), y1 + (0.0 if "N" in skip else T)
    P.box(wx0, wy0, fz - 1.0, wx1, wy1, fz, floor, tag="floor")
    P.box(wx0, wy0, cz, wx1, wy1, cz + 1.0, ceil, tag="ceiling")

    def side(name, axis, f0, f1, lo, hi):
        if name in skip:
            return
        for a, b in _cuts(lo, hi, gaps.get(name)):
            if axis == "y":
                P.box(f0, a, fz, f1, b, cz, wall, tag="wall")
            else:
                P.box(a, f0, fz, b, f1, cz, wall, tag="wall")
        for c, w, h in gaps.get(name, []):
            if h < cz - 1e-6:
                if axis == "y":
                    P.box(f0, c - w / 2.0, h, f1, c + w / 2.0, cz, wall, tag="wall")
                else:
                    P.box(c - w / 2.0, f0, h, c + w / 2.0, f1, cz, wall, tag="wall")

    side("W", "y", x0 - T, x0, wy0, wy1)
    side("E", "y", x1, x1 + T, wy0, wy1)
    side("S", "x", y0 - T, y0, x0, x1)
    side("N", "x", y1, y1 + T, x0, x1)


def tube(P, axis, a0, a1, c, w, floors, cz, gaps=None, wall="brick", ceil="brick"):
    """A corridor of boxes along `axis` from a0 to a1: floors = [(lo, hi, top z, material)] slabs along the axis; side walls up to cz (absolute) with gaps {'L'|'H': [(centre, width, top z)]}."""
    gaps = gaps or {}
    lo, hi = c - w / 2.0, c + w / 2.0
    wall_z0 = min(f[2] for f in floors) - 1.0
    for f0, f1, top, mat in floors:
        if axis == "x":
            P.box(f0, lo - T, top - 1.0, f1, hi + T, top, mat, tag="floor")
        else:
            P.box(lo - T, f0, top - 1.0, hi + T, f1, top, mat, tag="floor")
    if axis == "x":
        P.box(a0, lo - T, cz, a1, hi + T, cz + 1.0, ceil, tag="ceiling")
    else:
        P.box(lo - T, a0, cz, hi + T, a1, cz + 1.0, ceil, tag="ceiling")
    for side, f0, f1 in (("L", lo - T, lo), ("H", hi, hi + T)):
        for a, b in _cuts(a0, a1, gaps.get(side)):
            if axis == "x":
                P.box(a, f0, wall_z0, b, f1, cz, wall, tag="wall")
            else:
                P.box(f0, a, wall_z0, f1, b, cz, wall, tag="wall")
        for cc, ww, h in gaps.get(side, []):
            if h < cz - 1e-6:
                if axis == "x":
                    P.box(cc - ww / 2.0, f0, h, cc + ww / 2.0, f1, cz, wall, tag="wall")
                else:
                    P.box(f0, cc - ww / 2.0, h, f1, cc + ww / 2.0, cz, wall, tag="wall")


def corbel_arch(P, pos, c, w, cz, mat="ashlar"):
    """A corbelled arch across an x-axis corridor at x = pos: three stepped courses growing from each side wall (0.5 m thick along the corridor), piers under them."""
    lo, hi = c - w / 2.0, c + w / 2.0
    for k, reach in enumerate((0.55, 0.95, 1.30)):
        z0 = cz - 0.9 + 0.3 * k
        P.box(pos - 0.25, lo, z0, pos + 0.25, lo + reach, z0 + 0.3, mat, tag="arch")
        P.box(pos - 0.25, hi - reach, z0, pos + 0.25, hi, z0 + 0.3, mat, tag="arch")
    P.box(pos - 0.25, lo, cz - 3.6, pos + 0.25, lo + 0.4, cz - 0.9, mat, tag="arch")
    P.box(pos - 0.25, hi - 0.4, cz - 3.6, pos + 0.25, hi, cz - 0.9, mat, tag="arch")


# ----------------------------------------------------------------------------- the shell: A, B, D, F, G
def shell():
    P = Parts()
    a = A
    # A, the Stair Hall
    box_room(P, a["x0"], a["y0"], a["x1"], a["y1"], a["fz"], a["cz"], gaps={"E": [(GALLERY["c"], GALLERY["w"], CEIL_B)]}, floor="flagstone", wall="ashlar", ceil="brick")
    s = STAIR
    for i in range(s["steps"]):
        P.box(s["x_start"] + s["run"] * i, s["y0"], 0.0, s["x_end"], s["y1"], s["rise"] * (i + 1), "ashlar", tag="stair")
    for i in range(s["steps"]):                                                                                # the parapet along the open side of the stair follows the steps
        x_a = s["x_start"] + s["run"] * i
        P.box(x_a, s["y0"] - 0.15, 0.0, x_a + s["run"] if i < s["steps"] - 1 else s["x_end"], s["y0"], s["rise"] * (i + 1) + 1.0, "brick", tag="stair")
    for yc in (1.25, 3.75, 6.25):                                                                              # brick pilasters on the west wall
        P.box(0.0, yc - 0.25, 0.0, 0.4, yc + 0.25, a["cz"], "brick", tag="pilaster")
    for xc in (1.25, 3.75, 6.25, 8.75):                                                                        # and on the south wall
        P.box(xc - 0.25, 0.0, 0.0, xc + 0.25, 0.4, a["cz"], "brick", tag="pilaster")
    for xc in (1.8, 4.6, 7.4):                                                                                 # a coffered ceiling: timber beams over brick
        P.box(xc - 0.2, 0.0, a["cz"] - 0.3, xc + 0.2, 10.0, a["cz"], "beam", tag="beam")
    P.box(9.6, 2.9, 0.0, 10.0, 3.5, CEIL_B, "brick", tag="pilaster")                                           # the doorway to the gallery, framed in brick
    P.box(9.6, 6.5, 0.0, 10.0, 7.1, CEIL_B, "brick", tag="pilaster")
    P.box(9.6, 2.9, CEIL_B, 10.0, 7.1, CEIL_B + 0.5, "brick", tag="pilaster")
    P.box(8.6, 8.0, a["cz"] - 0.1, 9.8, 9.4, a["cz"], "timber", tag="deco")                                    # the trapdoor over the landing

    # B, the Brick Gallery
    g = GALLERY
    south_gaps = [((c0 + c1) / 2.0, c1 - c0, CEIL_B) for c0, c1 in CELLS]
    north_gaps = [(15.0, 3.0, CEIL_B)]                                                                         # the bone alcove
    floors = [(g["a0"], STEPS_B, 0.0, "cavefloor")]
    for k in range(3):
        floors.append((STEPS_B + 0.5 * k, STEPS_B + 0.5 * (k + 1), -0.25 * (k + 1), "ashlar"))
    floors.append((STEPS_B + 1.5, g["a1"], -0.75, "cavefloor"))
    tube(P, "x", g["a0"], g["a1"], g["c"], g["w"], floors, CEIL_B, gaps={"L": south_gaps, "H": north_gaps}, wall="brick", ceil="brick")
    for x in (15.5, 19.5):                                                                                     # corbelled arches in the upper gallery
        corbel_arch(P, x, g["c"], g["w"], CEIL_B)
    for x in (25.0, 28.5):                                                                                     # timber shoring in the lower, rougher part
        for y in (g["c"] - 1.3, g["c"] + 1.3):
            P.box(x - 0.18, y - 0.18, -0.75, x + 0.18, y + 0.18, CEIL_B - 0.35, "beam", tag="post")
        P.box(x - 0.22, g["c"] - 1.5, CEIL_B - 0.38, x + 0.22, g["c"] + 1.5, CEIL_B, "beam", tag="beam")
    for k, (c0, c1) in enumerate(CELLS):
        fz = b_floor((c0 + c1) / 2.0)
        box_room(P, c0, -0.5, c1, 3.5, fz, CEIL_B, skip="N", floor="cavefloor", wall="mossy" if k != 2 else "rockface", ceil="brick")
        # the bars across the cell's front (y = 3.2..3.3): rails, then uprights every 0.17 m; the door gap at the west end of cells 1 and 2 is open
        P.box(c0, 3.2, fz + 0.1, c1, 3.3, fz + 0.2, "iron", tag="bars")
        P.box(c0, 3.2, fz + 2.5, c1, 3.3, fz + 2.6, "iron", tag="bars")
        x = c0 + (1.8 if k != 2 else 0.0)
        while x < c1 - 0.05:
            P.box(x, 3.22, fz + 0.2, x + 0.04, 3.28, fz + 2.5, "iron", tag="bars")
            x += 0.17
        if k != 2:
            P.box(c0, 3.2, fz + 0.2, c0 + 0.06, 3.3, fz + 2.5, "iron", tag="bars")                             # the open door's frame
            P.box(c0 + 1.74, 3.2, fz + 0.2, c0 + 1.8, 3.3, fz + 2.5, "iron", tag="bars")
    box_room(P, 13.5, 7.5, 16.5, 9.5, 0.0, CEIL_B, skip="S", floor="flagstone", wall="mossy", ceil="brick")    # the bone alcove (2 m deep) on the north wall

    # D, the mossy passage with three steps down
    d = D
    dfloors = [(d["a0"], 50.0, D_FLOOR0, "ashlar"), (50.0, 50.5, D_FLOOR0 - 0.25, "ashlar"), (50.5, 51.0, D_FLOOR0 - 0.5, "ashlar"), (51.0, d["a1"], D_FLOOR0 - 0.75, "ashlar")]
    tube(P, "x", d["a0"], d["a1"], d["c"], d["w"], dfloors, d["cz"], wall="mossy", ceil="mossy")
    P.box(d["a1"], d["c"] - 1.5, Z_E - 1.0, d["a1"] + T, d["c"] + 1.5, Z_E, "ashlar", tag="floor")              # the floor under the ossuary's west doorway (its reveal is one wall thick)
    P.box(F1["c"] - 1.5, 14.0, Z_E - 1.0, F1["c"] + 1.5, F1["a0"], Z_E, "ashlar", tag="floor")                  # and under its north doorway

    # F, the dug tunnel: north from the ossuary, an S-bend, and into the hall at its south-east
    f1, hf = F1, HF
    rough = dict(wall="rockface", ceil="rockface")
    tube(P, "y", f1["a0"], f1["a1"], f1["c"], f1["w"], [(f1["a0"], f1["a1"], Z_E, "cavefloor")], Z_E + DOOR_H, gaps={"H": [(hf["c"], hf["w"], Z_E + DOOR_H)]}, **rough)
    tube(P, "x", hf["a0"], hf["a1"], hf["c"], hf["w"], [(hf["a0"], hf["a1"], Z_E, "cavefloor")], Z_E + DOOR_H, gaps={"H": [(71.5, 3.0, Z_E + DOOR_H)]}, **rough)
    P.box(hf["a1"], hf["c"] - hf["w"] / 2.0 - T, Z_E - 1.0, hf["a1"] + T, hf["c"] + hf["w"] / 2.0 + T, Z_E + DOOR_H + 1.0, "rockface", tag="wall")        # the end of the bend

    # G, Corbin's Hall (two levels)
    gg = G
    box_room(P, gg["x0"], gg["y0"], gg["x1"], gg["y1"], gg["fz"], gg["cz"], gaps={"S": [(71.5, 3.0, Z_E + DOOR_H)], "N": [(GATE["x"], 3.0, Z_E + 3.0)]}, floor="flagstone", wall="ashlar", ceil="timber")
    P.box(62.5, 23.0, gg["fz"], 66.5, 40.4, gg["fz"] + 0.01, "marble", faces="+z", tag="deco")                  # the marble carpet down the middle
    for yc in (25.5, 29.5, 33.5, 37.5):                                                                        # beams across the timber ceiling
        P.box(gg["x0"], yc - 0.25, gg["cz"] - 0.55, gg["x1"], yc + 0.25, gg["cz"], "beam", tag="beam")
    rise = BALCONY_Z - gg["fz"]
    n_steps = int(round(rise / 0.24))
    run = 0.5
    P.box(54.0, W_PLAT_Y0, BALCONY_Z - 0.4, 57.5, 41.0, BALCONY_Z, "timber", tag="floor")
    for i in range(n_steps):
        P.box(54.0, W_STAIR_Y0 + run * i, gg["fz"], 56.2, W_STAIR_Y0 + run * (i + 1), gg["fz"] + rise * (i + 1) / n_steps, "ashlar", tag="stair")
    P.box(72.5, 23.0, BALCONY_Z - 0.4, 76.0, E_PLAT_Y1, BALCONY_Z, "timber", tag="floor")
    for i in range(n_steps):
        P.box(73.8, E_STAIR_Y1 - run * (i + 1), gg["fz"], 76.0, E_STAIR_Y1 - run * i, gg["fz"] + rise * (i + 1) / n_steps, "ashlar", tag="stair")
    # the stairs' open sides: a solid parapet along each, and a rail where the platform's edge turns away from the stair
    P.box(56.2, W_STAIR_Y0, gg["fz"], 56.45, W_PLAT_Y0, BALCONY_Z + 1.0, "ashlar", tag="stair")
    P.box(56.2, W_PLAT_Y0 - 0.1, BALCONY_Z, 57.5, W_PLAT_Y0, BALCONY_Z + 1.0, "beam", tag="rail")
    P.box(73.55, E_PLAT_Y1, gg["fz"], 73.8, E_STAIR_Y1, BALCONY_Z + 1.0, "ashlar", tag="stair")
    P.box(72.5, E_PLAT_Y1, BALCONY_Z, 73.8, E_PLAT_Y1 + 0.1, BALCONY_Z + 1.0, "beam", tag="rail")
    # the dais at the north end under the vault door: marble, three steps
    P.box(59.0, 38.0, gg["fz"], 70.0, 40.0, gg["fz"] + 0.6, "marble", tag="dais")
    for k in range(2):
        P.box(59.0 - 0.5 * (k + 1), 38.0, gg["fz"], 70.0 + 0.5 * (k + 1), 40.0, gg["fz"] + 0.2 * (2 - k), "marble", tag="dais")
    return P


# ----------------------------------------------------------------------------- the polygon rooms: C (cave), E (ossuary), V (vault)
def cave():
    P = Parts()
    ring = CAVE
    cx, cy = CENTER_C
    rnd = random.Random(1348)
    n = len(ring)
    tops = [cave_floor(x, y) + 4.4 + 0.9 * math.sin(i * 1.7) + rnd.uniform(-0.2, 0.3) for i, (x, y) in enumerate(ring)]
    for i, h in CAVE_MOUTHS.items():                                                                           # the ceiling meets the corridors' at their mouths
        for v in (i, (i + 1) % n):
            tops[v] = max(tops[v], h + 0.3)
    zc = cave_floor(cx, cy) + 7.2
    fr = [0.0, 0.4, 0.75, 1.0]

    def ring_pt(i, f):
        return (cx + (ring[i][0] - cx) * f, cy + (ring[i][1] - cy) * f)

    def ceil_z(i, f):
        return tops[i] + (zc - tops[i]) * (1.0 - f) ** 1.3

    for k in range(len(fr) - 1):
        f0, f1 = fr[k], fr[k + 1]
        for i in range(n):
            j = (i + 1) % n
            corners = [(i, 0.0), (j, f1), (i, f1)] if k == 0 else [(i, f0), (j, f0), (j, f1), (i, f1)]
            xy = [ring_pt(vi, f) for vi, f in corners]
            P.poly([(x, y, cave_floor(x, y)) for x, y in xy], (0, 0, 1), "cavefloor", tag="floor")
            P.poly([(x, y, ceil_z(vi, f)) for (x, y), (vi, f) in zip(xy, corners)], (0, 0, -1), "rockface", tag="ceiling")
    ring_walls(P, ring, cave_floor, lambda i, x, y: tops[i], lambda i: "mossy" if i in (3, 4, 15, 16) else "rockface", mouths=CAVE_MOUTHS, tag="wall")
    for k in range(16):                                                                                         # stalactites hang from the ceiling
        ang, rad = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.15, 0.9)
        x, y = cx + math.cos(ang) * 8.5 * rad, cy + math.sin(ang) * 8.0 * rad
        h = rnd.uniform(0.7, 1.7)
        P.frustum(x, y, zc + (min(tops) - zc) * rad - h + 0.25, 0.03, rnd.uniform(0.15, 0.32), h, "rockface", 8, tag="deco")
    for k in range(9):                                                                                          # stalagmites stand on the floor, out of the way of the path
        ang, rad = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.35, 0.9)
        x, y = cx + math.cos(ang) * 8.5 * rad, cy + math.sin(ang) * 8.0 * rad
        if abs(y - 5.0) < 2.6:
            continue
        P.frustum(x, y, cave_floor(x, y) - 0.05, rnd.uniform(0.2, 0.4), 0.05, rnd.uniform(0.5, 1.1), "rockface", 8, tag="stalagmite")
    for x, y, r in ((36.0, 6.5, 0.9), (43.0, 8.5, 0.7), (40.5, 0.5, 0.8), (34.5, 10.5, 0.6)):                  # glossy puddles
        P.poly([(x + r * math.cos(t * math.pi / 4), y + 0.7 * r * math.sin(t * math.pi / 4), cave_floor(x, y) + 0.015) for t in range(8)], (0, 0, 1), "puddle", tag="puddle")
    return P


def ossuary():
    P = Parts()
    ring = [(float(x), float(y)) for x, y in E_RING]
    ring_walls(P, ring, lambda x, y: Z_E, lambda i, x, y: E_CZ, lambda i: "bones" if i in (0, 2, 4, 6) else "ashlar",
               doors={7: [(3.0, 3.0, Z_E + DOOR_H)], 1: [(6.5, 3.0, Z_E + DOOR_H)]}, reveal=T, reveal_mat="mossy")
    x0, y0, x1, y1 = E_PIT_RECT
    for pts in ([(54, 10), (58, 14), (x0, 14), (x0, -2), (58, -2), (54, 2)], [(x1, 14), (72, 14), (76, 10), (76, 2), (72, -2), (x1, -2)],
                [(x0, y1), (x1, y1), (x1, 14), (x0, 14)], [(x0, -2), (x1, -2), (x1, y0), (x0, y0)]):             # the slate floor round the pit
        P.poly([(px, py, Z_E) for px, py in pts], (0, 0, 1), "slate", tag="floor")
    P.quad((x0, y1, E_PIT), (x1, y1, E_PIT), (x1, y1, Z_E), (x0, y1, Z_E), "ashlar", [(x0, E_PIT), (x1, E_PIT), (x1, Z_E), (x0, Z_E)], tag="wall")
    P.quad((x1, y0, E_PIT), (x0, y0, E_PIT), (x0, y0, Z_E), (x1, y0, Z_E), "ashlar", [(-x1, E_PIT), (-x0, E_PIT), (-x0, Z_E), (-x1, Z_E)], tag="wall")
    P.quad((x0, y0, Z_E - 0.25), (x0, y1, Z_E - 0.25), (x0, y1, Z_E), (x0, y0, Z_E), "ashlar", [(y0, Z_E - 0.25), (y1, Z_E - 0.25), (y1, Z_E), (y0, Z_E)], tag="wall")
    P.quad((x1, y1, Z_E - 0.25), (x1, y0, Z_E - 0.25), (x1, y0, Z_E), (x1, y1, Z_E), "ashlar", [(-y1, Z_E - 0.25), (-y0, Z_E - 0.25), (-y0, Z_E), (-y1, Z_E)], tag="wall")
    for k in range(1, 6):                                                                                       # the pit's stairs from both ends: five steps of 0.25 m, then the floor
        top = Z_E - 0.25 * k
        P.box(x0 + 0.5 * (k - 1), y0, E_PIT, x0 + 0.5 * k, y1, top, "ashlar", tag="stair")
        P.box(x1 - 0.5 * k, y0, E_PIT, x1 - 0.5 * (k - 1), y1, top, "ashlar", tag="stair")
    P.poly([(x0 + 2.5, y0, E_PIT), (x1 - 2.5, y0, E_PIT), (x1 - 2.5, y1, E_PIT), (x0 + 2.5, y1, E_PIT)], (0, 0, 1), "cavefloor", tag="pitfloor")
    cx0, cy0, cx1, cy1 = 59.0, 1.0, 71.0, 11.0                                                                   # the cupola over the middle
    for pts in ([(54, 10), (58, 14), (cx0, 14), (cx0, -2), (58, -2), (54, 2)], [(cx1, 14), (72, 14), (76, 10), (76, 2), (72, -2), (cx1, -2)],
                [(cx0, cy1), (cx1, cy1), (cx1, 14), (cx0, 14)], [(cx0, -2), (cx1, -2), (cx1, cy0), (cx0, cy0)]):
        P.poly([(px, py, E_CZ) for px, py in pts], (0, 0, -1), "ashlar", tag="ceiling")
    P.poly([(cx0, cy0, E_CUPOLA), (cx1, cy0, E_CUPOLA), (cx1, cy1, E_CUPOLA), (cx0, cy1, E_CUPOLA)], (0, 0, -1), "brick", tag="cap")
    P.quad((cx0, cy1, E_CZ), (cx1, cy1, E_CZ), (cx1, cy1, E_CUPOLA), (cx0, cy1, E_CUPOLA), "brick", [(0, E_CZ), (12, E_CZ), (12, E_CUPOLA), (0, E_CUPOLA)], tag="reveal")
    P.quad((cx1, cy0, E_CZ), (cx0, cy0, E_CZ), (cx0, cy0, E_CUPOLA), (cx1, cy0, E_CUPOLA), "brick", [(0, E_CZ), (12, E_CZ), (12, E_CUPOLA), (0, E_CUPOLA)], tag="reveal")
    P.quad((cx0, cy0, E_CZ), (cx0, cy1, E_CZ), (cx0, cy1, E_CUPOLA), (cx0, cy0, E_CUPOLA), "brick", [(0, E_CZ), (10, E_CZ), (10, E_CUPOLA), (0, E_CUPOLA)], tag="reveal")
    P.quad((cx1, cy1, E_CZ), (cx1, cy0, E_CZ), (cx1, cy0, E_CUPOLA), (cx1, cy1, E_CUPOLA), "brick", [(0, E_CZ), (10, E_CZ), (10, E_CUPOLA), (0, E_CUPOLA)], tag="reveal")
    return P


def vault_ring(apothem):
    return regular_ring(VAULT_C[0], VAULT_C[1], apothem, 12, -90.0)


def vault():
    P = Parts()
    ring = vault_ring(VAULT_APOTHEM)
    top = Z_E + 4.8
    ring_walls(P, ring, lambda x, y: Z_E, lambda i, x, y: top, lambda i: "marble", doors={0: [(1.875, 3.0, Z_E + 3.0)]}, reveal=0.0)

    def disc(apothem, z, mat, normal=(0, 0, 1), inner=None):
        outer = vault_ring(apothem)
        tag = "floor" if normal[2] > 0 else "ceiling"
        if inner is None:
            P.poly([(x, y, z) for x, y in outer], normal, mat, tag=tag)
            return
        inn = vault_ring(inner)
        for i in range(12):
            j = (i + 1) % 12
            P.poly([(outer[i][0], outer[i][1], z), (outer[j][0], outer[j][1], z), (inn[j][0], inn[j][1], z), (inn[i][0], inn[i][1], z)], normal, mat, tag=tag)

    def band(apothem, z0, z1, mat, inward):
        rg = vault_ring(apothem)
        for i in range(12):
            a, b = rg[i], rg[(i + 1) % 12]
            uv = [(i * 2, z0), (i * 2 + 2, z0), (i * 2 + 2, z1), (i * 2, z1)]
            if inward:
                P.quad((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1), mat, uv, tag="wall")
            else:
                P.quad((b[0], b[1], z0), (a[0], a[1], z0), (a[0], a[1], z1), (b[0], b[1], z1), mat, uv, tag="wall")

    disc(VAULT_APOTHEM, Z_E, "marble", inner=5.2)                                                               # the floor steps up in rings to a plinth
    band(5.2, Z_E, Z_E + 0.25, "ashlar", False)
    disc(5.2, Z_E + 0.25, "ashlar", inner=3.6)
    band(3.6, Z_E + 0.25, Z_E + 0.5, "marble", False)
    disc(3.6, Z_E + 0.5, "marble", inner=2.4)
    band(2.4, Z_E + 0.5, Z_E + 0.75, "ashlar", False)
    disc(2.4, Z_E + 0.75, "slate")
    z = top                                                                                                     # the dome: stepped rings
    disc(VAULT_APOTHEM, z, "ashlar", (0, 0, -1), inner=5.4)
    band(5.4, z, z + 1.1, "ashlar", True)
    z += 1.1
    disc(5.4, z, "brick", (0, 0, -1), inner=3.8)
    band(3.8, z, z + 1.1, "brick", True)
    z += 1.1
    disc(3.8, z, "ashlar", (0, 0, -1), inner=2.2)
    band(2.2, z, z + 1.0, "ashlar", True)
    z += 1.0
    disc(2.2, z, "marble", (0, 0, -1))
    return P


# ----------------------------------------------------------------------------- the dressing
def props():
    P = Parts()
    rnd = random.Random(2077)
    for k in range(18):                                                                                         # B: a heap of bones in the north alcove, skulls on it
        x, y = rnd.uniform(14.0, 16.0), rnd.uniform(8.0, 9.2)
        a = rnd.uniform(0, math.pi)
        z = 0.1 + 0.03 * (k % 3)
        P.bar((x - 0.35 * math.cos(a), y - 0.35 * math.sin(a), z), (x + 0.35 * math.cos(a), y + 0.35 * math.sin(a), z + 0.04), 0.035, 0.035, "bones", tag="deco")
    for k in range(5):
        P.frustum(14.4 + 0.5 * k, 8.6 + 0.3 * math.sin(k), 0.1 + 0.12 * (k % 2), 0.11, 0.09, 0.14, "bones", 8, tag="deco")
    for k in range(12):                                                                                         # B: a collapse of rock against the north wall of the lower gallery
        cx, cy = 26.3 + rnd.uniform(-1.2, 1.2), 7.0 - rnd.uniform(0.0, 0.8)
        P.bar((cx - 0.5, cy - 0.2, -0.75), (cx + 0.3, cy + 0.2, -0.75 + max(0.4, 1.5 - 0.5 * abs(cx - 26.3) - 0.3 * (7.0 - cy) + rnd.uniform(-0.2, 0.3))), rnd.uniform(0.35, 0.6),
              rnd.uniform(0.3, 0.5), "rockface", (1.0, 0.0, 0.0), tag="rubble")
    heap = random.Random(7)                                                                                     # C: rubble in the north-west, timber frames over the diggings, a bench
    for i in range(16):
        cx, cy = 33.2 + heap.uniform(-1.7, 1.7), 11.0 + heap.uniform(-1.6, 1.6)
        gz = cave_floor(cx, cy)
        P.bar((cx - 0.5, cy - 0.3, gz - 0.1), (cx + 0.4, cy + 0.5, gz + max(0.5, 1.9 - 0.11 * (abs(cx - 33.2) + abs(cy - 11.0)) + heap.uniform(-0.2, 0.3))),
              heap.uniform(0.4, 0.8), heap.uniform(0.3, 0.6), "rockface", (1.0, 0.0, 0.0), tag="rubble")
    for fx in (37.5, 43.0):
        for fy in (5.2, 8.6):
            P.box(fx - 0.2, fy - 0.2, cave_floor(fx, fy) - 0.1, fx + 0.2, fy + 0.2, cave_floor(fx, fy) + 3.0, "beam", tag="post")
        P.box(fx - 0.25, 5.0, cave_floor(fx, 7.0) + 3.0, fx + 0.25, 8.8, cave_floor(fx, 7.0) + 3.3, "beam", tag="beam")
    gz = cave_floor(42.0, -2.3)
    P.box(40.2, -3.0, gz - 0.1, 43.8, -2.0, gz + 0.9, "timber", tag="bench")
    cols = [(58.5, 4.0, "round"), (58.5, 8.0, "pier"), (71.5, 4.0, "broken"), (71.5, 8.0, "round"), (65.0, -0.6, "pier"), (69.0, 12.3, "round")]       # E: three kinds of column
    for x, y, kind in cols:
        z0 = Z_E
        if kind == "round":
            P.cyl(x, y, z0, 0.78, 0.35, "ashlar", 12, tag="column")
            P.cyl(x, y, z0 + 0.35, 0.52, E_CZ - z0 - 0.85, "marble", 14, tag="column")
            P.cyl(x, y, E_CZ - 0.5, 0.80, 0.5, "ashlar", 12, tag="column")
        elif kind == "pier":
            P.box(x - 0.55, y - 0.55, z0, x + 0.55, y + 0.55, E_CZ, "brick", tag="column")
            P.box(x - 0.7, y - 0.7, z0, x + 0.7, y + 0.7, z0 + 0.4, "ashlar", tag="column")
            P.box(x - 0.7, y - 0.7, E_CZ - 0.4, x + 0.7, y + 0.7, E_CZ, "ashlar", tag="column")
        else:
            P.cyl(x, y, z0, 0.78, 0.35, "ashlar", 12, tag="column")
            P.cyl(x, y, z0 + 0.35, 0.52, 2.0, "marble", 14, tag="column")
            for k in range(7):                                                                                   # the fallen drum and its chips
                a = rnd.uniform(0, 2 * math.pi)
                P.bar((x + math.cos(a) * 0.9, y + math.sin(a) * 0.9, z0 + 0.15), (x + math.cos(a) * 1.5, y + math.sin(a) * 1.5, z0 + 0.25), rnd.uniform(0.1, 0.25), rnd.uniform(0.1, 0.2), "marble", tag="rubble")

    def coffin(x, y, yaw, kind, mat="marble"):
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        ax, ay = c * 1.2, s * 1.2
        P.bar((x - ax, y - ay, Z_E + 0.3), (x + ax, y + ay, Z_E + 0.3), 0.48, 0.30, "ashlar", tag="coffin")      # the base: a bar 0.96 wide and 0.6 high
        z = Z_E + 0.6
        if kind == "closed":
            P.bar((x - ax * 1.04, y - ay * 1.04, z + 0.14), (x + ax * 1.04, y + ay * 1.04, z + 0.14), 0.5, 0.14, mat, tag="coffin")
        elif kind == "ajar":
            P.bar((x - ax * 1.04 + s * 0.35, y - ay * 1.04 - c * 0.35, z + 0.14), (x + ax * 1.04 + s * 0.35, y + ay * 1.04 - c * 0.35, z + 0.14), 0.5, 0.14, mat, tag="coffin")
        elif kind == "open":
            P.bar((x - ax - s * 0.9, y - ay + c * 0.9, Z_E), (x + ax - s * 0.9, y + ay + c * 0.9, Z_E + 0.5), 0.5, 0.12, mat, tag="coffin")      # the lid leaning against the base
            P.bar((x - 0.7 * c, y - 0.7 * s, z + 0.02), (x + 0.5 * c, y + 0.5 * s, z + 0.05), 0.12, 0.05, "bones", tag="deco")
        else:                                                                                                    # broken: the lid in two halves
            P.bar((x - ax, y - ay, z + 0.14), (x - 0.1 * c, y - 0.1 * s, z + 0.26), 0.5, 0.14, mat, tag="coffin")
            P.bar((x + 0.2 * c, y + 0.2 * s, z + 0.3), (x + ax, y + ay, z + 0.14), 0.5, 0.14, mat, tag="coffin")

    for x, y, yaw, kind in ((56.8, 12.3, -45.0, "closed"), (73.2, 12.3, 45.0, "ajar"), (73.6, -0.2, -45.0, "open"), (56.6, -0.2, 45.0, "broken"),
                             (61.5, -1.0, 0.0, "closed"), (68.5, -1.0, 0.0, "ajar"), (61.0, 13.0, 0.0, "open")):
        coffin(x, y, yaw, kind)
    P.box(60.0, 0.95, Z_E, 70.0, 1.55, Z_E + 1.0, "ashlar", tag="kerb")                                          # a kerb round the pit's long sides
    P.box(60.0, 10.45, Z_E, 70.0, 11.05, Z_E + 1.0, "ashlar", tag="kerb")
    P.box(64.0, 5.2, E_PIT, 66.0, 6.8, E_PIT + 0.7, "marble", tag="altar")                                      # E: the skull altar in the pit, and heaps of bones
    P.box(63.8, 5.0, E_PIT + 0.7, 66.2, 7.0, E_PIT + 0.85, "ashlar", tag="altar")
    for k in range(14):
        P.frustum(64.0 + 0.16 * (k % 5), 5.3 + 0.38 * (k // 5), E_PIT + 0.85 + (0.1 if k % 2 else 0.0), 0.11, 0.10, 0.12, "bones", 8, tag="deco")
    for k in range(60):
        x, y = rnd.uniform(63.2, 66.8), rnd.uniform(2.0, 10.0)
        if 4.9 < y < 7.1 and 63.8 < x < 66.2:
            continue
        a = rnd.uniform(0, math.pi)
        P.bar((x - 0.3 * math.cos(a), y - 0.3 * math.sin(a), E_PIT + 0.02), (x + 0.3 * math.cos(a), y + 0.3 * math.sin(a), E_PIT + 0.05 + 0.03 * (k % 3)), 0.03, 0.03, "bones", tag="deco")
    P.cyl(65.0, 6.0, E_CUPOLA - 1.35, 1.1, 0.12, "iron", 16, tag="deco")                                        # an iron ring hanging from the cupola on three chains
    for k in range(3):
        a = k * 2 * math.pi / 3
        P.bar((65.0 + 1.0 * math.cos(a), 6.0 + 1.0 * math.sin(a), E_CUPOLA - 1.3), (65.0, 6.0, E_CUPOLA - 0.02), 0.015, 0.015, "iron", tag="chain")
    gg = G                                                                                                       # G: piers, balustrades, banners, the long table, statues
    for x in (57.5, 72.5):
        for y in (26.5, 32.0, 37.5):
            if (x < 60 and y < W_PLAT_Y0) or (x > 70 and y > E_PLAT_Y1):
                continue
            P.box(x - 0.45, y - 0.45, gg["fz"], x + 0.45, y + 0.45, BALCONY_Z - 0.4, "ashlar", tag="pier")
            P.box(x - 0.45, y - 0.45, BALCONY_Z, x + 0.45, y + 0.45, gg["cz"], "brick", tag="pier")
    for x, y in ((57.5, 25.0), (72.3, 36.4)):                                                                   # marble columns at the stairs' feet
        P.cyl(x, y, gg["fz"], 0.5, gg["cz"] - gg["fz"], "marble", 14, tag="column")
    for k in range(6):
        for x, y0 in ((57.4, W_PLAT_Y0 + 0.2), (72.6, 23.2)):
            y = y0 + k * 1.9
            if y < 41.0 and (x < 60 or y < E_PLAT_Y1):
                P.box(x - 0.1, y - 0.1, BALCONY_Z, x + 0.1, y + 0.1, BALCONY_Z + 0.95, "beam", tag="rail")
    P.box(57.3, W_PLAT_Y0, BALCONY_Z + 0.9, 57.5, 41.0, BALCONY_Z + 1.0, "beam", tag="rail")
    P.box(72.5, 23.0, BALCONY_Z + 0.9, 72.7, E_PLAT_Y1, BALCONY_Z + 1.0, "beam", tag="rail")
    for x, y in ((57.55, 34.0), (57.55, 38.0), (72.45, 27.0), (72.45, 31.0)):                                   # banners hung on the balcony fronts
        P.quad((x, y - 0.5, BALCONY_Z - 0.3), (x, y + 0.5, BALCONY_Z - 0.3), (x, y + 0.5, BALCONY_Z - 2.3), (x, y - 0.5, BALCONY_Z - 2.3), "banner", [(0, 0), (1, 0), (1, -2), (0, -2)], tag="banner")
        P.quad((x, y + 0.5, BALCONY_Z - 0.3), (x, y - 0.5, BALCONY_Z - 0.3), (x, y - 0.5, BALCONY_Z - 2.3), (x, y + 0.5, BALCONY_Z - 2.3), "banner", [(0, 0), (1, 0), (1, -2), (0, -2)], tag="banner")
    P.box(60.5, 29.5, gg["fz"], 63.5, 30.5, gg["fz"] + 0.85, "timber", tag="table")
    P.box(60.5, 28.6, gg["fz"], 63.5, 29.1, gg["fz"] + 0.45, "timber", tag="bench")
    P.box(60.5, 30.9, gg["fz"], 63.5, 31.4, gg["fz"] + 0.45, "timber", tag="bench")
    for sx in (60.2, 68.8):                                                                                      # two knights in marble flank the vault door
        z0 = gg["fz"] + 0.6
        P.box(sx - 0.45, 39.3, z0, sx + 0.45, 40.3, z0 + 0.5, "marble", tag="statue")
        P.box(sx - 0.2, 39.55, z0 + 0.5, sx + 0.2, 39.95, z0 + 1.35, "marble", tag="statue")
        P.box(sx - 0.38, 39.5, z0 + 1.25, sx + 0.38, 40.0, z0 + 1.5, "marble", tag="statue")
        P.box(sx - 0.14, 39.6, z0 + 1.5, sx + 0.14, 39.9, z0 + 1.82, "marble", tag="statue")
        P.bar((sx - 0.45, 39.6, z0 + 1.3), (sx - 0.45, 39.6, z0 + 0.45), 0.05, 0.03, "iron", tag="statue")
        P.box(sx + 0.3, 39.45, z0 + 0.7, sx + 0.52, 39.52, z0 + 1.3, "iron", tag="statue")
    base = Z_E + 0.75                                                                                            # V: the old lord's sarcophagus on the plinth, gold heaped by the chest
    c0 = VAULT_C
    P.box(c0[0] - 1.6, c0[1] - 0.7, base, c0[0] + 1.6, c0[1] + 0.7, base + 0.7, "marble", tag="tomb")
    P.box(c0[0] - 1.7, c0[1] - 0.8, base + 0.7, c0[0] + 1.7, c0[1] + 0.8, base + 0.9, "ashlar", tag="tomb")
    P.box(c0[0] - 1.4, c0[1] - 0.5, base + 0.9, c0[0] + 1.4, c0[1] + 0.5, base + 1.05, "marble", tag="tomb")
    P.box(c0[0] - 0.9, c0[1] - 0.1, base + 1.05, c0[0] + 0.6, c0[1] + 0.1, base + 1.1, "gold", tag="deco")
    P.box(c0[0] - 0.1, c0[1] - 0.45, base + 1.05, c0[0] + 0.1, c0[1] + 0.45, base + 1.1, "gold", tag="deco")
    for px, py, r in ((c0[0] - 2.4, c0[1] + 5.0, 0.8), (c0[0] + 2.7, c0[1] + 4.6, 0.6), (c0[0] + 0.6, c0[1] + 5.4, 0.5)):
        P.frustum(px, py, Z_E, r, 0.12, r * 0.8, "gold", 12, tag="gold")
    for k in range(10):
        a, rr = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.9, 1.3)
        P.cyl(c0[0] - 2.4 + math.cos(a) * rr, c0[1] + 5.0 + math.sin(a) * rr, Z_E, 0.1, 0.02, "gold", 8, tag="deco")
    return P


def gs_props():
    """Props from the GameSDK sample's own assets (the engine finds them in its paks): [{name, mesh, x, y, z, yaw}]."""
    B = "objects/props/maritime/wooden_barrel/"
    M_ = "objects/props/mine/"
    A_ = "objects/props/misc/pottery/amphora/"
    C_ = "objects/props/misc/lights/candles/holder_standing_a/candle_holder_standing_a.cgf"
    vc = VAULT_C
    out = [
        dict(name="UC_Barrel_A1", mesh=B + "wooden_barrel_a.cgf", x=0.7, y=0.7, z=0.0, yaw=10.0),
        dict(name="UC_Barrel_A2", mesh=B + "wooden_barrel_a.cgf", x=1.7, y=0.6, z=0.0, yaw=80.0),
        dict(name="UC_Barrel_C1", mesh=B + "wooden_barrel_b.cgf", x=45.5, y=13.0, z=None, yaw=30.0),
        dict(name="UC_Barrel_G1", mesh=B + "wooden_barrel_a.cgf", x=61.0, y=23.7, z=Z_E, yaw=0.0),
        dict(name="UC_Barrel_G2", mesh=B + "wooden_barrel_a.cgf", x=62.1, y=23.6, z=Z_E, yaw=45.0),
        dict(name="UC_Cart_C", mesh=M_ + "minecart_pile.cgf", x=45.0, y=-2.6, z=None, yaw=15.0),
        dict(name="UC_Planks_C1", mesh=M_ + "random_woodplanks.cgf", x=33.0, y=-2.9, z=None, yaw=0.0),
        dict(name="UC_Planks_C2", mesh=M_ + "boards01.cgf", x=47.0, y=9.5, z=None, yaw=90.0),
        dict(name="UC_Pole_C1", mesh=M_ + "pole01.cgf", x=31.0, y=10.5, z=None, yaw=0.0),
        dict(name="UC_Amphora_V1", mesh=A_ + "pottery_amphora.cgf", x=vc[0] + 4.9, y=vc[1] + 1.0, z=Z_E, yaw=0.0),
        dict(name="UC_Amphora_V2", mesh=A_ + "pottery_amphora.cgf", x=vc[0] + 5.2, y=vc[1] - 0.2, z=Z_E, yaw=90.0),
        dict(name="UC_Amphora_V3", mesh=A_ + "pottery_amphora.cgf", x=vc[0] - 5.0, y=vc[1] + 1.2, z=Z_E, yaw=45.0),
        dict(name="UC_Amphora_V4", mesh=A_ + "pottery_amphora_dmg_a.cgf", x=vc[0] - 5.3, y=vc[1] - 0.1, z=Z_E, yaw=0.0),
        dict(name="UC_Candle_V1", mesh=C_, x=vc[0] - 2.3, y=vc[1] - 1.4, z=Z_E + 0.75, yaw=0.0),
        dict(name="UC_Candle_V2", mesh=C_, x=vc[0] + 2.3, y=vc[1] - 1.4, z=Z_E + 0.75, yaw=0.0),
    ]
    for g in out:
        if g["z"] is None:
            g["z"] = floor_z(g["x"], g["y"])
    return out


def door():
    """The stone slab of the vault door in ITS OWN frame (origin at the foot, centred in the doorway), iron bands and the key plate on the hall side (-y)."""
    P = Parts()
    hw, ht, h = GATE["w"] / 2.0, GATE["t"] / 2.0, GATE["h"]
    P.box(-hw, -ht, 0.0, hw, ht, h, "marble", tag="door")
    for z in (0.55, 2.25):
        P.box(-hw, -ht - 0.04, z, hw, -ht, z + 0.15, "iron", tag="band")
        P.box(-hw, ht, z, hw, ht + 0.04, z + 0.15, "iron", tag="band")
    P.box(-0.07, -ht - 0.04, 0.0, 0.07, -ht, h, "iron", tag="strap")
    P.box(-0.24, -ht - 0.06, 1.18, 0.24, -ht, 1.78, "iron", tag="plate")
    P.box(-0.07, -ht - 0.075, 1.38, 0.07, -ht - 0.06, 1.58, "slate", tag="keyhole")
    return P


# ----------------------------------------------------------------------------- the people and things (local coordinates)
def _npc(name, x, y, yaw, z=None, **props):
    base = {"PatrolPath": "", "PatrolRadius": 2.0, "PatrolPoints": 3, "PauseSeconds": 4.0, "BehaviorTree": "", "Autonomous": False, "SightRange": 8.0}
    base.update(props)
    return dict(name=name, cls="Villager", x=x, y=y, z=floor_z(x, y) if z is None else z, yaw=yaw, comps={"CNpcComponent": base})


DIGGER = {"Role": "Bandit", "CharacterMaterial": "materials/ashvale/people/bandit.mtl", "MaxHealth": 70.0, "AttackDamage": 12.0, "CombatSight": 16.0,
          "WeaponLength": 0.9, "PatrolRadius": 3.0, "PatrolPoints": 3, "PauseSeconds": 5.0, "RunSpeed": 3.5}
ARCHER = dict(DIGGER, BowRange=22.0, MaxHealth=50.0, AttackDamage=8.0, WeaponLength=0.0, PatrolRadius=1.0, CombatSight=22.0)
CORBIN = dict(DIGGER, MaxHealth=150.0, AttackDamage=18.0, WeaponLength=1.1, PatrolRadius=2.0, DeathFlag="vault_key", CharacterMaterial="materials/ashvale/people/bandit_chief.mtl")

YAW = {"N": 0.0, "S": 180.0, "E": -90.0, "W": 90.0}


def _ring_torch(ring, edge, t):
    """(x, y, yaw) of a torch on polygon wall `edge` at fraction t along it, looking into the room (the ring is clockwise: the interior is on the right)."""
    p, q = ring[edge], ring[(edge + 1) % len(ring)]
    x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    nx, ny = dy / L, -dx / L
    return x, y, math.degrees(math.atan2(-nx, ny))               # the torch's front is its local +Y: Rz(yaw) * (0, 1) = (nx, ny)


def torches():
    """[(x, y, z, yaw)] wall torches on the inner faces."""
    out = []

    def on_wall(x, y, face, z):
        out.append((x, y, z, YAW[face]))

    for x, y, f in ((0.0, 2.5, "E"), (5.0, 0.0, "N"), (10.0, 1.5, "W"), (7.5, 0.0, "N")):
        on_wall(x, y, f, 1.4)
    for x, y, f, z in ((11.5, 3.5, "N", 1.4), (17.5, 6.5, "S", 1.4), (21.0, 3.5, "N", 1.4), (24.0, 6.5, "S", 0.65), (28.0, 3.5, "N", 0.65), (17.5, -0.5, "N", 1.4), (14.5, 9.5, "S", 1.4)):
        on_wall(x, y, f, z)
    for i, t in ((1, 0.2), (3, 0.5), (5, 0.4), (7, 0.5), (9, 0.4), (13, 0.5), (15, 0.5), (17, 0.5), (19, 0.5)):
        x, y, yaw = _ring_torch(CAVE, i, t)
        out.append((x, y, cave_floor(x, y) + 1.5, yaw))
    on_wall(50.5, 3.5, "N", D_FLOOR0 - 0.25 + 1.4)
    for i, t in ((0, 0.5), (2, 0.5), (4, 0.5), (6, 0.5), (1, 0.2), (1, 0.85), (3, 0.5), (5, 0.2), (5, 0.85), (7, 0.15), (7, 0.9)):
        x, y, yaw = _ring_torch(E_RING, i, t)
        out.append((x, y, Z_E + 1.5, yaw))
    on_wall(63.0, 18.0, "E", Z_E + 1.4)
    on_wall(69.5, 19.0, "N", Z_E + 1.4)
    for x, y, f in ((54.0, 26.0, "E"), (54.0, 38.0, "E"), (58.0, 23.0, "N"), (66.0, 23.0, "N"), (76.0, 26.0, "W"), (58.0, 41.0, "S"), (71.0, 41.0, "S"), (76.0, 38.0, "W")):
        on_wall(x, y, f, Z_E + 1.5)
    vr = vault_ring(VAULT_APOTHEM)
    for i in (1, 3, 5, 7, 9):
        x, y, yaw = _ring_torch(vr, i, 0.5)
        out.append((x, y, Z_E + 1.5, yaw))
    return out


ROOM_LIGHTS = [("A", 5.0, 5.0, 4.6, 7.0), ("B", 17.0, 5.0, 3.0, 6.0), ("B2", 26.0, 5.0, 2.4, 5.0), ("C1", 37.0, 3.0, 3.0, 9.0), ("C2", 41.0, 9.5, 3.0, 8.0),
               ("E", 65.0, 6.0, -0.4, 11.0), ("G", 65.0, 32.0, 2.4, 12.0), ("V", VAULT_C[0], VAULT_C[1] + 1.0, 0.2, 7.0),
               ("T", 69.5, 20.5, -1.0, 5.0)]                    # T: the rock tunnel's bend, which two wall torches left at a mean luma of 21
ROOM_LIGHT_BRIGHTNESS = 0.05


def entities():
    """Everything the builder places besides the meshes: [{name, cls, x, y, z, yaw, comps: {component: {property: value}}}] in LOCAL metres."""
    out = []
    out.append(dict(name="UndercroftStart", cls="AshvaleMarker", x=2.5, y=3.5, z=0.1, yaw=-90.0, comps={"CAshvaleMarkerComponent": {"Kind": "PlayerStart"}}))
    out.append(dict(name="UndercroftRespawn", cls="AshvaleMarker", x=3.5, y=4.5, z=0.1, yaw=-90.0, comps={"CAshvaleMarkerComponent": {"Kind": "Respawn"}}))
    out.append(dict(name="UndercroftMenuCamera", cls="AshvaleMarker", x=5.0, y=5.0, z=3.0, yaw=-90.0, comps={"CAshvaleMarkerComponent": {"Kind": "MenuCamera"}}))
    out.append(dict(name="UndercroftArrival", cls="AshvaleMarker", x=2.5, y=3.0, z=0.1, yaw=-90.0, comps={"CAshvaleMarkerComponent": {"Kind": "Spot"}}))
    out.append(dict(name="UndercroftExit", cls="Doorway", x=9.2, y=8.7, z=LANDING_Z, yaw=0.0,
                    comps={"CDoorwayComponent": {"Label": "stair", "Prompt": "Climb the stair to the tower", "TargetLevel": "ashvale", "TargetSpot": "TowerHatchExit",
                                                 "RequiresFlag": "", "LockedText": "", "Range": 2.8}}))
    out.append(dict(name="VaultDoor", cls="LockedGate", x=GATE["x"], y=GATE["y"], z=Z_E, yaw=0.0,
                    comps={"CLockedGateComponent": {"Label": "vault door", "RequiresFlag": "vault_key", "SeenFlag": "saw_vault",
                                                    "LockedText": "A slab of marble seals the vault. A square keyhole is cut in its face. Corbin must have the key.",
                                                    "OpenText": "The key turns. The great slab grinds upward into the wall.", "Material": MATERIAL_FILE,
                                                    "Mesh": "objects/ashvale/undercroft_door_%s.cgf" % VERSION,
                                                    "Size": {"x": GATE["w"], "y": GATE["t"], "z": GATE["h"]}, "Range": 3.0}}))
    out.append(_npc("Cuthbert", 17.5, 1.6, 0.0, TalkName="Brother Cuthbert", DialogueId="cuthbert", PatrolRadius=0.6, CharacterMaterial="materials/ashvale/people/priest.mtl", Gait="Slow"))   # an old sexton: the slow walk if he is ever freed
    for name, x, y, yaw in (("Digger1", 35.0, 2.0, 0.0), ("Digger2", 41.0, 9.0, 180.0), ("Digger3", 44.0, 0.5, 90.0)):
        out.append(_npc(name, x, y, yaw, **DIGGER))
    for name, x, y, yaw in (("Guard1", 72.8, 6.0, 90.0), ("Guard2", 66.6, 12.6, 180.0)):
        out.append(_npc(name, x, y, yaw, **dict(DIGGER, Gait="Stealth")))     # the vault's guards creep (MotionBricks walk_stealth); chasing is still a run
    out.append(_npc("Corbin", 65.0, 31.0, 180.0, **CORBIN))
    for name, x, y, z in (("CryptArcher1", 55.5, 36.5, BALCONY_Z), ("CryptArcher2", 74.5, 28.0, BALCONY_Z)):
        out.append(_npc(name, x, y, 90.0 if x < 60 else -90.0, z=z, **dict(ARCHER, Gait="Stealth")))

    def chest(name, x, y, yaw, contents, label, z=None, **kw):
        props_ = {"Label": label, "Contents": contents, "RequiresFlag": "", "LockedText": "It is locked."}
        props_.update(kw)
        out.append(dict(name=name, cls="Chest", x=x, y=y, z=floor_z(x, y) if z is None else z, yaw=yaw, comps={"CChestComponent": props_}))
    chest("DiggersSack", 45.5, 12.4, 200.0, "coins:8,bread:1", "diggers' sack")
    chest("OssuaryChest", 55.2, 8.6, 90.0, "coins:12", "reliquary chest")
    chest("CorbinChest", 74.7, 40.2, 180.0, "coins:18,bread:2", "Corbin's strongbox")
    chest("LordsStrongbox", VAULT_C[0], VAULT_C[1] + 5.7, 180.0, "coins:60,lordsword", "the old lord's strongbox", z=Z_E)

    def item(name, x, y, kind, amount, z=None):
        out.append(dict(name=name, cls="Pickup", x=x, y=y, z=(floor_z(x, y) if z is None else z) + 0.02, yaw=20.0, comps={"CPickupComponent": {"Kind": kind, "Amount": amount, "Label": ""}}))
    item("UC_Bread_Hall", 4.0, 2.0, "Bread", 1)
    item("UC_Coins_Gallery", 24.0, 4.2, "Coins", 3)
    item("UC_Bread_Cell", 28.6, 4.4, "Bread", 1)
    item("UC_Coins_Alcove", 15.0, 8.2, "Coins", 2)
    item("UC_Bread_Cavern", 33.0, 4.0, "Bread", 1)
    item("UC_Coins_Cavern", 46.0, 3.0, "Coins", 5)
    item("UC_Bread_Ossuary", 56.5, 6.5, "Bread", 2, z=Z_E)
    item("UC_Coins_Pit", 62.2, 3.0, "Coins", 4, z=Z_E - 0.25 * 3)
    item("UC_Coins_Hall", 56.0, 35.0, "Coins", 6, z=Z_E)
    item("UC_Bread_Hall2", 59.9, 30.0, "Bread", 1, z=Z_E)
    item("UC_Coins_Vault", VAULT_C[0] + 3.5, VAULT_C[1] + 3.2, "Coins", 10, z=Z_E)
    out.append(dict(name="DiggersFire", cls="Interactable", x=36.5, y=1.2, z=cave_floor(36.5, 1.2), yaw=0.0,
                    comps={"CInteractableComponent": {"Mesh": "objects/props/fishing_camp/campfire.cgf", "Label": "diggers' fire", "OnByDefault": True, "UseRange": 3.0}}))
    for k, bx in enumerate((61.0, 68.0)):
        out.append(dict(name="HallBrazier%d" % (k + 1), cls="Interactable", x=bx, y=26.5, z=Z_E, yaw=0.0,
                        comps={"CInteractableComponent": {"Mesh": "objects/props/fishing_camp/campfire.cgf", "Label": "brazier", "OnByDefault": True, "UseRange": 3.0}}))
    for i, (x, y, z, yaw) in enumerate(torches()):
        out.append(dict(name="UC_Torch%02d" % i, cls="NightLamp", x=x, y=y, z=z, yaw=yaw, comps={"CNightLampComponent": {"Kind": "WallTorch", "Brightness": 0.25}}))
    for name, x, y, z, r in ROOM_LIGHTS:
        out.append(dict(name="UC_Fill_" + name, cls="NightLamp", x=x, y=y, z=z, yaw=0.0,
                        comps={"CNightLampComponent": {"Kind": "Room", "Brightness": round(ROOM_LIGHT_BRIGHTNESS * r * r, 3), "Radius": r}}))
    for g in gs_props():
        out.append(dict(name=g["name"], cls="__brush__", x=g["x"], y=g["y"], z=g["z"], yaw=g["yaw"], comps={}, mesh=g["mesh"], scale=1.0))
    return out


# ----------------------------------------------------------------------------- meshes
def clean(parts):
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in parts]


def mesh_specs():
    specs = []
    for name, parts in (("undercroft_shell", shell()), ("undercroft_cave", cave()), ("undercroft_ossuary", ossuary()), ("undercroft_vault", vault()),
                        ("undercroft_props", props()), ("undercroft_door", door())):
        specs.append(dict(name="%s_%s" % (name, VERSION), folder="objects/ashvale", material_file=MATERIAL_FILE, materials=MATERIALS, tiles=TILES, parts=clean(parts)))
    return specs


def placed_meshes():
    """The meshes the builder places at O (all but the door, which belongs to the gate)."""
    return ["objects/ashvale/%s_%s.cgf" % (n, VERSION) for n in ("undercroft_shell", "undercroft_cave", "undercroft_ossuary", "undercroft_vault", "undercroft_props")]


def write_meshes(overwrite=False):
    import pipeline
    out = {}
    mtl = os.path.join(pipeline.ASSETS, "materials", "ashvale", "undercroft_%s.mtl" % VERSION)
    if not os.path.exists(mtl) or overwrite:
        out["material"] = pipeline.write_material(MATERIAL_FILE, SUBMATERIALS, overwrite=True)
    for spec in mesh_specs():
        out[spec["name"]] = pipeline.build_mesh(spec, overwrite=overwrite)
    return out


# ----------------------------------------------------------------------------- the offline check: the plan as a raster
def all_parts():
    return list(shell()) + list(cave()) + list(ossuary()) + list(vault()) + list(props())


def gate_box():
    return (GATE["x"] - GATE["w"] / 2.0, GATE["y"] - GATE["t"] / 2.0, Z_E, GATE["x"] + GATE["w"] / 2.0, GATE["y"] + GATE["t"] / 2.0, Z_E + GATE["h"])


BOUNDS = (-3.0, -8.0, 81.0, 58.0)


def walk_grid(boxes, cell=0.25):
    x0, y0, x1, y1 = BOUNDS
    nx, ny = int((x1 - x0) / cell), int((y1 - y0) / cell)
    cols = {}
    for b in boxes:
        i0, i1 = max(0, int((b[0] - x0) / cell)), min(nx - 1, int((b[3] - x0) / cell - 1e-9))
        j0, j1 = max(0, int((b[1] - y0) / cell)), min(ny - 1, int((b[4] - y0) / cell - 1e-9))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cols.setdefault((i, j), []).append(b)
    grid = {}
    for key, bs in cols.items():
        levels = []
        for top in sorted({b[5] for b in bs if len(b) == 6 or (b[5] - b[2]) <= 0.6}):       # a wall column is standing ground only when it is a low riser (a step), not a wall's top
            if all(not (b[2] < top + BODY_HEIGHT - 1e-6 and b[5] > top + 1e-6) for b in bs):
                levels.append(top)
        if levels:
            grid[key] = levels
    return grid, (x0, y0, cell, nx, ny), cols


def reach(grid, meta, start):
    x0, y0, cell, nx, ny = meta
    si, sj = int((start[0] - x0) / cell), int((start[1] - y0) / cell)
    sl = min(range(len(grid[(si, sj)])), key=lambda k: abs(grid[(si, sj)][k] - start[2]))
    seen = {(si, sj, sl)}
    todo = [(si, sj, sl)]
    while todo:
        i, j, k = todo.pop()
        z = grid[(i, j)][k]
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if n not in grid:
                continue
            for m, zz in enumerate(grid[n]):
                if abs(zz - z) <= STEP + 1e-6 and (n[0], n[1], m) not in seen:
                    seen.add((n[0], n[1], m))
                    todo.append((n[0], n[1], m))
    return seen


AGENT_HEIGHT = 2.05           # the navmesh agent: 16 voxels of 0.125 m tall, 4 voxels (0.5 m) of radius (Assets/scripts/AI/Navigation.xml)
AGENT_RADIUS_CELLS = 2
STATIONARY = ("Cuthbert", "UndercroftExit", "VaultDoor")


def prop_boxes():
    """Stand-ins for the GameSDK props the builder places (their real bounds are not known here: round numbers, a barrel 0.9 m across, the rest 1.6 m)."""
    out = []
    for g in gs_props():
        r = 0.45 if "barrel" in g["name"].lower() else (0.2 if "candle" in g["name"].lower() else 0.8)
        out.append((g["x"] - r, g["y"] - r, g["z"], g["x"] + r, g["y"] + r, g["z"] + (0.9 if "barrel" in g["name"].lower() else 1.0)))
    return out


def agent_reach(boxes, grid, cols, seen_flood, start, meta):
    """The flood again, but only through cells where the AGENT fits: no obstacle taller than a step within its radius, and AGENT_HEIGHT of clear air."""
    x0, y0, cell, nx, ny = meta
    R = AGENT_RADIUS_CELLS

    def fits(i, j, z):
        for di in range(-R, R + 1):
            for dj in range(-R, R + 1):
                for b in cols.get((i + di, j + dj), []):
                    if b[2] < z + AGENT_HEIGHT and b[5] > z + STEP + 1e-6 and not (len(b) == 7 and False):
                        return False
        return True

    si, sj = int((start[0] - x0) / cell), int((start[1] - y0) / cell)
    sl = min(range(len(grid[(si, sj)])), key=lambda k: abs(grid[(si, sj)][k] - start[2]))
    seen = {(si, sj, sl)}
    todo = [(si, sj, sl)]
    ok_cache = {}
    while todo:
        i, j, k = todo.pop()
        z = grid[(i, j)][k]
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if n not in grid:
                continue
            for m, zz in enumerate(grid[n]):
                key = (n[0], n[1], m)
                if abs(zz - z) <= STEP + 1e-6 and key not in seen:
                    if key not in ok_cache:
                        ok_cache[key] = fits(n[0], n[1], zz)
                    if ok_cache[key]:
                        seen.add(key)
                        todo.append(key)
    return seen


def check(verbose=False):
    """-> list of problems (empty = the plan holds): the start reaches every person, chest, item and marker; the sealed vault is unreachable with the door shut and reachable with it open;
    nothing reachable borders an unguarded drop (the complex is enclosed)."""
    problems = []
    start = (2.5, 3.5, 0.0)
    ents = entities()
    base = proxies(all_parts())
    grid, meta, _c = walk_grid(base + [gate_box()])
    seen = reach(grid, meta, start)
    x0, y0, cell, nx, ny = meta
    index = {}
    for i, j, k in seen:
        index.setdefault((i, j), []).append(grid[(i, j)][k])

    def reachable(idx, x, y, z):
        i, j = int((x - x0) / cell), int((y - y0) / cell)
        return any(abs(zz - z) < 0.65 for zz in idx.get((i, j), []))

    in_vault = lambda e: (e["x"] - VAULT_C[0]) ** 2 + (e["y"] - VAULT_C[1]) ** 2 < (VAULT_APOTHEM + 0.5) ** 2 and e["y"] > 41.9 and e["name"] != "VaultDoor"
    vault_names = tuple(e["name"] for e in ents if in_vault(e) and e["cls"] in ("Chest", "Pickup"))
    for e in ents:
        if e["cls"] in ("NightLamp", "__brush__") or e["name"] == "UndercroftMenuCamera":
            continue
        ok = reachable(index, e["x"], e["y"], e["z"])
        if e["name"] in vault_names:
            if ok:
                problems.append("%s is reachable with the vault door shut" % e["name"])
        elif e["name"] == "VaultDoor":
            pass
        elif not ok:
            problems.append("%s at (%.1f, %.1f, %.2f) cannot be reached from the start" % (e["name"], e["x"], e["y"], e["z"]))
    grid2, meta2, cols2 = walk_grid(base)
    seen2 = reach(grid2, meta2, start)
    # the agent (a villager, a bandit: 1 m across, 2 m tall) has to get to every one of them too, through the doors, the cells, over the stairs and past the props
    gridA, metaA, colsA = walk_grid(base + prop_boxes())
    seenA = agent_reach(base, gridA, colsA, None, start, metaA)
    indexA = {}
    for i, j, k in seenA:
        indexA.setdefault((i, j), []).append(gridA[(i, j)][k])
    for e in ents:
        if e["cls"] != "Villager" or e["name"] in STATIONARY:
            continue
        if not reachable(indexA, e["x"], e["y"], e["z"]):
            problems.append("%s at (%.1f, %.1f, %.2f): a navmesh agent (1 m across, 2 m tall) cannot get there from the start" % (e["name"], e["x"], e["y"], e["z"]))
    for name, x, y, z in (("the stair's landing", 9.2, 8.7, LANDING_Z), ("the west balcony", 55.5, 36.5, BALCONY_Z), ("the east balcony", 74.5, 28.0, BALCONY_Z),
                          ("the bottom of the pit", 65.0, 3.0, E_PIT), ("a cell", 17.5, 2.0, 0.0)):
        if not reachable(indexA, x, y, z):
            problems.append("%s: a navmesh agent cannot get there" % name)
    index2 = {}
    for i, j, k in seen2:
        index2.setdefault((i, j), []).append(grid2[(i, j)][k])
    for n in vault_names:
        e = next(e for e in ents if e["name"] == n)
        if not reachable(index2, e["x"], e["y"], e["z"]):
            problems.append("%s cannot be reached even with the vault door open" % n)
    leaks = 0
    for (i, j, k) in seen2:
        z = grid2[(i, j)][k]
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if n in grid2 and any(abs(zz - z) <= STEP + 1e-6 for zz in grid2[n]):
                continue
            if n in grid2 and any(0.0 < z - zz <= 1.25 for zz in grid2[n]):
                continue
            if not any(b[2] < z + BODY_HEIGHT and b[5] > z + 0.3 for b in cols2.get(n, [])):
                leaks += 1
                if verbose and leaks <= 12:
                    px, py = x0 + (n[0] + 0.5) * cell, y0 + (n[1] + 0.5) * cell
                    problems.append("open edge beside (%.2f, %.2f, z %.2f): no wall there" % (px, py, z))
    if leaks:
        problems.append("%d walkable cell edge(s) with no wall beside them: the complex is not sealed" % leaks)
    if verbose:
        cells = {(i, j) for i, j, _k in seen2}
        print("walkable cells reachable from the start: %d (%.0f m2)" % (len(cells), len(cells) * cell * cell))
    return problems


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "meshes":
        res = write_meshes(overwrite="--overwrite" in sys.argv)
        for k, v in res.items():
            print(k, "ok" if v.get("ok", True) else "NOT OK", v.get("polygons", ""), v.get("problems", ""), v.get("bounds_m", {}).get("size", ""))
    elif cmd == "list":
        for e in entities():
            print("%-18s %-14s (%6.1f, %6.1f, %5.2f) yaw %6.1f" % (e["name"], e["cls"], e["x"], e["y"], e["z"], e["yaw"]))
    else:
        probs = check(verbose=True)
        print("\n".join(probs) if probs else "the plan holds")
        sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
