#!/usr/bin/env python3
"""Build the Ashvale level from the game script (Design/ASHVALE_SCRIPT.md).

Everything is placed through the ClaudeBridge (editor Python), in phases. Nothing clears first:
run `clear <prefix>` before re-running a phase, otherwise objects are duplicated (dedupe.py removes duplicates):

    python examples/ashvale/build_ashvale.py buildings     houses with walkable interiors, doors, the chapel, smithy, barn
    python examples/ashvale/build_ashvale.py village       green, well, fields, roads, bridge, shrine, props
    python examples/ashvale/build_ashvale.py camp          the ruined tower and the bandit camp
    python examples/ashvale/build_ashvale.py people        villagers, bandits, chests, markers, fires, woodpile
    python examples/ashvale/build_ashvale.py archers       only Black Wat's two archers (a level that already has its people)
    python examples/ashvale/build_ashvale.py items         loaves of bread and coins lying about, for E to pick up
    python examples/ashvale/build_ashvale.py trim          half-timbering, windows, shutters, door frames and chimneys on the houses
    python examples/ashvale/build_ashvale.py nav           one navigation area over the valley
    python examples/ashvale/build_ashvale.py vegetation    pines, oaks, bushes, rocks
    python examples/ashvale/build_ashvale.py clear <prefix> delete every object whose name starts with prefix

Terrain heights come from heights_ashvale.r32 (ashvale_heightmap.py), so things sit on the ground.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import json
import math
import os
import random
import socket
import sys

import numpy as np

HERE = _paths.ROOT
SCRATCH = os.environ.get("ASHVALE_SCRATCH", os.path.join(HERE, "engine_data"))     # heights_ashvale.r32 (ashvale_heightmap.py engine_data); it used to live in a session scratch folder that vanished
_H = None


def heights():
    """The Ashvale heightmap, loaded on first use (importing this module needs no data file)."""
    global _H
    if _H is None:
        _H = np.fromfile(os.path.join(SCRATCH, "heights_ashvale.r32"), dtype=np.float32).reshape(1024, 1024)
    return _H

CUBE = "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf"
PYRAMID = "%ENGINE%/EngineAssets/Objects/primitive_pyramid.cgf"
CYLINDER = "%ENGINE%/EngineAssets/Objects/primitive_cylinder.cgf"
M = "materials/ashvale/"


def ground(x, y):
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - x0, y - y0
    H = heights()
    return float(H[x0, y0] * (1 - fx) * (1 - fy) + H[x0 + 1, y0] * fx * (1 - fy) +
                 H[x0, y0 + 1] * (1 - fx) * fy + H[x0 + 1, y0 + 1] * fx * fy)


# ----------------------------------------------------------------------------- bridge
def send(cmd, timeout=300):
    with socket.create_connection(("127.0.0.1", 37778), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            d = s.recv(1 << 20)
            if not d:
                break
            buf += d
        return json.loads(buf.decode())


def py(source):
    r = send("bridge.execute_python.b64 " + base64.b64encode(source.encode()).decode())
    out = r.get("output", "")
    try:
        log = json.loads(out).get("log", "")
    except Exception:
        log = out
    if "Traceback" in log:
        raise RuntimeError(log)
    return log


def set_props(entity, component, values):
    r = send("bridge.component.properties %s %s" % (entity, component))
    current = json.loads(r["output"])["properties"]
    current.update(values)
    payload = json.dumps(current, separators=(",", ":"))
    return send("bridge.component.properties %s %s %s" % (entity, component, payload))


# ----------------------------------------------------------------------------- specs
class Scene:
    """Collects objects, then creates them in batches."""
    def __init__(self):
        self.brushes = []     # (name, mesh, x, y, z, sx, sy, sz, rx, ry, rz, material)
        self.entities = []    # (name, cls, x, y, z, rz)
        self.cuts = []        # (name, x, y, floor, [(dx, dy)...], height)
        self.props = []       # (entity, component, dict)

    def box(self, name, x, y, z0, sx, sy, sz, material, rz=0.0, rx=0.0, ry=0.0):
        """A box standing on z0 (the primitive is 1 m and centred)."""
        self.brushes.append((name, CUBE, x, y, z0 + sz / 2.0, sx, sy, sz, rx, ry, rz, material))

    def model(self, name, mesh, x, y, z, rz=0.0, scale=1.0):
        self.brushes.append((name, mesh, x, y, z, scale, scale, scale, 0.0, 0.0, rz, ""))

    def flush(self):
        batch = []
        for b in self.brushes:
            batch.append(b)
            if len(batch) >= 40:
                self._make_brushes(batch)
                batch = []
        if batch:
            self._make_brushes(batch)
        for e in self.entities:
            print(py("""
import sandbox.general as general
import sandbox.object as object
n = general.new_object('Entity', %r, %r, %f, %f, %f)
object.set_rotation(n, 0.0, 0.0, %f)
print('entity', n)
""" % (e[1], e[0], e[2], e[3], e[4], e[5])).strip())
        for c in self.cuts:
            name, x, y, floor, pts, height = c
            print(py("""
import sandbox.general as general
n = general.new_object('NavigationArea', '', %r, %f, %f, %f)
general.set_entity_param(n, 'Height', %f)
general.set_entity_param(n, 'Exclusion', True)
for i, (px, py_) in enumerate(%r):
    general.nav_insert_point(n, i, px, py_, 0.0)
print('cut', n)
""" % (name, x, y, floor, height, pts)).strip())
        for entity, component, values in self.props:
            r = set_props(entity, component, values)
            if not r.get("ok"):
                print("props failed", entity, r)
        print("created %d brushes, %d entities, %d door cuts, %d property sets" %
              (len(self.brushes), len(self.entities), len(self.cuts), len(self.props)))

    @staticmethod
    def _make_brushes(batch):
        batch = [tuple(float(v) if isinstance(v, (float, np.floating, np.integer)) else v for v in b) for b in batch]
        out = py("""
import sandbox.general as general
import sandbox.object as object
made = 0
for name, mesh, x, y, z, sx, sy, sz, rx, ry, rz, mtl in %r:
    n = general.new_object('Brush', mesh, name, x, y, z)
    if not n:
        print('FAILED', name, mesh)
        continue
    object.set_scale(n, sx, sy, sz)
    if rx or ry or rz:
        object.set_rotation(n, rx, ry, rz)
    if mtl:
        object.set_custom_material(n, mtl)
    made += 1
