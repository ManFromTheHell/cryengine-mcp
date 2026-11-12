#!/usr/bin/env python3
"""Decals (ENGINE_LEARNING_PLAN F3): the GameSDK sample's decal materials as a catalog, a check of a decal material against the manual's rules, and a LOOK at a decal in the EDITOR viewport. The game is not run.

Manual: 'Decal Entity' (36869871), 'Effects Components' - Decal (44966055). A Decal object projects a material onto geometry and terrain. The material must carry the Decal shader
generation flag (%DECAL; every material under Materials/decals is set up that way). Properties of the editor object: Material, ProjectionType (0 Planar: exactly where it is placed, cheapest, flat surfaces
only; 1 ProjectOnStaticObjects; 2 ProjectOnTerrain; 3 ProjectOnTerrainAndStaticObjects; 1 and 3 are done as a deferred pass), Deferred, ViewDistRatio, SortPriority, ProjectionDepth. The decal looks DOWN its
own Z axis ('projected along the opposite direction of the blue Z axis'). The material's public parameters DecalDiffuseOpacity, DecalFalloff and DecalAlphaMult shape it.

    python screens/decal_lab.py                 the catalog summary (159 GameSDK decal materials, by folder)
    python screens/decal_lab.py check <mtl>     one material against the rules
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
DATA = os.path.join(HERE, "engine_data")
CATALOG = os.path.join(DATA, "gamesdk_decals.json")
sys.path.insert(0, HERE)

DECAL_BIT = 0x800                                   # the engine's common flag table: %DECAL 800 (user/shaders/cache/globals.txt)
PROJECTIONS = {0: "Planar", 1: "ProjectOnStaticObjects", 2: "ProjectOnTerrain", 3: "ProjectOnTerrainAndStaticObjects"}


class DecalError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def check_material_text(path, text, exists=None):
    """A decal material's XML against the rules -> [finding]
    X001 not a material / not XML     X002 the Decal flag is missing from StringGenMask (the engine then draws it as an ordinary surface, not a projection)
    X003 the shader is not Illum (decals are an Illum feature)     X004 a texture file is not found     X005 no Diffuse texture (nothing to project)
    X006 the material is outside Materials/decals (works, but breaks the convention the manual relies on)"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [_finding("error", "X001", path, "not valid XML: %s" % exc)]
    if root.tag != "Material":
        return [_finding("error", "X001", path, "root element is <%s>, not <Material>" % root.tag)]
    out = []
    names = [n for n in (root.get("StringGenMask") or "").split("%") if n]
    if "DECAL" not in {n.upper() for n in names}:
        out.append(_finding("error", "X002", path, "StringGenMask '%s' has no %%DECAL: the material is not a decal" % (root.get("StringGenMask") or ""), "add %DECAL (every material under Materials/decals has it)"))
    if (root.get("Shader") or "").split(".")[0].lower() != "illum":
        out.append(_finding("warning", "X003", path, "shader '%s': decal materials are Illum materials" % root.get("Shader")))
    tex = root.find("Textures")
    files = [(t.get("Map"), t.get("File")) for t in (tex.findall("Texture") if tex is not None else []) if t.get("File")]
    if not any(m == "Diffuse" for m, _ in files):
        out.append(_finding("error", "X005", path, "no Diffuse texture: there is nothing to project"))
    if exists is not None:
        for m, f in files:
            if not exists(f):
                out.append(_finding("error", "X004", path, "%s texture '%s' not found" % (m, f)))
    if "materials/decals/" not in path.replace(chr(92), "/").lower():
        out.append(_finding("info", "X006", path, "outside Materials/decals: it works, but the manual says everything under that folder is set up as a decal"))
    return out


def build(refresh=True):
    """Read the GameSDK sample's decal materials from its paks (read-only) -> engine_data/gamesdk_decals.json"""
    import import_gamesdk_asset as I
    paks = I.Paks()
    rows = {}
    for key in sorted(paks.with_prefix("materials/decals/")):
        if not key.endswith(".mtl"):
            continue
        text = paks.read(key).decode("utf-8", "replace")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        subs = root.findall("./SubMaterials/Material") or [root]
        names = [n for n in (root.get("StringGenMask") or "").split("%") if n]
        tex = [t.get("File") for s in subs for t in (s.find("Textures").findall("Texture") if s.find("Textures") is not None else []) if t.get("File")]
        pub = root.find("PublicParams")
        rows[key] = {"folder": key.split("/")[2] if key.count("/") > 2 else "(root)", "name": os.path.splitext(os.path.basename(key))[0], "shader": root.get("Shader"), "decal_flag": "DECAL" in {n.upper() for n in names} or any("DECAL" in (s.get("StringGenMask") or "").upper() for s in subs),
                     "textures": tex, "opacity": root.get("Opacity"), "public": dict(pub.attrib) if pub is not None else {}}
    folders = {}
    for r in rows.values():
        folders[r["folder"]] = folders.get(r["folder"], 0) + 1
    data = {"count": len(rows), "folders": folders, "materials": rows, "all_flagged": all(r["decal_flag"] for r in rows.values())}
    os.makedirs(DATA, exist_ok=True)
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def load(refresh=False):
    if refresh or not os.path.isfile(CATALOG):
        return build()
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def search(text="", limit=40):
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    rows = [dict(path=k, **{x: v[x] for x in ("folder", "name", "textures", "public")}) for k, v in sorted(load()["materials"].items()) if all(w in (k + " " + " ".join(v["textures"])).lower() for w in words)]
    return rows[:limit], len(rows)


