#!/usr/bin/env python3
"""The archers' weapons: an arrow and a bow, modelled in Python and imported by the engine's own RC (mesh_fbx.py).

    weapon_kit.py            build Assets/objects/ashvale/weapons/{Arrow,Bow}.cgf and Assets/Materials/ashvale/weapons.mtl

Arrow.cgf   0.88 m along +Y (the engine's forward): the nock end at y = 0, the steel head's tip at y = 0.88, two crossed feather
            vanes near the nock. It is flown by CBanditBrain and drawn along its flight direction.
Bow.cgf     1.14 m along Z, grip at the origin, the belly bulging toward +X, the string a straight line between the tips.
            CNpcComponent holds it in the right hand where a bandit's club would be.
Flat colours (no textures): at the distances these are seen, a texture would not show.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import math
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import mesh_fbx as M

PROJECT = __import__("project_root").PROJECT
OUT = os.path.join(PROJECT, "Assets", "objects", "ashvale", "weapons")
MTL_PATH = "materials/ashvale/weapons"
MATERIALS = ["arrow_wood", "arrow_steel", "feather", "bow_wood", "bow_string"]
COLOURS = {   # diffuse, specular, shininess
    "arrow_wood": ("0.46,0.31,0.16", "0.04,0.04,0.04", 40),
    "arrow_steel": ("0.45,0.46,0.48", "0.60,0.60,0.62", 180),
    "feather": ("0.86,0.83,0.72", "0.03,0.03,0.03", 20),
    "bow_wood": ("0.30,0.17,0.07", "0.05,0.05,0.05", 60),
    "bow_string": ("0.80,0.76,0.62", "0.02,0.02,0.02", 10),
}


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def outward_quad(mesh, pts, centre, material):
    """A quad wound counter-clockwise seen from the side away from `centre` (the solid's inside)."""
    n = cross(sub(pts[1], pts[0]), sub(pts[2], pts[0]))
    mid = tuple(sum(p[i] for p in pts) / 4.0 for i in range(3))
    away = sub(mid, centre)
    flip = n[0] * away[0] + n[1] * away[1] + n[2] * away[2] < 0
    # metric UVs (metres on the face: u along the first edge, v across it) so a textured bar keeps its texture scale
    e1 = sub(pts[1], pts[0])
    l1 = math.sqrt(sum(c * c for c in e1)) or 1.0
    e1 = tuple(c / l1 for c in e1)
    ln = math.sqrt(sum(c * c for c in n)) or 1.0
    e2 = cross(tuple(c / ln for c in n), e1)
    uv = []
    for p in pts:
        d = sub(p, pts[0])
        uv.append((d[0] * e1[0] + d[1] * e1[1] + d[2] * e1[2], d[0] * e2[0] + d[1] * e2[1] + d[2] * e2[2]))
    mesh.poly(pts, material, uv, None, flip)


def prism(mesh, a, b, hw, hy, material, plane_normal=(0.0, 1.0, 0.0)):
    """A rectangular bar from a to b, hw half-width across the in-plane perpendicular, hy half-thickness along plane_normal."""
    d = sub(b, a)
    length = math.sqrt(sum(c * c for c in d)) or 1.0
    d = tuple(c / length for c in d)
    side = cross(d, plane_normal)
    sl = math.sqrt(sum(c * c for c in side)) or 1.0
    side = tuple(c / sl for c in side)
    up = plane_normal
    corners = {}
    for tag, p in (("a", a), ("b", b)):
        for sx in (-1, 1):
            for sy in (-1, 1):
                corners[(tag, sx, sy)] = tuple(p[i] + side[i] * hw * sx + up[i] * hy * sy for i in range(3))
    centre = tuple((a[i] + b[i]) / 2.0 for i in range(3))
    for sx, sy, tx, ty in ((-1, -1, 1, -1), (1, -1, 1, 1), (1, 1, -1, 1), (-1, 1, -1, -1)):   # the four long faces
        outward_quad(mesh, [corners[("a", sx, sy)], corners[("a", tx, ty)], corners[("b", tx, ty)], corners[("b", sx, sy)]], centre, material)
    outward_quad(mesh, [corners[("a", -1, -1)], corners[("a", 1, -1)], corners[("a", 1, 1)], corners[("a", -1, 1)]], centre, material)
    outward_quad(mesh, [corners[("b", -1, -1)], corners[("b", 1, -1)], corners[("b", 1, 1)], corners[("b", -1, 1)]], centre, material)


def arrow():
    m = M.Mesh("Arrow")
    h = 0.008                                             # shaft half-thickness (16 mm: this is seen from 5 to 25 m)
    m.box(-h, 0.0, -h, h, 0.78, h, "arrow_wood")
    # the head: a four-sided steel pyramid, base at y = 0.78, tip at y = 0.88
    base = 0.022
    tip = (0.0, 0.88, 0.0)
    ring = [(-base, 0.78, -base), (base, 0.78, -base), (base, 0.78, base), (-base, 0.78, base)]
    centre = (0.0, 0.80, 0.0)
    for i in range(4):
        tri = [ring[i], ring[(i + 1) % 4], tip]
        n = cross(sub(tri[1], tri[0]), sub(tri[2], tri[0]))
        mid = tuple(sum(p[k] for p in tri) / 3.0 for k in range(3))
        away = sub(mid, centre)
        m.poly(tri, "arrow_steel", None, None, n[0] * away[0] + n[1] * away[1] + n[2] * away[2] < 0)
    m.quad(ring[3], ring[2], ring[1], ring[0], "arrow_steel")       # the base, facing back along the shaft
    # two crossed feather vanes near the nock, each drawn from both sides
    for pts in (
        [(0.0, 0.01, -0.04), (0.0, 0.17, -0.04), (0.0, 0.17, 0.04), (0.0, 0.01, 0.04)],
        [(-0.04, 0.01, 0.0), (-0.04, 0.17, 0.0), (0.04, 0.17, 0.0), (0.04, 0.01, 0.0)],
    ):
        m.poly(pts, "feather")
        m.poly(pts, "feather", flip=True)
    return m


def bow():
    m = M.Mesh("Bow")
    half = 0.57
    belly = 0.15                                          # how far the grip stands out from the line of the string
    steps = 8
    pts = []
    for i in range(steps + 1):
        z = -half + 2.0 * half * i / steps
        pts.append((belly * (1.0 - (z / half) ** 2) ** 0.9, 0.0, z))
    for a, b in zip(pts, pts[1:]):
        prism(m, a, b, 0.014, 0.012, "bow_wood")
    prism(m, (0.0, 0.0, -half), (0.0, 0.0, half), 0.003, 0.003, "bow_string")      # the string, tip to tip
    # a leather-wrapped grip in the middle of the stave
    prism(m, (belly - 0.01, 0.0, -0.06), (belly - 0.01, 0.0, 0.06), 0.02, 0.02, "bow_wood")
    return m


def write_materials():
    lines = ['<Material MtlFlags="524544">', ' <SubMaterials>']
    for name in MATERIALS:
        diffuse, specular, shin = COLOURS[name]
        lines.append('  <Material Name="%s" MtlFlags="524416" Shader="Illum" GenMask="0" StringGenMask="" SurfaceType="mat_default" MatTemplate="" '
                     'Diffuse="%s" Specular="%s" Emissive="0,0,0" Shininess="%d" Opacity="1" LayerAct="1">' % (name, diffuse, specular, shin))
        lines.append('   <Textures><Texture Map="Diffuse" File="%engine%/EngineAssets/TextureMsg/color_white.dds"/></Textures>')
        lines.append('   <PublicParams SSSIndex="0"/>')
        lines.append('  </Material>')
    lines += [' </SubMaterials>', '</Material>', '']
    path = os.path.join(PROJECT, "Assets", "Materials", "ashvale", "weapons.mtl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", newline="\n").write("\n".join(lines))
    print("wrote", path)


def main():
    write_materials()
    for mesh in (arrow(), bow()):
        path = M.build_cgf(mesh.name, [mesh], MATERIALS, OUT, material_file=MTL_PATH)
        lo, hi = mesh.bounds()
        print("%-6s -> %s  bounds x %.3f..%.3f  y %.3f..%.3f  z %.3f..%.3f  (%d polygons)" %
              (mesh.name, path, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2], len(mesh.polys)))


if __name__ == "__main__":
    main()