print('brushes', made)
""" % (batch,))
        print(out.strip())


# ----------------------------------------------------------------------------- buildings
WALL_T = 0.3
FLOOR_H = 0.25


def house(sc, name, cx, cy, w, d, door, kind="cottage", wall_h=2.9, wall_mat=M + "plaster", roof_mat="materials/thatch",
          roof_h=2.4, open_side=None, door_width=1.2):
    """A hollow house: floor, four walls (one with a doorway, or one left open), corner timbers,
    a ceiling and a hip roof (the pyramid primitive). A door entity (FarmGate) fills the doorway
    and an exclusion area cuts the doorway from the navmesh, so villagers use the door."""
    z0 = max(ground(cx - w / 2, cy - d / 2), ground(cx + w / 2, cy - d / 2), ground(cx - w / 2, cy + d / 2),
             ground(cx + w / 2, cy + d / 2), ground(cx, cy))
    base = z0 - 0.3
    sc.box(name + "_Floor", cx, cy, base, w, d, 0.3 + FLOOR_H, M + "planks")
    top = z0 + FLOOR_H
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - d / 2, cy + d / 2
    gap = door_width + 0.15

    def wall(side, length_along, fixed, a0, a1):
        """A wall on `side` running from a0 to a1 along its axis, split for a door/opening."""
        pieces = []
        if side == door or side == open_side:
            mid = (a0 + a1) / 2
            half_gap = gap / 2 if side == door else (a1 - a0) * 0.35
            pieces = [(a0, mid - half_gap), (mid + half_gap, a1)]
            if side == door:
                # lintel over the doorway
                lint_h = wall_h - 2.25
                if side in ("N", "S"):
                    sc.box("%s_Wall%s_Lintel" % (name, side), mid, fixed, top + 2.25, gap, WALL_T, lint_h, wall_mat)
                else:
                    sc.box("%s_Wall%s_Lintel" % (name, side), fixed, mid, top + 2.25, WALL_T, gap, lint_h, wall_mat)
            elif side == open_side:
                if side in ("N", "S"):
                    sc.box("%s_Wall%s_Beam" % (name, side), mid, fixed, top + wall_h - 0.4, half_gap * 2, WALL_T, 0.4, M + "timber")
                else:
                    sc.box("%s_Wall%s_Beam" % (name, side), fixed, mid, top + wall_h - 0.4, WALL_T, half_gap * 2, 0.4, M + "timber")
        else:
            pieces = [(a0, a1)]
        for i, (p0, p1) in enumerate(pieces):
            if p1 - p0 < 0.05:
                continue
            c = (p0 + p1) / 2
            if side in ("N", "S"):
                sc.box("%s_Wall%s%d" % (name, side, i), c, fixed, top, p1 - p0, WALL_T, wall_h, wall_mat)
            else:
                sc.box("%s_Wall%s%d" % (name, side, i), fixed, c, top, WALL_T, p1 - p0, wall_h, wall_mat)

    wall("S", w, y0 + WALL_T / 2, x0, x1)
    wall("N", w, y1 - WALL_T / 2, x0, x1)
    wall("W", d, x0 + WALL_T / 2, y0 + WALL_T, y1 - WALL_T)
    wall("E", d, x1 - WALL_T / 2, y0 + WALL_T, y1 - WALL_T)
    for i, (px, py_) in enumerate([(x0, y0), (x1, y0), (x0, y1), (x1, y1)]):
        sc.box("%s_Post%d" % (name, i), px, py_, base, 0.4, 0.4, wall_h + FLOOR_H + 0.3, M + "timber")
    sc.box(name + "_Ceiling", cx, cy, top + wall_h, w, d, 0.2, M + "timber")
    # hip roof: the pyramid is 1 m, centred -- lift by half its height
    sc.brushes.append((name + "_Roof", PYRAMID, cx, cy, top + wall_h + 0.2 + roof_h / 2, w + 1.2, d + 1.2, roof_h, 0.0, 0.0, 0.0, roof_mat))

    if door is not None:
        # hinge, rotation (see FarmGate: leaf along local +X, inside is local +Y)
        if door == "S":
            hinge, rot = (cx - door_width / 2, y0 + WALL_T / 2), 0.0
            cut = (cx - gap / 2, y0 - 0.25, [(0, 0), (gap, 0), (gap, WALL_T + 0.5), (0, WALL_T + 0.5)])
        elif door == "N":
            hinge, rot = (cx + door_width / 2, y1 - WALL_T / 2), 180.0
            cut = (cx - gap / 2, y1 - WALL_T - 0.25, [(0, 0), (gap, 0), (gap, WALL_T + 0.5), (0, WALL_T + 0.5)])
        elif door == "E":
            hinge, rot = (x1 - WALL_T / 2, cy - door_width / 2), 90.0
            cut = (x1 - WALL_T - 0.25, cy - gap / 2, [(0, 0), (WALL_T + 0.5, 0), (WALL_T + 0.5, gap), (0, gap)])
        else:
            hinge, rot = (x0 + WALL_T / 2, cy + door_width / 2), -90.0
            cut = (x0 - 0.25, cy - gap / 2, [(0, 0), (WALL_T + 0.5, 0), (WALL_T + 0.5, gap), (0, gap)])
        door_name = name + "_Door"
        sc.entities.append((door_name, "FarmGate", hinge[0], hinge[1], top, rot))
        sc.props.append((door_name, "CFarmGateComponent", {"Width": door_width, "Height": 2.15, "OpenAngle": 95.0,
                                                           "SwingSeconds": 0.8, "LinkDepth": 1.4, "Cost": 1.0,
                                                           "Material": M + "timber"}))
        sc.cuts.append((name + "_DoorCut", cut[0], cut[1], z0 - 3.0, cut[2], 8.0))
    return top


def furnish(sc, name, kind, cx, cy, w, d, top):
    F = "Objects/props/furniture/"
    if kind in ("cottage", "reeve", "cabin"):
        sc.model(name + "_Table", F + "log_furniture/table_a.cgf", cx - w * 0.15, cy + d * 0.12, top, 0.0)
        sc.model(name + "_Bench", F + "log_furniture/bench_a.cgf", cx - w * 0.15, cy + d * 0.12 - 0.9, top, 0.0)
        sc.box(name + "_Bed", cx + w / 2 - 1.0, cy + d / 2 - 1.5, top, 0.95, 2.0, 0.45, M + "cloth_pale")
        sc.box(name + "_BedFrame", cx + w / 2 - 1.0, cy + d / 2 - 1.5, top, 1.05, 2.1, 0.25, M + "timber")
        sc.model(name + "_Barrel", "Objects/props/maritime/wooden_barrel/wooden_barrel_a.cgf", cx - w / 2 + 0.7, cy + d / 2 - 0.7, top)
        sc.box(name + "_Hearth", cx - w / 2 + 0.9, cy - d / 2 + 0.9, top, 1.1, 1.1, 0.5, M + "stone_wall")
    if kind == "reeve":
        sc.model(name + "_Shelf", F + "wooden/wood_shelf_a.cgf", cx + w * 0.1, cy + d / 2 - 0.45, top, 180.0)
        sc.model(name + "_Bench2", F + "log_furniture/bench_a.cgf", cx - w * 0.15, cy + d * 0.12 + 0.9, top, 180.0)
    if kind == "tavern":
        # Two tables, not three: the one by the door (cx - 0.3w) put a bench in line with the doorway, 0.8 m from where
        # the door crossing ends, and villagers crossing jammed on it. Names kept (Table1, Table2).
        for i, tx in [(1, cx), (2, cx + w * 0.3)]:
            sc.model("%s_Table%d" % (name, i), F + "log_furniture/table_a.cgf", tx, cy - d * 0.12, top)
            sc.model("%s_BenchA%d" % (name, i), F + "log_furniture/bench_a.cgf", tx, cy - d * 0.12 - 0.9, top)
            sc.model("%s_BenchB%d" % (name, i), F + "log_furniture/bench_a.cgf", tx, cy - d * 0.12 + 0.9, top, 180.0)
        sc.box(name + "_Counter", cx + w * 0.1, cy + d / 2 - 1.1, top, w * 0.5, 0.6, 1.05, M + "timber")
        for i in range(3):
            sc.model("%s_Keg%d" % (name, i), "Objects/props/maritime/wooden_barrel/wooden_barrel_b.cgf", cx - w / 2 + 0.7 + i * 0.9, cy + d / 2 - 0.6, top)
    if kind == "chapel":
        for row in range(4):
            for side in (-1, 1):
                sc.model("%s_Pew%d_%d" % (name, row, side + 1), F + "log_furniture/bench_b.cgf", cx + side * 1.6, cy - d * 0.25 + row * 1.5, top)
        sc.box(name + "_Altar", cx, cy + d / 2 - 1.2, top, 1.8, 0.8, 1.0, M + "stone_wall")
        sc.box(name + "_AltarCloth", cx, cy + d / 2 - 1.2, top + 1.0, 1.9, 0.9, 0.04, M + "cloth")


BUILDINGS = [
    # name, cx, cy, w, d, door, kind
    ("Reeve", 478.0, 372.0, 10.0, 7.0, "E", "reeve"),
    ("Tavern", 524.0, 374.0, 11.0, 7.0, "W", "tavern"),
    ("Chapel", 500.0, 398.0, 8.0, 12.0, "S", "chapel"),
    ("CottageA", 476.0, 346.0, 7.0, 6.0, "E", "cottage"),
    ("CottageB", 486.0, 330.0, 7.0, 6.0, "N", "cottage"),
    ("CottageC", 532.0, 396.0, 7.0, 6.0, "W", "cottage"),
    ("TomCabin", 493.0, 600.0, 6.0, 5.0, "E", "cabin"),
]


def build_buildings(sc):
    for name, cx, cy, w, d, door, kind in BUILDINGS:
        if kind == "chapel":
            top = house(sc, name, cx, cy, w, d, door, kind, wall_h=4.2, wall_mat=M + "stone_wall", roof_mat=M + "slate", roof_h=3.2)
        elif kind == "tavern":
            top = house(sc, name, cx, cy, w, d, door, kind, wall_h=3.2)
        else:
            top = house(sc, name, cx, cy, w, d, door, kind)
        furnish(sc, name, kind, cx, cy, w, d, top)
    # the smithy: open to the west, a forge and an anvil
    top = house(sc, "Smithy", 522.0, 344.0, 7.0, 6.0, None, "smithy", wall_h=3.0, wall_mat=M + "stone_wall",
                roof_mat=M + "slate", open_side="W")
    sc.box("Smithy_Forge", 523.8, 344.0, top, 1.6, 1.6, 0.9, M + "stone_wall")
    sc.box("Smithy_Anvil", 521.0, 343.0, top, 0.7, 0.35, 0.8, M + "steel")
    sc.box("Smithy_AnvilBase", 521.0, 343.0, top, 0.5, 0.5, 0.5, M + "timber")
    # the barn: a wide opening to the south
    top = house(sc, "Barn", 457.0, 392.0, 10.0, 8.0, None, "barn", wall_h=3.6, wall_mat=M + "planks", open_side="S")
    for i in range(4):
        sc.model("Barn_Logs%d" % i, "Objects/props/misc/stacked_logs/stacked_logs_half_2m_a.cgf", 453.0 + i * 2.2, 395.0, top)


# ----------------------------------------------------------------------------- village dressing
ROADS = [
    [(512, 150), (510, 220), (506, 290), (503, 330), (500, 350)],
    [(500, 372), (505, 400), (510, 440), (512, 456)],
    [(512, 486), (508, 520), (502, 570), (500, 585)],
    [(503, 605), (512, 660), (520, 710), (516, 748)],
]


def build_village(sc):
    # the well on the green
    g = ground(500, 356)
    sc.brushes.append(("Well_Ring", CYLINDER, 500.0, 356.0, g + 0.5, 2.2, 2.2, 1.0, 0.0, 0.0, 0.0, M + "stone_wall"))
    sc.box("Well_PostA", 499.0, 356.0, g, 0.18, 0.18, 2.4, M + "timber")
    sc.box("Well_PostB", 501.0, 356.0, g, 0.18, 0.18, 2.4, M + "timber")
    sc.brushes.append(("Well_Roof", PYRAMID, 500.0, 356.0, g + 2.9, 2.8, 1.8, 1.0, 0.0, 0.0, 0.0, "materials/thatch"))
    # a cart and a scatter of barrels and baskets on the green
    sc.box("Cart_Bed", 492.0, 366.0, ground(492, 366) + 0.55, 3.0, 1.5, 0.35, M + "planks")
    for i, (wx, wy) in enumerate([(491.0, 365.2), (491.0, 366.8)]):
        sc.brushes.append(("Cart_Wheel%d" % i, CYLINDER, wx, wy, ground(wx, wy) + 0.55, 1.1, 1.1, 0.15, 90.0, 0.0, 0.0, M + "timber"))
    B = "Objects/props/maritime/wooden_barrel/"
    W = "Objects/props/industrial/baskets/wicker/"
    for i, (m, x, y) in enumerate([(B + "wooden_barrel_a.cgf", 495.5, 368.0), (B + "wooden_barrel_b.cgf", 496.4, 368.5),
                                   (W + "basket_wicker_a.cgf", 494.0, 369.0), (W + "basket_wicker_b.cgf", 518.0, 368.0),
                                   (B + "wooden_bucket_a.cgf", 501.6, 355.0), (B + "wooden_barrel_a.cgf", 519.0, 381.0)]):
        sc.model("Green_Prop%d" % i, m, x, y, ground(x, y))
    # woodpile behind the tavern
    sc.model("Tavern_Woodpile", "Objects/props/misc/stacked_logs/stacked_logs_full_2m_a.cgf", 534.5, 364.0, ground(534.5, 364.0), 90.0)
    # fields to the south-west, fenced with GameSDK's wooden fence (4 m panels)
    F = "Objects/architecture/fences/wooden_fence/wooden_fence_120_400_a.cgf"
    fx0, fx1, fy0, fy1 = 452.0, 476.0, 304.0, 324.0
    k = 0
    for x in np.arange(fx0 + 2, fx1, 4.0):
        for y in (fy0, fy1):
            sc.model("Field_Fence%d" % k, F, float(x), y, ground(float(x), y), 0.0); k += 1
    for y in np.arange(fy0 + 2, fy1, 4.0):
        for x in (fx0, fx1):
            sc.model("Field_Fence%d" % k, F, x, float(y), ground(x, float(y)), 90.0); k += 1
    # dirt roads: thin strips pitched to the ground
    k = 0
    for road in ROADS:
        pts = []
        for (ax, ay), (bx, by) in zip(road, road[1:]):
            n = max(1, int(math.hypot(bx - ax, by - ay) / 5.0))
            for i in range(n):
                pts.append((ax + (bx - ax) * i / n, ay + (by - ay) * i / n))
        pts.append(road[-1])
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            za, zb = ground(ax, ay), ground(bx, by)
            L = math.hypot(bx - ax, by - ay)
            yaw = math.degrees(math.atan2(by - ay, bx - ax))
            pitch = math.degrees(math.atan2(zb - za, L))
            sc.brushes.append(("Road%03d" % k, CUBE, (ax + bx) / 2, (ay + by) / 2, (za + zb) / 2 + 0.0, L + 0.8, 3.0, 0.1,
                               0.0, -pitch, yaw, M + "dirt"))
            k += 1
    # the bridge over the river on the forest road
    ya, yb = 455.0, 487.0
    za, zb = ground(512, ya), ground(512, yb)
    L = yb - ya
    pitch = math.degrees(math.atan2(zb - za, L))
    deck_z = (za + zb) / 2 + 0.2
    sc.brushes.append(("Bridge_Deck", CUBE, 512.0, (ya + yb) / 2, deck_z, L, 4.0, 0.3, 0.0, -pitch, 90.0, M + "planks"))
    for side in (-1, 1):
        sc.brushes.append(("Bridge_Rail%d" % (side + 1), CUBE, 512.0 + side * 1.9, (ya + yb) / 2, deck_z + 0.75, L - 2, 0.15, 0.12,
                           0.0, -pitch, 90.0, M + "timber"))
        for i, y in enumerate(np.arange(ya + 2, yb - 1, 4.0)):
            t = (y - ya) / L
            z = za + (zb - za) * t
            sc.box("Bridge_Post%d_%d" % (side + 1, i), 512.0 + side * 1.9, float(y), z - 0.1, 0.18, 0.18, 1.0, M + "timber")
    for i, y in enumerate([464.0, 471.0, 478.0]):
        for side in (-1, 1):
            sc.box("Bridge_Pile%d_%d" % (i, side + 1), 512.0 + side * 1.4, y, 10.0, 0.35, 0.35, deck_z - 10.0, M + "timber")
    # the roadside shrine at the start
    g = ground(512, 170)
    sc.box("Shrine_Base", 514.5, 170.0, g, 1.2, 1.2, 0.4, M + "stone_wall")
    sc.box("Shrine_Shaft", 514.5, 170.0, g + 0.4, 0.35, 0.35, 2.6, M + "stone_wall")
    sc.box("Shrine_Cross", 514.5, 170.0, g + 2.3, 1.2, 0.3, 0.3, M + "stone_wall")
    # the clearing: logs and a stump field around Tom's cabin
    for i, (x, y, r) in enumerate([(503.0, 604.0, 0.0), (506.0, 596.0, 90.0), (488.0, 590.0, 30.0)]):
        sc.model("Clearing_Logs%d" % i, "Objects/props/misc/stacked_logs/stacked_logs_full_4m_a.cgf", x, y, ground(x, y), r)


# ----------------------------------------------------------------------------- the old tower
def build_camp(sc):
    rnd = random.Random(7)
    cx, cy = 515.0, 772.0
    x0, x1, y0, y1 = 504.0, 526.0, 761.0, 783.0
    g = ground(cx, cy)
    k = 0

    def ruin_run(ax, ay, bx, by, gaps):
        nonlocal k
        L = math.hypot(bx - ax, by - ay)
        n = int(L / 2.0)
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            mid = (t0 + t1) / 2
            if any(g0 <= mid <= g1 for g0, g1 in gaps):
                continue
            px, py_ = ax + (bx - ax) * mid, ay + (by - ay) * mid
            h = rnd.uniform(2.4, 4.6)
            yaw = math.degrees(math.atan2(by - ay, bx - ax))
            sc.box("Tower_Wall%03d" % k, px, py_, ground(px, py_) - 0.5, L / n + 0.05, 1.1, h + 0.5, M + "stone_wall", yaw)
            k += 1

    ruin_run(x0, y0, x1, y0, [(0.42, 0.58)])        # south, with the gate
    ruin_run(x1, y0, x1, y1, [(0.62, 0.72)])        # east, with a breach
    ruin_run(x1, y1, x0, y1, [])                    # north
    ruin_run(x0, y1, x0, y0, [])                    # west
    # the old keep in the north-west corner (solid), and fallen blocks
    sc.box("Tower_Keep", x0 + 3.0, y1 - 3.0, g - 1.0, 6.0, 6.0, 10.0, M + "stone_wall")
    sc.box("Tower_KeepTop", x0 + 3.0, y1 - 3.0, g + 9.0, 6.6, 6.6, 0.6, M + "stone_wall")
    for i in range(6):
        x, y = rnd.uniform(x0 + 2, x1 - 2), rnd.uniform(y0 + 2, y0 + 6)
        sc.box("Tower_Rubble%d" % i, x, y, ground(x, y) - 0.2, rnd.uniform(0.6, 1.2), rnd.uniform(0.5, 1.0), rnd.uniform(0.4, 0.8),
               M + "stone_wall", rnd.uniform(0, 90))
    # the camp: two tents (pyramids of cloth), bedrolls, crates
    for i, (x, y) in enumerate([(521.0, 777.0), (509.0, 769.0)]):
        sc.brushes.append(("Camp_Tent%d" % i, PYRAMID, x, y, ground(x, y) + 1.2, 3.2, 3.6, 2.4, 0.0, 0.0, rnd.uniform(0, 30), M + "cloth"))
    for i, (x, y) in enumerate([(517.0, 775.0), (512.0, 776.0), (520.0, 770.0)]):
        sc.box("Camp_Bedroll%d" % i, x, y, ground(x, y), 0.8, 1.9, 0.12, M + "cloth_pale", rnd.uniform(0, 180))
    B = "Objects/props/maritime/wooden_barrel/"
    for i, (x, y) in enumerate([(523.0, 781.0), (524.0, 780.2), (507.0, 764.0)]):
        sc.model("Camp_Barrel%d" % i, B + "wooden_barrel_a.cgf", x, y, ground(x, y))


# ----------------------------------------------------------------------------- people and things
# Black Wat's two archers: inside the ruined wall, behind the gate, where the walls give the player cover from them
# and the gap and the breach are the lines they shoot through. BowRange makes a bandit an archer (CBanditBrain).
ARCHERS = [("BanditArcher1", 513.8, 768.0, 180.0), ("BanditArcher2", 519.5, 765.5, 180.0)]
ARCHER_PROPS = {"BowRange": 24.0, "MaxHealth": 50.0, "AttackDamage": 8.0, "WeaponLength": 0.0, "PatrolRadius": 1.5, "CombatSight": 24.0,
                "Gait": "Stealth"}   # the archers creep along the wall (a MotionBricks walk_stealth clip at its own speed); alerted, they run like the others
BANDIT = {"Role": "Bandit", "CharacterMaterial": "materials/ashvale/people/bandit.mtl",
          "MaxHealth": 70.0, "AttackDamage": 12.0, "CombatSight": 18.0, "WeaponLength": 0.9, "PatrolRadius": 2.5,
          "PatrolPoints": 3, "PauseSeconds": 5.0, "RunSpeed": 3.5}


def _add_npc(sc, name, x, y, rot, props):
    sc.entities.append((name, "Villager", x, y, ground(x, y), rot))
    base = {"PatrolPath": "", "PatrolRadius": 2.0, "PatrolPoints": 3, "PauseSeconds": 4.0, "BehaviorTree": "",
            "Autonomous": False, "SightRange": 8.0}
    base.update(props)
    sc.props.append((name, "CNpcComponent", base))


def build_archers(sc):
    """Only the archers: for a level that already has its people (build_ashvale.py archers)."""
    for name, x, y, rot in ARCHERS:
        _add_npc(sc, name, x, y, rot, dict(BANDIT, **ARCHER_PROPS))


def build_people(sc):
    E = sc.entities
    P = sc.props

    def npc(name, x, y, rot, props):
        E.append((name, "Villager", x, y, ground(x, y), rot))
        base = {"PatrolPath": "", "PatrolRadius": 2.0, "PatrolPoints": 3, "PauseSeconds": 4.0, "BehaviorTree": "",
                "Autonomous": False, "SightRange": 8.0}
        base.update(props)
        P.append((name, "CNpcComponent", base))

    # Ashford's people
    npc("Hilda", 485.5, 375.0, 90.0, {"TalkName": "Hilda, the reeve", "DialogueId": "hilda", "PatrolRadius": 1.5,
                                        "CharacterMaterial": "materials/ashvale/people/reeve.mtl"})
    npc("Oswin", 525.0, 375.5, 90.0, {"TalkName": "Oswin, the tavernkeeper", "DialogueId": "oswin", "PatrolRadius": 1.2, "Gait": "Slow",
                                        "CharacterMaterial": "materials/ashvale/people/tavern.mtl"})
    npc("Edmund", 500.0, 399.0, 180.0, {"TalkName": "Father Edmund", "DialogueId": "edmund", "PatrolRadius": 1.5, "Gait": "Slow",
                                          "CharacterMaterial": "materials/ashvale/people/priest.mtl"})
    npc("Garrick", 518.0, 343.0, -90.0, {"TalkName": "Garrick, the smith", "DialogueId": "garrick", "PatrolRadius": 1.5,
                                           "CharacterMaterial": "materials/ashvale/people/smith.mtl"})
    npc("Martha", 506.0, 349.0, 0.0, {"TalkName": "Martha", "DialogueId": "woman", "Autonomous": True, "PatrolRadius": 8.0,
                                        "PatrolPoints": 5, "VisitInterval": 100000.0,
                                        "CharacterMaterial": "materials/ashvale/people/peasant_green.mtl"})
    npc("Will", 494.0, 372.0, 0.0, {"TalkName": "Will", "DialogueId": "villager", "Autonomous": True, "PatrolRadius": 10.0,
                                      "PatrolPoints": 5, "VisitInterval": 100000.0,
                                      "CharacterMaterial": "materials/ashvale/people/peasant_blue.mtl"})
    npc("Tom", 500.0, 594.0, 180.0, {"TalkName": "Tom, the woodcutter", "DialogueId": "tom", "PatrolRadius": 2.5,
                                       "CharacterMaterial": "materials/ashvale/people/woodcutter.mtl"})

    # Black Wat's men
    bandit = BANDIT
    for name, x, y, rot in [("BanditGateW", 512.5, 757.5, 180.0), ("BanditGateE", 518.0, 757.0, 180.0),
                            ("BanditFire", 511.0, 773.0, 90.0), ("BanditYard", 521.0, 767.0, -90.0)]:
        npc(name, x, y, rot, dict(bandit))
    wat = dict(bandit)
    wat.update({"MaxHealth": 160.0, "AttackDamage": 20.0, "DeathFlag": "wat_dead", "WeaponLength": 1.15, "PatrolRadius": 1.5,
                "CharacterMaterial": "materials/ashvale/people/bandit_chief.mtl"})
    npc("BlackWat", 516.0, 779.0, 180.0, wat)
    for name, x, y, rot in ARCHERS:
        npc(name, x, y, rot, dict(bandit, **ARCHER_PROPS))

    # chests
    def chest(name, x, y, rot, contents, label="chest", requires="", locked="It is locked."):
        E.append((name, "Chest", x, y, ground(x, y), rot))
        P.append((name, "CChestComponent", {"Label": label, "Contents": contents, "RequiresFlag": requires, "LockedText": locked}))

    chest("TitheChest", 518.0, 781.0, 180.0, "tithe,coins:25", "tithe chest", "wat_dead",
          "Locked. Black Wat has the key.")
    chest("ReeveChest", 474.5, 374.5, 90.0, "coins:6,bread:1", "chest")
    chest("TomChest", 491.0, 601.5, 0.0, "bread:2", "chest")
    chest("CampChest", 522.5, 776.0, 180.0, "coins:12,bread:1", "bandits' strongbox")

    # fires: the green, the smithy forge, the clearing, the bandits' camp (lit)
    def fire(name, x, y, on=False, label="campfire"):
        E.append((name, "Interactable", x, y, ground(x, y), 0.0))
        P.append((name, "CInteractableComponent", {"Mesh": "objects/props/fishing_camp/campfire.cgf", "Label": label,
                                                    "OnByDefault": on, "UseRange": 3.0}))

    fire("GreenFire", 507.0, 363.0)
    fire("ClearingFire", 504.0, 590.0)
    fire("CampFire", 515.0, 770.5, True)

    # places for the autonomous villagers
    E.append(("AshfordVillage", "VillagePoint", 500.0, 362.0, ground(500, 362), 0.0))
    P.append(("AshfordVillage", "CVillagePointComponent", {"Kind": "Village", "Radius": 45.0, "Label": "Ashford"}))
    E.append(("AshfordWoodpile", "VillagePoint", 531.5, 364.0, ground(531.5, 364.0), -90.0))
    P.append(("AshfordWoodpile", "CVillagePointComponent", {"Kind": "Woodpile", "Radius": 2.5, "Label": "Ashford woodpile"}))
    for i, (x, y) in enumerate([(470.0, 326.0), (541.0, 352.0), (514.0, 420.0)]):
        E.append(("Firewood%d" % i, "PickupItem", x, y, ground(x, y) + 0.3, 0.0))

    # markers
    E.append(("AshvaleStart", "AshvaleMarker", 512.0, 172.0, ground(512, 172) + 0.2, 0.0))
    P.append(("AshvaleStart", "CAshvaleMarkerComponent", {"Kind": "PlayerStart"}))
    E.append(("AshvaleRespawn", "AshvaleMarker", 500.0, 352.5, ground(500, 352.5) + 0.2, 180.0))
    P.append(("AshvaleRespawn", "CAshvaleMarkerComponent", {"Kind": "Respawn"}))
    E.append(("AshvaleMenuCamera", "AshvaleMarker", 566.0, 262.0, 72.0, 34.0))
    P.append(("AshvaleMenuCamera", "CAshvaleMarkerComponent", {"Kind": "MenuCamera"}))


def building_top(name):
    """The floor height inside a BUILDINGS house (the same arithmetic as house())."""
    for n, cx, cy, w, d, door, kind in BUILDINGS:
        if n == name:
            z0 = max(ground(cx - w / 2, cy - d / 2), ground(cx + w / 2, cy - d / 2), ground(cx - w / 2, cy + d / 2),
                     ground(cx + w / 2, cy + d / 2), ground(cx, cy))
            return z0 + FLOOR_H, (cx, cy, w, d)
    raise KeyError(name)


TABLE_TOP = 0.71          # GameSDK log_furniture/table_a.cgf, measured with cgf_bounds.py


def build_items(sc):
    """Bread and coins lying about the valley: real items (Pickup entities, CPickupComponent) the player takes with E and sees in
    the inventory (I). Indoors they sit on the tables; outdoors on the ground by the road, the green, the clearing, the camp."""
    E, P = sc.entities, sc.props

    def item(name, x, y, z, kind, amount, rot=0.0):
        E.append((name, "Pickup", x, y, z + 0.02, rot))
        P.append((name, "CPickupComponent", {"Kind": kind, "Amount": amount, "Label": ""}))

    def outdoors(name, x, y, kind, amount, rot=0.0):
        item(name, x, y, ground(x, y), kind, amount, rot)

    def on_table(name, house, dx, dy, kind, amount, rot=0.0):
        top, (cx, cy, w, d) = building_top(house)
        item(name, cx + dx * w, cy + dy * d, top + TABLE_TOP, kind, amount, rot)

    # in the houses, on the tables (furnish(): the table is at (cx - 0.15 w, cy + 0.12 d) in a cottage, the reeve's and the cabin)
    on_table("Bread_ReeveTable", "Reeve", -0.15, 0.12, "Bread", 1, 20.0)
    on_table("Coins_TavernTable", "Tavern", 0.30, -0.12, "Coins", 2, 40.0)
    on_table("Bread_TavernTable", "Tavern", 0.0, -0.12, "Bread", 2, 10.0)
    on_table("Bread_CottageA", "CottageA", -0.15, 0.12, "Bread", 1, 80.0)
    on_table("Coins_CottageB", "CottageB", -0.15, 0.12, "Coins", 3, 15.0)
    on_table("Bread_CottageC", "CottageC", -0.15, 0.12, "Bread", 1, -30.0)
    on_table("Bread_TomCabin", "TomCabin", -0.15, 0.12, "Bread", 1, 5.0)
    top, (cx, cy, w, d) = building_top("Chapel")
    item("Coins_ChapelAltar", cx + 0.5, cy + d / 2 - 1.2, top + 1.0 + 0.04, "Coins", 2, 25.0)
    top, (cx, cy, w, d) = building_top_of(457.0, 392.0, 10.0, 8.0)      # the barn is not in BUILDINGS
    item("Coins_Barn", cx - 1.5, cy - 1.0, top, "Coins", 5, 50.0)

    # outdoors
    outdoors("Coins_Start", 514.0, 186.0, "Coins", 2, 20.0)        # near where a new game starts: the first thing to find
    outdoors("Bread_Start", 509.5, 196.0, "Bread", 1, -15.0)
    outdoors("Coins_RoadSouth", 508.5, 290.0, "Coins", 3, 70.0)
    outdoors("Coins_Well", 497.0, 359.0, "Coins", 4, 0.0)
    outdoors("Bread_Green", 505.0, 366.0, "Bread", 1, 35.0)
    outdoors("Bread_Clearing", 505.5, 592.5, "Bread", 1, -40.0)
    outdoors("Bread_Camp", 519.0, 773.5, "Bread", 2, 10.0)
    outdoors("Coins_Camp", 511.0, 772.0, "Coins", 6, 60.0)


# ----------------------------------------------------------------------------- house trim
TRIM_KINDS_WITH_CHIMNEY = ("cottage", "reeve", "cabin", "tavern")


def _side_frame(cx, cy, w, d, side):
    """(outer-face centre x, y, outward normal nx, ny, wall length) of a house wall on `side`."""
    if side == "S":
        return cx, cy - d / 2, 0.0, -1.0, w
    if side == "N":
        return cx, cy + d / 2, 0.0, 1.0, w
    if side == "W":
        return cx - w / 2, cy, -1.0, 0.0, d
    return cx + w / 2, cy, 1.0, 0.0, d


def _trim_box(sc, name, side, face, t, thick, out, z0, length, height, material):
    """A box on a wall: `t` along the wall from its middle, `out` metres out from the outer face (its centre), `thick` deep."""
    fx, fy, nx, ny, _ = face
    tx, ty = -ny, nx                                  # the direction along the wall
    x, y = fx + nx * out + tx * t, fy + ny * out + ty * t
    if side in ("N", "S"):
        sc.box(name, x, y, z0, length, thick, height, material)
    else:
        sc.box(name, x, y, z0, thick, length, height, material)


def trim_house(sc, name, cx, cy, w, d, door, kind, top, wall_h, open_side=None):
    """Half-timbering, windows with shutters, a door frame and a chimney on a house that house() already built.
    Purely visual and OUTSIDE the walls: nothing here changes the navmesh, the door cuts or the interior's walkable floor.
    Every object is named Trim_<house>_..., so `clear Trim_` removes it all."""
    T = M + "timber"
    P = M + "slate"                                   # the panes: dark
    S = M + "planks"                                  # shutters
    framed = kind in TRIM_KINDS_WITH_CHIMNEY          # half-timbering belongs on the plastered houses, not on stone or planks
    windowed = kind in TRIM_KINDS_WITH_CHIMNEY or kind in ("smithy",)
    for side in ("S", "N", "W", "E"):
        if side == open_side:
            continue
        face = _side_frame(cx, cy, w, d, side)
        length = face[4]
        gap = 1.35 if side == door else 0.0
        tag = "Trim_%s_%s" % (name, side)

        def spans(z_lo_door_free):
            """The stretches of the wall a horizontal beam may run along: all of it, or both sides of the doorway."""
            lo, hi = -length / 2 + 0.45, length / 2 - 0.45
            if gap > 0.0 and z_lo_door_free:
                return [(lo, -gap / 2 - 0.15), (gap / 2 + 0.15, hi)]
            return [(lo, hi)]

        # sill plate, mid rail, head plate
        for i, (zr, door_free) in enumerate(((0.06, True), (wall_h * 0.5, True), (wall_h - 0.12, False))):
            for j, (a, b) in enumerate(spans(door_free) if framed else []):
                if b - a > 0.2:
                    _trim_box(sc, "%s_Rail%d_%d" % (tag, i, j), side, face, (a + b) / 2, 0.08, 0.02, top + zr, b - a, 0.12, T)
        # studs every ~1.5 m, skipping the doorway and the windows' places
        n = max(2, int(length // 1.5))
        window_t = [] if side == door else ([0.0] if length < 6.5 else [-length * 0.24, length * 0.24])
        for k in range(1, n if framed else 1):
            t = -length / 2 + length * k / n
            if gap > 0.0 and abs(t) < gap / 2 + 0.3:
                continue
            if any(abs(t - wt) < 0.75 for wt in window_t):
                continue
            _trim_box(sc, "%s_Stud%d" % (tag, k), side, face, t, 0.08, 0.02, top + 0.06, 0.12, wall_h - 0.18, T)
        # windows: a dark pane through the wall (seen from both sides), a timber frame, a sill and two shutters
        if windowed:
            for k, wt in enumerate(window_t):
                z0, ww, wh = top + 1.05, 0.78, 0.92
                _trim_box(sc, "%s_Pane%d" % (tag, k), side, face, wt, 0.3 + 0.05, -0.15, z0, ww, wh, P)
                _trim_box(sc, "%s_FrameL%d" % (tag, k), side, face, wt - ww / 2 - 0.04, 0.09, 0.03, z0 - 0.06, 0.08, wh + 0.12, T)
                _trim_box(sc, "%s_FrameR%d" % (tag, k), side, face, wt + ww / 2 + 0.04, 0.09, 0.03, z0 - 0.06, 0.08, wh + 0.12, T)
                _trim_box(sc, "%s_FrameT%d" % (tag, k), side, face, wt, 0.09, 0.03, z0 + wh, ww + 0.16, 0.08, T)
                _trim_box(sc, "%s_Sill%d" % (tag, k), side, face, wt, 0.16, 0.06, z0 - 0.12, ww + 0.24, 0.07, T)
                _trim_box(sc, "%s_Bar%d" % (tag, k), side, face, wt, 0.05, 0.0, z0 + wh * 0.5 - 0.02, ww, 0.04, T)
                for sgn in (-1, 1):
                    _trim_box(sc, "%s_Shutter%d_%d" % (tag, k, sgn), side, face, wt + sgn * (ww / 2 + 0.28), 0.05, 0.03, z0 - 0.04, 0.34, wh + 0.08, S)
        # the door's frame: two posts just outside the opening and a head beam over it
        if side == door:
            for sgn in (-1, 1):
                _trim_box(sc, "%s_Jamb%d" % (tag, sgn), side, face, sgn * (gap / 2 + 0.12), 0.1, 0.03, top, 0.14, 2.3, T)
            _trim_box(sc, "%s_DoorHead" % tag, side, face, 0.0, 0.1, 0.03, top + 2.3, gap + 0.4, 0.16, T)
    # the chimney, over the hearth: a stone stack from the hearth through the ceiling and the roof
    if kind in TRIM_KINDS_WITH_CHIMNEY:
        hx, hy = cx - w / 2 + 0.9, cy - d / 2 + 0.9
        sc.box("Trim_%s_Chimney" % name, hx, hy, top + 0.5, 0.75, 0.75, wall_h + 2.3 - 0.5, M + "stone_wall")
        sc.box("Trim_%s_ChimneyCap" % name, hx, hy, top + wall_h + 2.3, 0.95, 0.95, 0.16, M + "stone_wall")


def build_trim(sc):
    for name, cx, cy, w, d, door, kind in BUILDINGS:
        top, _ = building_top(name)
        wall_h = 4.2 if kind == "chapel" else (3.2 if kind == "tavern" else 2.9)
        trim_house(sc, name, cx, cy, w, d, door, kind, top, wall_h)
    # the smithy (open to the west) and the barn (open to the south)
    top, _ = building_top_of(522.0, 344.0, 7.0, 6.0)
    trim_house(sc, "Smithy", 522.0, 344.0, 7.0, 6.0, None, "smithy", top, 3.0, open_side="W")
    sc.box("Trim_Smithy_Chimney", 523.8, 344.0, top + 0.9, 0.9, 0.9, 3.0 + 2.6 - 0.9, M + "stone_wall")
    top, _ = building_top_of(457.0, 392.0, 10.0, 8.0)
    trim_house(sc, "Barn", 457.0, 392.0, 10.0, 8.0, None, "barn", top, 3.6, open_side="S")


def building_top_of(cx, cy, w, d):
    z0 = max(ground(cx - w / 2, cy - d / 2), ground(cx + w / 2, cy - d / 2), ground(cx - w / 2, cy + d / 2),
             ground(cx + w / 2, cy + d / 2), ground(cx, cy))
    return z0 + FLOOR_H, (cx, cy, w, d)


def build_nav():
    print(py("""
