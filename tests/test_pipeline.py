#!/usr/bin/env python3
"""End-to-end test of pipeline.py in a throwaway folder: a texture set with a height map -> a material that uses it -> a mesh that names
the material, each checked on what is ON DISK (the .dds formats, the .mtl's flags, the .cgf's material name and used ids), plus the refusals.
Everything is written under Assets/<textures|materials|objects>/_mcp_selftest and removed at the end (also on failure).

    test_pipeline.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import asset_lint as L
import pipeline as P

ASSETS = P.ASSETS
TMP = [os.path.join(ASSETS, top, "_mcp_selftest") for top in ("textures", "materials", "objects")]
SRC = [os.path.join(HERE, "texture_src", "selftest_stone_" + s + ".tif") for s in ("diff", "ddna", "displ")]
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-62s %s" % ("ok  " if ok else "FAIL", name, detail))


def refuses(name, fn, *a, **k):
    try:
        fn(*a, **k)
    except P.PipelineError as exc:
        check(name, True, str(exc)[:70])
        return
    check(name, False, "was accepted")


def cleanup():
    for d in TMP:
        if os.path.basename(d) == "_mcp_selftest":
            for _ in range(20):                     # the open editor watches new asset folders and may hold one for a moment (it left an empty folder behind in the editor suite)
                if not os.path.isdir(d):
                    break
                shutil.rmtree(d, ignore_errors=True)
                if os.path.isdir(d):
                    time.sleep(0.5)
    for f in SRC:
        if os.path.isfile(f):
            os.remove(f)


def main():
    cleanup()
    try:
        # --- textures: the three maps, with the height map in BC4 (RC's BC3 trap)
        t = P.make_texture("stone", "selftest_stone", 256, "textures/_mcp_selftest")
        fmt = {f["path"].rsplit("_", 1)[1][:-4]: f["format"] for f in t["files"]}
        check("texture: diff BC1, ddna BC5, displ BC4 (not RC's BC3)", fmt.get("diff", "").startswith("BC1") and fmt.get("ddna") in ("BC5", "BC5 snorm") and fmt.get("displ") == "BC4", str(fmt))
        check("texture: linter clean", t["ok"], str(t["lint"])[:80])
        refuses("texture: a second make without overwrite is refused", P.make_texture, "stone", "selftest_stone", 256, "textures/_mcp_selftest")
        refuses("texture: '..' in the folder is refused", P.make_texture, "stone", "x", 256, "textures/../objects")
        refuses("texture: an unknown recipe is refused", P.make_texture, "lava", "x", 256, "textures/_mcp_selftest")
        # --- material: flags agree with the Heightmap slot, glass is see-through
        m = P.write_material("materials/_mcp_selftest/stool", [
            {"name": "stone", "texture": t["material_texture"], "height_depth": 0.03, "surface_type": "mat_rock", "shininess": 110},
            {"name": "glass", "glass": True}])
        text = open(os.path.join(ASSETS, "materials", "_mcp_selftest", "stool.mtl"), encoding="utf-8").read()
        check("material: POM+OBM mask 88020081 with a Heightmap slot", 'GenMask="88020081"' in text and 'Map="Heightmap"' in text and "PomDisplacement" in text)
        check("material: glass is nearly transparent (Opacity 0.08)", 'Opacity="0.08"' in text)
        check("material: linter clean", m["ok"], str(m["lint"])[:80])
        refuses("material: a height map that was never compiled is refused", P.write_material, "materials/_mcp_selftest/bad", [{"name": "x", "texture": "textures/_mcp_selftest/nothing", "height_depth": 0.02}])
        refuses("material: an existing file without overwrite is refused", P.write_material, "materials/_mcp_selftest/stool", [{"name": "a"}])
        # the surface types are the project's own (Libs/MaterialEffects/SurfaceTypes.xml): a hard-coded list once let mat_dirt through (undefined: mat_default plus a warning) and refused mat_soil
        check("material: the allowed surface types are the project's SurfaceTypes.xml (mat_soil, mat_fabric, mat_flesh yes; mat_dirt, mat_gravel no)",
              all(s in P.MTL_SURFACES for s in ("mat_soil", "mat_fabric", "mat_flesh", "mat_rock")) and not any(s in P.MTL_SURFACES for s in ("mat_dirt", "mat_gravel", "mat_sand", "mat_mud")), str(P.MTL_SURFACES)[:80])
        refuses("material: an undefined surface type (mat_dirt) is refused", P.write_material, "materials/_mcp_selftest/dirty", [{"name": "d", "surface_type": "mat_dirt"}])
        mm = P.write_material("materials/_mcp_selftest/metal", [{"name": "iron", "tint": "0.55,0.54,0.52", "surface_type": "mat_metal", "shininess": 110, "metal": True}, {"name": "plain", "tint": "0.5,0.4,0.3"}])
        mtext = open(os.path.join(ASSETS, "materials", "_mcp_selftest", "metal.mtl"), encoding="utf-8").read()
        check("material: metal=true puts the colour in the Specular and keeps Diffuse near black (the manual's PBS rule); a plain material keeps its colour in Diffuse",
              'Name="iron"' in mtext and 'Diffuse="0.02,0.02,0.02" Specular="0.55,0.54,0.52"' in mtext and 'Diffuse="0.5,0.4,0.3" Specular="0.021219011' in mtext and mm["ok"], "")
        # --- mesh: every part kind, the material name read back from the .cgf
        spec = {"name": "stool", "folder": "objects/_mcp_selftest", "material_file": m["game_path"], "materials": ["stone", "glass"], "tiles": {"stone": 1.5},
                "parts": [{"box": [-0.2, -0.2, 0.4, 0.2, 0.2, 0.45], "material": "stone"},
                          {"bar": {"a": [-0.15, -0.15, 0.0], "b": [-0.15, -0.15, 0.4], "half_width": 0.02, "half_thickness": 0.02}, "material": "stone"},
                          {"cylinder": {"center": [0.15, 0.15, 0.0], "radius": 0.03, "height": 0.4, "sides": 10}, "material": "stone"},
                          {"quad": [[-0.2, 0.2, 0.5], [0.2, 0.2, 0.5], [0.2, 0.2, 0.9], [-0.2, 0.2, 0.9]], "material": "glass"}]}
        b = P.build_mesh(spec)
        check("mesh: built and the linter is clean", b["ok"], "%d polys %s" % (b["polygons"], b["problems"]))
        check("mesh: the .cgf names the material file given", b["material_named_in_cgf"] == m["game_path"], str(b["material_named_in_cgf"]))
        check("mesh: it uses material id 1 (the glass) and the .mtl has two", b["highest_material_id_used"] == 1, str(b["highest_material_id_used"]))
        check("mesh: bounds are the model's (0.4 wide, 0.9 tall)", abs(b["bounds_m"]["size"][0] - 0.4) < 0.01 and abs(b["bounds_m"]["size"][2] - 0.9) < 0.01, str(b["bounds_m"]["size"]))
        refuses("mesh: a part with a material not in the list is refused", P.build_mesh, dict(spec, name="bad1", parts=[{"box": [0, 0, 0, 1, 1, 1], "material": "nope"}]))
        refuses("mesh: an existing .cgf without overwrite is refused", P.build_mesh, spec)
        refuses("mesh: a folder outside objects/ is refused", P.build_mesh, dict(spec, name="bad2", folder="materials/_mcp_selftest"))
        # the control for the material-name check: a mesh naming a material that does not exist must NOT come back ok
        g = P.build_mesh(dict(spec, name="ghost", material_file="materials/_mcp_selftest/ghost_material"))
        check("mesh control: a missing .mtl is reported (ok false, L001)", (not g["ok"]) and any(f["code"] == "L001" for f in g["lint"]), str(g["problems"])[:70])
        # --- levels of detail: $lodN_<name> nodes under the base mesh, read back from the compiled file
        bolts = [{"box": [-0.18 + 0.06 * k, -0.22, 0.35, -0.14 + 0.06 * k, -0.2, 0.39], "material": "stone", "detail": 1} for k in range(6)]
        knobs = [{"box": [-0.2, -0.2, 0.45 + 0.1 * k, -0.18, -0.18, 0.47 + 0.1 * k], "material": "stone", "detail": 2} for k in range(3)]
        lodspec = {"name": "stool_detail", "folder": "objects/_mcp_selftest", "material_file": m["game_path"], "materials": ["stone", "glass"], "lods": 2,
                   "parts": [{"box": [-0.2, -0.2, 0.0, 0.2, 0.2, 0.45], "material": "stone"}, {"cylinder": {"center": [0.0, 0.0, 0.45], "radius": 0.05, "height": 0.3, "sides": 16}, "material": "stone"}] + bolts + knobs}
        lb = P.build_mesh(lodspec)
        tris = [r["triangles"] for r in lb["lods"]]
        check("lods: the compiled .cgf carries LOD 0, 1 and 2, each with fewer triangles than the one before (%s)" % tris, [r["level"] for r in lb["lods"]] == [0, 1, 2] and tris[0] > tris[1] > tris[2], tris)
        check("lods: the nodes are named $lod1_stool_detail and $lod2_stool_detail (the engine's naming) and the build is ok", [r["node"] for r in lb["lods"]] == ["stool_detail", "$lod1_stool_detail", "$lod2_stool_detail"] and lb["ok"], str(lb["problems"])[:70])
        check("lods: a part with detail=1 is not in LOD 1 (6 bolts x 12 triangles = 72 fewer) and a cylinder loses sides (16 -> 8 -> 6)", tris[0] - tris[1] >= 72 + 16 * 2 - 8 * 2 - 1, tris)
        plain_b = P.build_mesh(dict(lodspec, name="stool_plain", lods=0))
        check("lods: without lods the mesh has only the base node (control)", [r["level"] for r in plain_b["lods"]] == [0], str(plain_b["lods"]))
        heavy_parts = [{"box": [-1.0 + 0.1 * k, -1.0, 0.0, -0.95 + 0.1 * k, 1.0, 0.5 + 0.01 * k], "material": "stone", "detail": 1 if k % 2 else None} for k in range(20)]
        heavy_parts = [{k: v for k, v in part.items() if v is not None} for part in heavy_parts] * 5
        heavy_plain = P.build_mesh(dict(lodspec, name="heavy_plain", lods=0, parts=heavy_parts))
        heavy_levels = P.build_mesh(dict(lodspec, name="heavy_levels", lods=1, parts=heavy_parts))
        listed = L.heavy_without_lod([os.path.join(ASSETS, "objects", "_mcp_selftest", n + ".cgf") for n in ("heavy_plain", "heavy_levels", "stool_detail")])
        check("L013: a mesh of %d triangles with no LOD is listed, the same mesh with a LOD (and a light one) is not" % heavy_plain["lods"][0]["triangles"],
              [os.path.basename(p) for t, p in listed] == ["heavy_plain.cgf"] and heavy_plain["lods"][0]["triangles"] >= L.HEAVY_TRIANGLES, [(t, os.path.basename(p)) for t, p in listed])
        refuses("lods: more than 3 extra levels are refused", P.build_mesh, dict(lodspec, name="bad3", lods=4))
        refuses("lods: a part's detail outside 1..3 is refused", P.build_mesh, dict(lodspec, name="bad4", parts=[{"box": [0, 0, 0, 1, 1, 1], "material": "stone", "detail": 0}]))
        refuses("lods: a LOD that would be empty is refused", P.build_mesh, dict(lodspec, name="bad5", lods=1, parts=[{"box": [0, 0, 0, 1, 1, 1], "material": "stone", "detail": 1}]))
        refuses("a mesh path containing '_lod' is refused (the engine would not load it)", P.build_mesh, dict(lodspec, name="stool_lod_x"))
        refuses("and so is a folder containing '_lod'", P.build_mesh, dict(lodspec, name="stool_ok", folder="objects/_mcp_selftest/zz_lod"))
        # --- and the whole project is still clean with the throwaway files in it
        full = L.lint(None, 20)
        mine = [f for f in full["findings"] if "_mcp_selftest" in f["path"] and f.get("severity", "error") == "error"]      # other errors the project may have are not this test's business
        check("project lint with the throwaway files: only the planted ghost mesh fails (among the throwaway files)", len(mine) == 1 and mine[0]["path"].endswith("ghost.cgf"), "%d error(s) of mine, %d in all" % (len(mine), full["errors"]))
    finally:
        cleanup()
    left = [d for d in TMP if os.path.exists(d)]
    check("cleanup: the throwaway folders are gone", not left, str(left))
    bad = results.count(False)
    print("pipeline test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
