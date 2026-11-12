#!/usr/bin/env python3
"""Procedural meshes for CRYENGINE: build polygons in Python, write a binary FBX, and let the engine's own Resource Compiler
import it to a .cgf. No Autodesk SDK and no download: the FBX is written with fbx_binary.py (standard library only)
and imported by RC's FbxConverter, the same importer the editor's mesh importer calls (fbx_to_caf.py does this for animations).

    import mesh_fbx as M
    m = M.Mesh("wall")
    m.quad((0, 0, 0), (4, 0, 0), (4, 0, 2.9), (0, 0, 2.9), material="plaster", uv=M.world_uv(4, 2.9))
    M.build_cgf("my_wall", [m], ["plaster"], out_dir)         # -> out_dir/my_wall.cgf

Coordinates are CRYENGINE's own (x right, y forward, z up, METRES). UVs are in metres of surface (1 unit = 1 m of wall) so that a
tiling texture keeps its scale on every part whatever its size; the .mtl's texture then repeats once per metre (or per TexMod tile).
Faces are wound counter-clockwise seen from outside; `flip=True` on a polygon reverses it.

Why this exists: the village's houses were scaled unit cubes, whose UVs run 0..1 over a face whatever its size, so any texture would
stretch over a 10 m wall. A mesh with metric UVs textures correctly.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import fbx_binary as FB
from fbx_binary import Node

# Resource Compiler: <engine folder>\Tools\rc\rc.exe; the engine folder is env CRYENGINE_ENGINE_DIR or CRYENGINE_ENGINE, default the Launcher's 5.7.1 LTS folder.
RC = os.path.join(os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"), "Tools", "rc", "rc.exe")

# Blender's constants for a file the Autodesk SDK accepts without a matching creation stamp (its footer id depends on them).
FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
CREATION_TIME = b"1970-01-01 10:00:00:000"


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def world_uv(w, h, u0=0.0, v0=0.0):
    """UVs for a quad a-b-c-d (a at the bottom left, b bottom right, c top right, d top left) that is w by h metres."""
    return [(u0, v0), (u0 + w, v0), (u0 + w, v0 + h), (u0, v0 + h)]


class Mesh:
    """A set of polygons with per-corner UVs and one material name per polygon. Normals are flat per polygon unless given."""

    def __init__(self, name, tiles=None):
        self.name = name
        self.tiles = tiles or {}          # material -> metres one texture tile covers (UVs are divided by it)
        self.parent = None         # the name of the Mesh this one hangs under: a "$lod1_<name>" mesh is parented to its base mesh, which is how the engine's FBX importer finds LODs
        self.verts = []            # (x, y, z)
        self.polys = []            # (index list, material, [uv per corner], normal or None)
        self.detail = 0            # polygons added while this is N (1-3) are left out of LOD N and every coarser one (0 = in every level): set it around the fine parts
        self.poly_detail = []      # the `detail` each polygon was added with
        self._lookup = {}

    def vertex(self, p):
        key = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
        i = self._lookup.get(key)
        if i is None:
            i = len(self.verts)
            self.verts.append(key)
            self._lookup[key] = i
        return i

    def poly(self, points, material, uv=None, normal=None, flip=False):
        pts = list(points)
        if flip:
            pts.reverse()
            if uv:
                uv = list(uv)
                uv.reverse()
        idx = [self.vertex(p) for p in pts]
        if uv is None:
            uv = [(0.0, 0.0)] * len(idx)
        inv = 1.0 / self.tiles.get(material, 1.0)
        uv = [(u * inv, v * inv) for u, v in uv]
        if normal is None:
            a, b, c = (self.verts[idx[0]], self.verts[idx[1]], self.verts[idx[2]])
            normal = norm(cross(sub(b, a), sub(c, a)))
        self.polys.append((idx, material, list(uv), normal))
        self.poly_detail.append(self.detail)

    def lod_copy(self, level, name=None):
        """The mesh of LOD `level` (1-3): every polygon except those added with detail <= level, hung under this mesh as the engine wants ($lodN_<name>)."""
        out = Mesh(name or "$lod%d_%s" % (level, self.name), self.tiles)
        out.parent = self.name
        for (idx, material, uv, normal), d in zip(self.polys, self.poly_detail):
            if d and d <= level:
                continue
            out.polys.append(([out.vertex(self.verts[i]) for i in idx], material, list(uv), normal))
            out.poly_detail.append(0)
        return out

    def quad(self, a, b, c, d, material, uv=None, flip=False):
        self.poly([a, b, c, d], material, uv, None, flip)

    def box(self, x0, y0, z0, x1, y1, z1, material, faces="all", materials=None):
        """An axis-aligned box. UVs are positions in metres on each face's plane (so neighbouring boxes on one wall continue the
        same pattern), divided by the material's tile size. `materials` may map a face name ('-x', '+z' ...) to another material.
        Faces: -x +x -y +y -z +z, wound counter-clockwise seen from outside."""
        mats = materials or {}
        want = set(faces.split()) if faces != "all" else {"-x", "+x", "-y", "+y", "-z", "+z"}
        if "-y" in want:   # seen from -y: x to the right, z up
            self.quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), mats.get("-y", material), [(x0, z0), (x1, z0), (x1, z1), (x0, z1)])
        if "+y" in want:   # seen from +y: x runs to the left
            self.quad((x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1), mats.get("+y", material), [(-x1, z0), (-x0, z0), (-x0, z1), (-x1, z1)])
        if "-x" in want:   # seen from -x: y runs to the left
            self.quad((x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1), mats.get("-x", material), [(-y1, z0), (-y0, z0), (-y0, z1), (-y1, z1)])
        if "+x" in want:
            self.quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1), mats.get("+x", material), [(y0, z0), (y1, z0), (y1, z1), (y0, z1)])
        if "+z" in want:
            self.quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), mats.get("+z", material), [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        if "-z" in want:
            self.quad((x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0), mats.get("-z", material), [(x0, -y1), (x1, -y1), (x1, -y0), (x0, -y0)])

    def bounds(self):
        xs, ys, zs = zip(*self.verts)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _props70(*entries):
    return Node("Properties70", [], [Node("P", [("S", n), ("S", t), ("S", l), ("S", f)] + v) for n, t, l, f, v in entries])


def _name(n, cls):
    return ("S", n.encode("ascii") + b"\x00\x01" + cls.encode("ascii"))


def write_fbx(path, meshes, materials):
    """meshes: list of Mesh; materials: list of material names (the face material index is the position in this list)."""
    ids = {}
    nxt = [1000]

    def new_id(key):
        nxt[0] += 1
        ids[key] = nxt[0]
        return nxt[0]

    mat_index = {m: i for i, m in enumerate(materials)}
    geoms, models, mats, conns = [], [], [], []
    names = [m.name for m in meshes]
    for m in meshes:
        if m.parent is not None and m.parent not in names:
            raise ValueError("mesh %r hangs under %r, which is not in the file" % (m.name, m.parent))
    for m in meshes:
        gid, mid = new_id(("g", m.name)), new_id(("m", m.name))
        verts, pvi, normals, uvs, uvidx, polymat = [], [], [], [], [], []
        for v in m.verts:
            verts.extend(v)
        for idx, material, uv, normal in m.polys:
            for k, vi in enumerate(idx):
                pvi.append(-vi - 1 if k == len(idx) - 1 else vi)
                normals.extend(normal)
                uvidx.append(len(uvs) // 2)
                uvs.extend(uv[k])
            polymat.append(mat_index[material])
        geoms.append(Node("Geometry", [("L", gid), _name(m.name, "Geometry"), ("S", b"Mesh")], [
            Node("Properties70", [], []),
            Node("GeometryVersion", [("I", 124)]),
            Node("Vertices", [("d", verts)]),
            Node("PolygonVertexIndex", [("i", pvi)]),
            Node("LayerElementNormal", [("I", 0)], [
                Node("Version", [("I", 101)]), Node("Name", [("S", b"")]),
                Node("MappingInformationType", [("S", b"ByPolygonVertex")]),
                Node("ReferenceInformationType", [("S", b"Direct")]),
                Node("Normals", [("d", normals)])]),
            Node("LayerElementUV", [("I", 0)], [
                Node("Version", [("I", 101)]), Node("Name", [("S", b"UVMap")]),
                Node("MappingInformationType", [("S", b"ByPolygonVertex")]),
                Node("ReferenceInformationType", [("S", b"IndexToDirect")]),
                Node("UV", [("d", uvs)]), Node("UVIndex", [("i", uvidx)])]),
            Node("LayerElementMaterial", [("I", 0)], [
                Node("Version", [("I", 101)]), Node("Name", [("S", b"")]),
                Node("MappingInformationType", [("S", b"ByPolygon")]),
                Node("ReferenceInformationType", [("S", b"IndexToDirect")]),
                Node("Materials", [("i", polymat)])]),
            Node("Layer", [("I", 0)], [
                Node("Version", [("I", 100)]),
                Node("LayerElement", [], [Node("Type", [("S", b"LayerElementNormal")]), Node("TypedIndex", [("I", 0)])]),
                Node("LayerElement", [], [Node("Type", [("S", b"LayerElementMaterial")]), Node("TypedIndex", [("I", 0)])]),
                Node("LayerElement", [], [Node("Type", [("S", b"LayerElementUV")]), Node("TypedIndex", [("I", 0)])])]),
        ]))
        models.append(Node("Model", [("L", mid), _name(m.name, "Model"), ("S", b"Mesh")], [
            Node("Version", [("I", 232)]),
            _props70(("Lcl Translation", "Lcl Translation", "", "A", [("D", 0.0), ("D", 0.0), ("D", 0.0)])),
            Node("Shading", [("C", True)]),
            Node("Culling", [("S", b"CullingOff")]),
        ]))
        conns.append(Node("C", [("S", b"OO"), ("L", mid), ("L", ids[("m", m.parent)] if m.parent is not None else 0)]))
        conns.append(Node("C", [("S", b"OO"), ("L", gid), ("L", mid)]))
        for matname in materials:                       # the model's material slots, in the face-index order
            key = ("mat", matname)
            if key not in ids:
                xid = new_id(key)
                mats.append(Node("Material", [("L", xid), _name(matname, "Material"), ("S", b"")], [
                    Node("Version", [("I", 102)]), Node("ShadingModel", [("S", b"phong")]), Node("MultiLayer", [("I", 0)]),
                    _props70(("DiffuseColor", "Color", "", "A", [("D", 0.8), ("D", 0.8), ("D", 0.8)]))]))
            conns.append(Node("C", [("S", b"OO"), ("L", ids[key]), ("L", mid)]))

    header = Node("FBXHeaderExtension", [], [
        Node("FBXHeaderVersion", [("I", 1003)]), Node("FBXVersion", [("I", 7400)]), Node("EncryptionType", [("I", 0)]),
        Node("CreationTimeStamp", [], [Node("Version", [("I", 1000)]), Node("Year", [("I", 1970)]), Node("Month", [("I", 1)]),
                                       Node("Day", [("I", 1)]), Node("Hour", [("I", 10)]), Node("Minute", [("I", 0)]),
                                       Node("Second", [("I", 0)]), Node("Millisecond", [("I", 0)])]),
        Node("Creator", [("S", b"ashvale mesh_fbx.py")]),
    ])
    nodes = [
        header,
        Node("FileId", [("R", FILE_ID)]),
        Node("CreationTime", [("S", CREATION_TIME)]),
        Node("Creator", [("S", b"ashvale mesh_fbx.py")]),
        Node("GlobalSettings", [], [
            Node("Version", [("I", 1000)]),
            _props70(("UpAxis", "int", "Integer", "", [("I", 2)]), ("UpAxisSign", "int", "Integer", "", [("I", 1)]),
                     ("FrontAxis", "int", "Integer", "", [("I", 1)]), ("FrontAxisSign", "int", "Integer", "", [("I", -1)]),
                     ("CoordAxis", "int", "Integer", "", [("I", 0)]), ("CoordAxisSign", "int", "Integer", "", [("I", 1)]),
                     ("UnitScaleFactor", "double", "Number", "", [("D", 100.0)]),
                     ("OriginalUnitScaleFactor", "double", "Number", "", [("D", 100.0)])),
        ]),
        Node("Documents", [], [Node("Count", [("I", 1)]), Node("Document", [("L", 1), ("S", b"Scene"), ("S", b"Scene")], [
            Node("Properties70", [], []), Node("RootNode", [("L", 0)])])]),
        Node("References", [], []),
        Node("Definitions", [], [
            Node("Version", [("I", 100)]),
            Node("Count", [("I", 1 + len(models) + len(geoms) + len(mats))]),
            Node("ObjectType", [("S", b"GlobalSettings")], [Node("Count", [("I", 1)])]),
            Node("ObjectType", [("S", b"Model")], [Node("Count", [("I", len(models))])]),
            Node("ObjectType", [("S", b"Geometry")], [Node("Count", [("I", len(geoms))])]),
            Node("ObjectType", [("S", b"Material")], [Node("Count", [("I", len(mats))])]),
        ]),
        Node("Objects", [], geoms + models + mats),
        Node("Connections", [], conns),
        Node("Takes", [], [Node("Current", [("S", b"")])]),
    ]
    FB.write(path, 7400, nodes)


def build_cgf(name, meshes, materials, out_dir, material_file=None, physicalize="default", rc=RC):
    """FBX -> RC -> out_dir/<name>.cgf. `materials`: names in face-index order; the sub-material index in the .cgf is the same.
    material_file: the engine path of the .mtl the .cgf names (the sub-materials must be in the same order)."""
    if not os.path.isfile(rc):
        raise FileNotFoundError("Resource Compiler not found at %s: set CRYENGINE_ENGINE_DIR to the engine install folder" % rc)
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="meshfbx_")
    try:
        fbx = os.path.join(work, name + ".fbx")
        write_fbx(fbx, meshes, materials)
        request = {
            "source_filename": fbx.replace("\\", "/"),
            "output_ext": "cgf",
            "forward_up_axes": "-Y+Z",
            "unit_size": "m",
            "scale": 1.0,
            "merge_all_nodes": False,
            "scene_origin": True,
            "material_filename": material_file or name,
            "materials": [{"name": m, "physicalize": physicalize, "sub_index": i} for i, m in enumerate(materials)],
        }
        js = os.path.join(work, name + ".json")
        with open(js, "w") as f:
            json.dump(request, f)
        proc = subprocess.run([rc, js, "/overwriteextension=fbx", "/refresh"], capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL)
        cgf = os.path.join(work, name + ".cgf")
        errors = [l for l in proc.stdout.splitlines() if l.startswith("[E]") or "rror" in l[:12]]
        if proc.returncode != 0 or not os.path.isfile(cgf):
            raise RuntimeError("rc.exe failed (exit %d): %s" % (proc.returncode, " | ".join(errors) or proc.stdout[-1200:]))
        target = os.path.join(out_dir, name + ".cgf")
        shutil.copyfile(cgf, target)
        return target
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    # a self test: a 2 x 1 x 3 m box in two materials, built and read back with cgf_bounds
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else tempfile.gettempdir()
    m = Mesh("testbox")
    m.box(-1, -0.5, 0, 1, 0.5, 3, "wallmat", materials={"+z": "roofmat", "-z": "roofmat"})
    path = build_cgf("mesh_fbx_selftest", [m], ["wallmat", "roofmat"], out)
    print("wrote", path, os.path.getsize(path), "bytes")