import sandbox.general as general
n = general.new_object('NavigationArea', '', 'AshvaleNav', 405.0, 135.0, 8.0)
general.set_entity_param(n, 'Height', 95.0)
for i, (px, py_) in enumerate([(0.0, 0.0), (215.0, 0.0), (215.0, 670.0), (0.0, 670.0)]):
    general.nav_insert_point(n, i, px, py_, 0.0)
print('nav area', n)
"""))


# ----------------------------------------------------------------------------- vegetation
def build_vegetation():
    rnd = random.Random(1348)
    H = heights()
    gy, gx = np.gradient(H)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    def near_road(x, y, margin):
        for road in ROADS:
            for (ax, ay), (bx, by) in zip(road, road[1:]):
                abx, aby = bx - ax, by - ay
                t = max(0.0, min(1.0, ((x - ax) * abx + (y - ay) * aby) / (abx * abx + aby * aby)))
                if math.hypot(x - ax - abx * t, y - ay - aby * t) < margin:
                    return True
        return False

    keep_clear = [(500, 362, 62), (500, 594, 22), (515, 772, 20), (512, 170, 12), (464, 314, 16)]

    def free(x, y, margin_road=5.0):
        if not (6 < x < 1017 and 6 < y < 1017):
            return False
        if H[int(x), int(y)] < 17.5:
            return False
        if any(math.hypot(x - cx, y - cy) < r for cx, cy, r in keep_clear):
            return False
        return not near_road(x, y, margin_road)

    species = {
        "pine": ("Objects/natural/trees/pine/pine.cgf", 1.0),
        "pine_large": ("Objects/natural/trees/pine/pine_large.cgf", 1.0),
        "pine_small": ("Objects/natural/trees/pine/pine_small.cgf", 1.0),
        "oak_a": ("Objects/natural/trees/meadow/a_oak_tree_a.cgf", 1.0),
        "oak_b": ("Objects/natural/trees/meadow/b_oak_tree_a.cgf", 1.0),
        "oak_c": ("Objects/natural/trees/meadow/c_oak_tree_a.cgf", 1.0),
        "bush_a": ("Objects/natural/trees/meadow/e_oak_bush_a.cgf", 1.0),
        "bush_b": ("Objects/natural/trees/meadow/e_oak_bush_b.cgf", 1.0),
        "rock_a": ("Objects/natural/rocks/forest_rocks/deco_rock_forest_a.cgf", 1.0),
        "rock_b": ("Objects/natural/rocks/forest_rocks/deco_rock_forest_b.cgf", 1.0),
    }
    index = {}
    existing = json.loads(send("bridge.vegetation.list")["output"])
    known = {o.get("file", "").lower(): o["index"] for o in (existing if isinstance(existing, list) else existing.get("objects", []))}
    for key, (cgf, size) in species.items():
        if cgf.lower() in known:
            index[key] = known[cgf.lower()]
            continue
        r = send("bridge.vegetation.add %s 1 %f 0.2 0 1024 0 90" % (cgf, size))
        out = json.loads(r["output"]) if r.get("ok") else {}
        index[key] = out.get("index")
        print("added", key, r.get("ok"), str(r)[:120])

    placements = []
    # forest: north of the river, thickening uphill
    for _ in range(9000):
        x, y = rnd.uniform(400, 625), rnd.uniform(495, 800)
        if not free(x, y, 6.0) or slope[int(x), int(y)] > 38:
            continue
        density = 0.35 + 0.65 * min(1.0, (y - 495) / 120.0)
        if rnd.random() > density:
            continue
        kind = rnd.choice(["pine", "pine", "pine_large", "pine_small"])
        placements.append((kind, x, y))
    # thin the forest: keep ~1 per 45 m2
    thinned, taken = [], []
    for kind, x, y in placements:
        if all((x - a) ** 2 + (y - b) ** 2 > 42.0 for a, b in taken):
            thinned.append((kind, x, y))
            taken.append((x, y))
    placements = thinned
    # meadows: oaks and bushes south of the river
    for _ in range(1400):
        x, y = rnd.uniform(410, 620), rnd.uniform(150, 455)
        if not free(x, y, 5.0) or slope[int(x), int(y)] > 30:
            continue
        r = rnd.random()
        if r < 0.10:
            placements.append((rnd.choice(["oak_a", "oak_b", "oak_c"]), x, y))
        elif r < 0.35:
            placements.append((rnd.choice(["bush_a", "bush_b"]), x, y))
    # rocks in the forest
    for _ in range(220):
        x, y = rnd.uniform(410, 620), rnd.uniform(500, 790)
        if free(x, y, 4.0):
            placements.append((rnd.choice(["rock_a", "rock_b"]), x, y))

    counts = {}
    for kind, x, y in placements:
        idx = index.get(kind)
        if idx is None:
            continue
        send("bridge.vegetation.place %d %f %f %f" % (idx, x, y, ground(x, y)))
        counts[kind] = counts.get(kind, 0) + 1
    print("placed", counts)


# ----------------------------------------------------------------------------- main
def clear(prefix):
    import json
    objs = json.loads(send("bridge.object.list")["output"])
    names = [o["name"] for o in objs if o["name"].startswith(prefix)]
    if names:
        print(py("import sandbox.object as object\nfor n in %r:\n    object.delete(n)\nprint('deleted', %d)" % (names, len(names))))
    else:
        print("nothing to delete")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    sc = Scene()
    if phase == "buildings":
        build_buildings(sc)
        sc.flush()
    elif phase == "village":
        build_village(sc)
        sc.flush()
    elif phase == "camp":
        build_camp(sc)
        sc.flush()
    elif phase == "people":
        build_people(sc)
        sc.flush()
    elif phase == "archers":
        build_archers(sc)
        sc.flush()
    elif phase == "items":
        build_items(sc)
        sc.flush()
    elif phase == "trim":
        build_trim(sc)
        sc.flush()
    elif phase == "nav":
        build_nav()
    elif phase == "vegetation":
        build_vegetation()
    elif phase == "clear":
        prefix = sys.argv[2] if len(sys.argv) > 2 else ""
        if len(prefix) < 3:
            sys.exit("clear needs a prefix of at least 3 characters")
        clear(prefix)
    else:
        print(__doc__)
