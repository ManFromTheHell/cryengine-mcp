#!/usr/bin/env python3
"""Geometry helpers for undercroft_kit.py: build_mesh parts (boxes, bars, cylinders, frustums, quads, polygons), rooms whose outline is any polygon (walls as inward-facing
quads, doorways with reveals), and the PROXIES (axis-aligned boxes) the offline check walks and seals.

Conventions: metres, x east, y north, z up; a ring is a list of (x, y) listed CLOCKWISE seen from above (the interior is on the right of every edge); every quad is
wound counter-clockwise seen from the side that is meant to be seen.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import math
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)

NONSOLID = {"deco", "banner", "puddle", "chain", "cobweb"}          # drawn, but nobody bumps into them (the check ignores them)


class Parts(list):
    def box(self, x0, y0, z0, x1, y1, z1, mat, faces=None, tag=""):
        part = {"box": [round(min(x0, x1), 4), round(min(y0, y1), 4), round(min(z0, z1), 4), round(max(x0, x1), 4), round(max(y0, y1), 4), round(max(z0, z1), 4)], "material": mat, "_tag": tag}
        if faces:
            part["faces"] = faces
        self.append(part)

    def cyl(self, cx, cy, z0, radius, height, mat, sides=12, tag=""):
        self.append({"cylinder": {"center": [round(cx, 4), round(cy, 4), round(z0, 4)], "radius": radius, "height": height, "sides": sides}, "material": mat, "_tag": tag})

    def frustum(self, cx, cy, z0, r0, r1, height, mat, sides=10, tag=""):
        """Radius r0 at the bottom (z0), r1 at the top."""
        self.append({"frustum": {"center": [round(cx, 4), round(cy, 4), round(z0, 4)], "r0": r0, "r1": r1, "height": height, "sides": sides}, "material": mat, "_tag": tag})

    def bar(self, a, b, hw, ht, mat, normal=(0.0, 0.0, 1.0), tag=""):
        """A rectangular bar from a to b: hw across (in the plane perpendicular to the bar and `normal`), ht along `normal`."""
        self.append({"bar": {"a": [round(c, 4) for c in a], "b": [round(c, 4) for c in b], "half_width": hw, "half_thickness": ht, "plane_normal": list(normal)}, "material": mat, "_tag": tag})

    def quad(self, p0, p1, p2, p3, mat, uv=None, tag=""):
        part = {"quad": [[round(c, 4) for c in p] for p in (p0, p1, p2, p3)], "material": mat, "_tag": tag}
        if uv:
            part["uv"] = [[round(u, 4), round(v, 4)] for u, v in uv]
        self.append(part)

    def poly(self, points, normal, mat, tag=""):
        self.append({"poly": {"points": [[round(c, 4) for c in p] for p in points], "normal": list(normal)}, "material": mat, "_tag": tag})


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def regular_ring(cx, cy, apothem, sides, flat_at_deg):
    """A regular polygon with `sides` sides, clockwise, one flat centred on the direction flat_at_deg (0 = east, -90 = south). Edge 0 IS that flat (ring[0] is its counter-clockwise end,
    ring[1] its clockwise end)."""
    R = apothem / math.cos(math.pi / sides)
    step = 2 * math.pi / sides
    base = math.radians(flat_at_deg) + step / 2.0           # the counter-clockwise end of the flat
    return [(cx + R * math.cos(base - step * k), cy + R * math.sin(base - step * k)) for k in range(sides)]


def scaled(ring, center, f):
    return [(center[0] + (x - center[0]) * f, center[1] + (y - center[1]) * f) for x, y in ring]


def ring_perimeter_uv(ring):
    s, out = 0.0, []
    for i in range(len(ring)):
        out.append(s)
        s += math.hypot(ring[(i + 1) % len(ring)][0] - ring[i][0], ring[(i + 1) % len(ring)][1] - ring[i][1])
    return out


def ring_walls(P, ring, floor_fn, top_fn, mat_fn, doors=None, mouths=None, reveal=1.0, reveal_mat=None, tag="wall"):
    """Inward-facing walls on a clockwise ring. floor_fn(x, y) -> z of the floor; top_fn(i, x, y) -> z of the wall top at ring vertex i (interpolated along an edge);
    mat_fn(i) -> material of edge i; doors = {edge: [(centre along the edge in m, width, absolute z of the door's top)]} cut a doorway with reveals `reveal` m deep to the OUTSIDE;
    mouths = {edge: absolute z of the opening's top}: no wall on that edge at all (a corridor meets the room there), only the lintel above the opening."""
    doors, mouths = doors or {}, mouths or {}
    n = len(ring)
    ss = ring_perimeter_uv(ring)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        if L < 1e-6:
            continue
        d = ((q[0] - p[0]) / L, (q[1] - p[1]) / L)
        nout = (-d[1], d[0])
        mat = mat_fn(i)
        tp, tq = top_fn(i, p[0], p[1]), top_fn((i + 1) % n, q[0], q[1])

        def at(s):
            return (p[0] + d[0] * s, p[1] + d[1] * s)

        def top_at(s):
            return lerp(tp, tq, s / L)

        def wall(a, b, z_lo_fn=None, z_lo=None):
            (xa, ya), (xb, yb) = at(a), at(b)
            za = floor_fn(xa, ya) if z_lo is None else z_lo
            zb = floor_fn(xb, yb) if z_lo is None else z_lo
            P.quad((xa, ya, za), (xb, yb, zb), (xb, yb, top_at(b)), (xa, ya, top_at(a)), mat, [(ss[i] + a, za), (ss[i] + b, zb), (ss[i] + b, top_at(b)), (ss[i] + a, top_at(a))], tag=tag)

        if i in mouths:
            h = mouths[i]
            if min(tp, tq) > h + 1e-6:
                wall(0.0, L, z_lo=h)
            continue
        gaps = sorted(doors.get(i, []))
        cuts = [(c - w / 2.0, c + w / 2.0, h) for c, w, h in gaps]
        cursor = 0.0
        for a, b, h in cuts:
            if a > cursor + 1e-6:
                wall(cursor, a)
            if min(top_at(a), top_at(b)) > h + 1e-6:                        # the lintel over the doorway
                wall(a, b, z_lo=h)
            if reveal > 0:
                rm = reveal_mat or mat
                for edge_s, face in ((a, +1), (b, -1)):                     # the two jambs, facing into the opening
                    xin, yin = at(edge_s)
                    xo, yo = xin + nout[0] * reveal, yin + nout[1] * reveal
                    zf = floor_fn(xin, yin)
                    if face > 0:
                        pts = [(xin, yin, zf), (xo, yo, zf), (xo, yo, h), (xin, yin, h)]
                    else:
                        pts = [(xo, yo, zf), (xin, yin, zf), (xin, yin, h), (xo, yo, h)]
                    P.quad(*pts, rm, [(0, zf), (reveal, zf), (reveal, h), (0, h)], tag="reveal")
                (xa, ya), (xb, yb) = at(a), at(b)
                P.quad((xa, ya, h), (xa + nout[0] * reveal, ya + nout[1] * reveal, h), (xb + nout[0] * reveal, yb + nout[1] * reveal, h), (xb, yb, h), rm,
                       [(0, 0), (reveal, 0), (reveal, b - a), (0, b - a)], tag="ceiling")
            cursor = b
        if cursor < L - 1e-6:
            wall(cursor, L)


def rect_ring(x0, y0, x1, y1):
    """A rectangle as a clockwise ring starting at its north-west corner."""
    return [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]


# ----------------------------------------------------------------------------- proxies for the offline check
def _tri_raster(a, b, c, cell, origin):
    """Cells (i, j) whose centres lie in the XY triangle, with the z of the triangle's plane there: [(i, j, z)]."""
    x0, y0 = origin
    ax, ay, az = a
    bx, by, bz = b
    cx, cy, cz = c
    i0 = int(math.floor((min(ax, bx, cx) - x0) / cell))
    i1 = int(math.ceil((max(ax, bx, cx) - x0) / cell))
    j0 = int(math.floor((min(ay, by, cy) - y0) / cell))
    j1 = int(math.ceil((max(ay, by, cy) - y0) / cell))
    if i1 < i0 or j1 < j0:
        return []
    gi, gj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="ij")
    px = x0 + (gi + 0.5) * cell
    py = y0 + (gj + 0.5) * cell
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(det) < 1e-9:
        return []
    w1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
    w2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
    w3 = 1.0 - w1 - w2
    eps = -1e-6
    ok = (w1 >= eps) & (w2 >= eps) & (w3 >= eps)
    z = w1 * az + w2 * bz + w3 * cz
    return [(int(i), int(j), float(zz)) for i, j, zz, k in zip(gi[ok], gj[ok], z[ok], range(int(ok.sum())))]


def proxies(parts, cell=0.25, origin=(-3.0, -8.0)):
    """Axis-aligned boxes standing in for every solid part (the offline check's world): boxes, cylinders and frustums by their bounds, bars by theirs, vertical quads as thin
    columns along their bottom edge, horizontal polygons and quads as one-cell slabs (floors below their surface, ceilings above it)."""
    from pipeline import _ear_clip
    out = []
    x0, y0 = origin
    for p in parts:
        tag = p.get("_tag", "")
        if tag in NONSOLID:
            continue
        if "box" in p:
            out.append(tuple(p["box"]))
        elif "cylinder" in p:
            c = p["cylinder"]
            r = c["radius"]
            out.append((c["center"][0] - r, c["center"][1] - r, c["center"][2], c["center"][0] + r, c["center"][1] + r, c["center"][2] + c["height"]))
        elif "frustum" in p:
            c = p["frustum"]
            r = max(c["r0"], c["r1"])
            out.append((c["center"][0] - r, c["center"][1] - r, c["center"][2], c["center"][0] + r, c["center"][1] + r, c["center"][2] + c["height"]))
        elif "bar" in p:
            b = p["bar"]
            a, bb = b["a"], b["b"]
            r = max(b["half_width"], b["half_thickness"])
            out.append((min(a[0], bb[0]) - r, min(a[1], bb[1]) - r, min(a[2], bb[2]) - r, max(a[0], bb[0]) + r, max(a[1], bb[1]) + r, max(a[2], bb[2]) + r))
        elif "quad" in p or "poly" in p:
            pts = p["quad"] if "quad" in p else p["poly"]["points"]
            zs = [q[2] for q in pts]
            horizontal = (max(zs) - min(zs) < 1e-3) or tag in ("floor", "ceiling", "pitfloor") or ("poly" in p and abs(p["poly"]["normal"][2]) > 0.7)
            if not horizontal:
                # a wall: columns along every edge of its footprint, over its whole height
                # (a wall's quads are wound from the bottom edge p0 -> p1, so the columns stand along that one edge; they block but are never standing ground: the 7th item "w")
                a, b = pts[0], pts[1]
                if math.hypot(b[0] - a[0], b[1] - a[1]) >= 1e-3:
                    steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.2))
                    for t in range(steps + 1):
                        f = t / float(steps)
                        x, y = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                        out.append((x - 0.22, y - 0.22, min(zs), x + 0.22, y + 0.22, max(zs), "w"))
                continue
            ceiling = (tag == "ceiling") or ("poly" in p and p["poly"]["normal"][2] < 0) or (tag in ("cap",))
            flat = [(q[0], q[1]) for q in pts]
            for i, j, k in _ear_clip(flat):
                for ci, cj, z in _tri_raster(pts[i], pts[j], pts[k], cell, origin):
                    cx0, cy0 = x0 + ci * cell, y0 + cj * cell
                    if ceiling:
                        out.append((cx0, cy0, z, cx0 + cell, cy0 + cell, z + 1.0))
                    else:
                        out.append((cx0, cy0, z - 1.0, cx0 + cell, cy0 + cell, z))
    return out