def ground_z(x, y):
    """Terrain height at (x, y) from the editor's own heightmap (verb bridge.terrain.get_heights_raw; 1 m cells)."""
    import numpy as np
    import bridge_py
    os.makedirs(os.path.join(DATA, "decal_scratch"), exist_ok=True)
    p = os.path.join(DATA, "decal_scratch", "heights_%d.r32" % os.getpid())
    r = bridge_py.send("bridge.terrain.get_heights_raw " + p.replace(chr(92), "/"), 60)
    if not r.get("ok"):
        raise DecalError("cannot read the heightmap: %s" % r.get("error"))
    flat = np.fromfile(p, dtype=np.float32)
    os.remove(p)
    n = int(round(flat.size ** 0.5))
    if n < 2 or n * n != flat.size:
        raise DecalError("the heightmap is not square (%d values)" % flat.size)
    h = flat.reshape(n, n)                       # cells are assumed to be 1 m
    x0, y0 = min(max(int(x), 0), n - 2), min(max(int(y), 0), n - 2)
    fx, fy = min(max(x - x0, 0.0), 1.0), min(max(y - y0, 0.0), 1.0)
    return float(h[x0, y0] * (1 - fx) * (1 - fy) + h[x0 + 1, y0] * fx * (1 - fy) + h[x0, y0 + 1] * (1 - fx) * fy + h[x0 + 1, y0 + 1] * fx * fy)


def preview(material, x, y, projection=2, deferred=0, size=3.0, lift=0.3, distance=5.0, height=3.0, name="zz_decal_preview"):
    """Look at a decal in the EDITOR viewport: two control pictures of the ground with NO decal, then a scratch Decal object (Material, ProjectionType, Deferred, scale `size` metres, `lift` above the
    ground, looking down its Z axis) and one picture; the object is deleted again (the count is checked: a graceful editor close would SAVE a leftover). The editor draws a small icon at the object, so
    the measurement leaves out a square around the object's centre. -> {picture, changed_pixels, noise_pixels, ring_changed, ring_noise, mean_rgb_there, mean_rgb_control_there, ...}"""
    import numpy as np
    import debug_views as D
    import object_props as O
    import shot_compare as S
    z = ground_z(x, y)
    eye, target = [x, y - float(distance), z + float(height)], [x, y, z]
    before = O.object_count()
    c0 = S.load(D.shoot(name + "_ctrl0", eye, target))
    c1 = S.load(D.shoot(name + "_ctrl1", eye, target))
    with O.scratch(name):
        O.create("Decal", name, [x, y, z + float(lift)])
        O.set_properties(name, {"Material": material, "ProjectionType": str(int(projection)), "Deferred": str(int(deferred)), "Scale": "%g,%g,%g" % (size, size, size), "ViewDistRatio": "100"})
        time.sleep(2.0)
        pic = D.shoot(name + "_with", eye, target)
        img = S.load(pic)
    after = O.object_count()
    h, w = img.shape[:2]
    cy, cx = int(.5 * h), int(.5 * w)
    mask = np.zeros((h, w), bool)
    mask[int(.2 * h):int(.85 * h), int(.2 * w):int(.8 * w)] = True
    mask[cy - 14:cy + 14, cx - 14:cx + 14] = False                      # the editor's icon at the object
    diff = np.abs(img.astype(int) - c0.astype(int)).max(axis=2) > 24
    noise = np.abs(c1.astype(int) - c0.astype(int)).max(axis=2) > 24
    n, n0 = int((diff & mask).sum()), int((noise & mask).sum())
    return {"picture": pic, "changed_pixels": n, "noise_pixels": n0, "mean_rgb_there": img[diff & mask].mean(axis=0).round(0).tolist() if n else None,
            "mean_rgb_control_there": c0[diff & mask].mean(axis=0).round(0).tolist() if n else None, "ground_z": round(z, 2), "objects_before": before, "objects_after": after,
            "projection": PROJECTIONS.get(int(projection)), "deferred": int(deferred)}


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "check":
        p = sys.argv[2]
        for f in check_material_text(p, open(p, encoding="utf-8").read()):
            print(f)
        return
    d = load()
    print("%d GameSDK decal materials, every one flagged %%DECAL: %s" % (d["count"], d["all_flagged"]))
    for k, v in sorted(d["folders"].items(), key=lambda kv: -kv[1]):
        print("  %-12s %d" % (k, v))


if __name__ == "__main__":
    main()
