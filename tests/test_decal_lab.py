#!/usr/bin/env python3
"""Self-test of decal_lab.py: the material rules with positive controls and the GameSDK decal catalog (editor-free), then, with the editor open, a decal in the editor viewport next to controls:
the GameSDK footprint decal is copied into the project (from the local sample pak: nothing is downloaded), looked at over terrain, and removed again. About 60 s live.

    test_decal_lab.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import os
import shutil
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import decal_lab as D

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings):
    return sorted({f["code"] for f in findings})


GOOD = '<Material Shader="Illum" StringGenMask="%ALLOW_SILHOUETTE_POM%BUMP_MAP%DECAL%SUBSURFACE_SCATTERING" Opacity="1"><Textures><Texture Map="Diffuse" File="textures/decals/x/a.tif"/></Textures></Material>'


def main():
    check("control: a decal material under Materials/decals with %DECAL, Illum and a Diffuse texture has no finding", D.check_material_text("materials/decals/x/a.mtl", GOOD, lambda f: True) == [], codes(D.check_material_text("materials/decals/x/a.mtl", GOOD, lambda f: True)))
    r = D.check_material_text("materials/decals/x/a.mtl", GOOD.replace("%DECAL", ""), None)
    check("positive control: a material without the Decal flag is X002, an error", codes(r) == ["X002"] and r[0]["severity"] == "error", codes(r))
    check("positive control: a shader other than Illum is X003", "X003" in codes(D.check_material_text("materials/decals/x/a.mtl", GOOD.replace('Shader="Illum"', 'Shader="Glass"'), None)), "")
    check("positive control: a texture that is not found is X004", codes(D.check_material_text("materials/decals/x/a.mtl", GOOD, lambda f: False)) == ["X004"], "")
    check("positive control: no Diffuse texture is X005", "X005" in codes(D.check_material_text("materials/decals/x/a.mtl", GOOD.replace("Diffuse", "Bumpmap"), None)), "")
    check("a decal material outside Materials/decals is only information (X006)", [f["severity"] for f in D.check_material_text("materials/x/a.mtl", GOOD, None)] == ["info"], "")
    check("positive control: XML that does not parse is X001", codes(D.check_material_text("a.mtl", "<Material", None)) == ["X001"] and codes(D.check_material_text("a.mtl", "<Foo/>", None)) == ["X001"], "")

    d = D.build()
    check("the GameSDK sample has 159 decal materials (mud 35, dirt 32, rubbish 16, shadow 12, blood 10 ...), 158 of them Illum with %DECAL", d["count"] == 159 and d["folders"]["mud"] == 35 and d["folders"]["blood"] == 10 and sum(1 for v in d["materials"].values() if v["decal_flag"]) == 158, (d["count"], d["folders"].get("mud")))
    rows, total = D.search("footprint")
    check("the footprint decals are found by texture name (footstep_dirt and footstep_snow share footprint_sand_left)", total == 2 and {r["name"] for r in rows} == {"footstep_dirt", "footstep_snow"}, total)
    fp = d["materials"]["materials/decals/footsteps/footstep_dirt.mtl"]
    check("a GameSDK decal's public parameters are DecalDiffuseOpacity, DecalFalloff, DecalAlphaMult (and IndirectColor)", {"DecalDiffuseOpacity", "DecalFalloff", "DecalAlphaMult"} <= set(fp["public"]), sorted(fp["public"]))

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live and any(os.path.exists(os.path.join(D.ASSETS, "materials", "decals")) for _ in (0,)):
        # since 2026-10-23 the project owns real decals (Assets/materials/decals/blood): the live part copies a GameSDK decal into that folder and removes the folder afterwards, which would delete them
        print("  --   Assets/materials/decals exists (the project's own blood decals): the live decal checks are skipped so nothing of it is touched")
        live = False
    if live:
        import import_gamesdk_asset as I
        import object_props as O
        made = [os.path.join(D.ASSETS, "materials", "decals"), os.path.join(D.ASSETS, "textures", "decals")]
        if any(os.path.exists(p) for p in made):
            raise SystemExit("Assets/materials/decals or Assets/textures/decals already exists: the test would delete it, so it stops")
        try:
            I.main(["Materials/decals/footsteps/footstep_dirt.mtl"])
            import material_check as M
            mc = M.check(["materials/decals/footsteps/footstep_dirt.mtl"])
            check("the copied decal material loads in the engine as an Illum material with its textures bound (the editor material manager): no finding", mc["ok"] and not [f for f in mc["findings"] if f["severity"] != "info"], [(f["code"], f["message"][:30]) for f in mc["findings"]][:2])
            ok_files = D.check_material_text("materials/decals/footsteps/footstep_dirt.mtl", open(os.path.join(D.ASSETS, "materials", "decals", "footsteps", "footstep_dirt.mtl"), encoding="utf-8").read(), None)
            check("and it passes the decal rules", ok_files == [], codes(ok_files))
            n_before = O.object_count()
            ok = False
            for attempt in (1, 2):                      # a picture measurement: the scene drifts; one repeat is allowed before the check fails
                r = D.preview("materials/decals/footsteps/footstep_dirt", 487, 392, projection=2, size=3.0)
                ok = r["changed_pixels"] > 5 * max(r["noise_pixels"], 500) and r["mean_rgb_there"][0] < r["mean_rgb_control_there"][0] - 20
                if ok:
                    break
            check("a Decal object with this material over terrain DARKENS the ground under it (editor viewport): changed pixels well above the two-control noise, and the changed pixels got darker", ok, (r["changed_pixels"], r["noise_pixels"], r["mean_rgb_control_there"], r["mean_rgb_there"]))
            check("measured: with the decal 2 m above the ground (ProjectionDepth is 1) nothing draws, for ANY projection type including Planar: the editor viewport shows decals only within the depth",
                  all(D.preview("materials/decals/footsteps/footstep_dirt", 487, 392, projection=p, size=3.0, lift=2.0)["changed_pixels"] < 3 * max(1700, 1) for p in (0, 2)), "")
            check("the scratch Decal object is gone: the level has the object count it had", O.object_count() == n_before == r["objects_after"], (n_before, O.object_count()))
        finally:
            for p in made:
                shutil.rmtree(p, ignore_errors=True)
        left = [p for p in made if os.path.exists(p)] + glob.glob(os.path.join(D.ASSETS, "materials", "decals*"))
        check("the project is as it was: the copied material and textures are removed", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
