#!/usr/bin/env python3
"""Self-test of material_check.py: the comparison on synthetic engine rows (offline), then, with the editor open, every material of the project through the engine's own material manager
and deliberately broken copies of cloth.mtl (positive controls) next to an unchanged copy (control). Copies get unique names (CryPak caches a loose file by name), the editor writes a
.mtl.cryasset next to each of them, and all of it is deleted again. About 20 s live.

    test_material_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import re
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import material_check as M

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


def good_row():
    return {"file": "materials/x", "found": True, "already_loaded": False, "is_default": False, "name": "materials/x", "flags": 0, "log": [],
            "subs": [{"slot": -1, "name": "x", "surface": "mat_wood", "surface_id": 5, "shader": "Illum", "gen_mask": "4000000000020",
                      "textures": [{"slot": "diffuse", "file": "materials/x_diff.tif", "bound": True, "bound_file": "materials/x_diff.dds", "w": 64, "h": 64, "loaded": False},
                                   {"slot": "normals", "file": "materials/x_ddna.tif", "bound": True, "bound_file": "materials/x_ddna.dds", "w": 64, "h": 64, "loaded": True}]}]}


def good_file():
    return [{"slot": -1, "name": "x", "shader": "Illum", "surface": "mat_wood", "string_gen": "%NORMAL_MAP%PARALLAX_OCCLUSION_MAPPING", "gen": "0", "textures": ["materials/x_diff.tif", "materials/x_ddna.tif"]}]


TABLE = {"NORMAL_MAP": 0x20, "PARALLAX_OCCLUSION_MAPPING": 0x8000000, "SUBSURFACE_SCATTERING": 0x4000000000000}


def resolve(shader):
    return TABLE, True


def main():
    # ---- the comparison, offline
    check("control: an engine row that matches its file has no finding (mask carries NORMAL_MAP, but POM is asked for and missing -> only M004)", codes(M.compare(good_row(), good_file(), resolve, lambda f: True)) == ["M004"], codes(M.compare(good_row(), good_file(), resolve, lambda f: True)))
    ok = good_row()
    ok["subs"][0]["gen_mask"] = "4000008000020"
    check("control: with the POM bit in the mask the same row is clean", M.compare(ok, good_file(), resolve, lambda f: True) == [], codes(M.compare(ok, good_file(), resolve, lambda f: True)))
    row = good_row()
    row["found"] = False
    check("positive control: a material the engine cannot load is M001", codes(M.compare(row, good_file())) == ["M001"], "")
    row = good_row()
    row["is_default"] = True
    check("positive control: the engine's default material in place of the file is M001", codes(M.compare(row, good_file())) == ["M001"], "")
    row = good_row()
    row["subs"][0]["surface"] = "mat_default"
    check("positive control: SurfaceType wood in the file, default in the engine is M002", codes(M.compare(row, good_file())) == ["M002"], "")
    row = good_row()
    row["subs"][0]["shader"] = "NoShader"
    check("positive control: a different shader is M003", codes(M.compare(row, good_file())) == ["M003"], "")
    row = good_row()
    row["subs"][0]["textures"][1].update(bound_file="%ENGINE%/EngineAssets/Textures/White_ddn.dds", w=4, h=4)
    check("positive control: a placeholder in place of the normal map is M006, and not also M008 (one finding per cause)", codes(M.compare(row, good_file(), None, lambda f: False)) == ["M006", "M008"] and sum(1 for f in M.compare(row, good_file(), None, lambda f: False) if "ddna" in f["message"]) == 1, codes(M.compare(row, good_file(), None, lambda f: False)))
    row = good_row()
    row["subs"][0]["textures"][0]["bound"] = False
    check("positive control: a texture the engine did not bind is an error (M006)", [f["severity"] for f in M.compare(row, good_file()) if f["code"] == "M006"] == ["error"], "")
    check("positive control: a texture file that is not in the project is M008 (a streamed texture keeps a stand-in of its own name)", codes(M.compare(good_row(), good_file(), None, lambda f: "diff" not in f)) == ["M008"], "")
    # a UI element drawn as a texture ("Board.ui"): the engine binds nothing at load time, so no M006 and no M008 while the element exists; an unknown element is M009
    ui_file = good_file()
    ui_file[0]["textures"] = ["materials/x_diff.tif", "Board.ui"]
    ui_row = good_row()
    ui_row["subs"][0]["textures"] = ui_row["subs"][0]["textures"][:1] + [{"slot": "normals", "file": "Board.ui", "bound": False, "bound_file": "", "w": 0, "h": 0, "loaded": False}]
    check("control: a .ui texture whose UI element exists gives no M006 and no M008", not ({"M006", "M008", "M009"} & set(codes(M.compare(ui_row, ui_file, None, lambda f: True)))), codes(M.compare(ui_row, ui_file, None, lambda f: True)))
    check("positive control: a .ui texture whose UI element is not defined is M009", "M009" in codes(M.compare(ui_row, ui_file, None, lambda f: not f.endswith(".ui"))), codes(M.compare(ui_row, ui_file, None, lambda f: not f.endswith(".ui"))))
    row = good_row()
    row["subs"] = []
    check("positive control: a sub-material the engine lacks is M005", "M005" in codes(M.compare(row, good_file())), "")
    row = good_row()
    row["log"] = ["$6Warning: something broke", "ordinary line"]
    check("positive control: a load warning is kept (M007) and an ordinary line is not", [f["message"].split("   (")[0] for f in M.compare(row, good_file()) if f["code"] == "M007"] == ["Warning: something broke"], "")
    row = good_row()
    row["log"] = ['$6[Warning] Failed to load shader "Foo" in material "materials/x"\n']
    check("positive control: the engine's 'Failed to load shader' line is M003 (an error, naming the shader), not a generic M007", codes(M.compare(row, good_file())) == ["M003"] and "Foo" in M.compare(row, good_file())[0]["message"], codes(M.compare(row, good_file())))
    check("a texture is told from its other spelling: .tif and .dds, case, a leading ./", M._tex_key("Materials\\X_Diff.TIF") == M._tex_key("materials/x_diff.dds") == M._tex_key("./materials/x_diff.dds"), "")
    check("the names of the bits set in a mask come from the table", M.flag_names(0x4000008000020, TABLE) == ["NORMAL_MAP", "PARALLAX_OCCLUSION_MAPPING", "SUBSURFACE_SCATTERING"], "")
    # The checks below pin THIS author's materials (materials/ashvale/world.mtl, cloth.mtl, people/bandit.mtl ...); on another project they are skipped, not failed.
    ashvale = all(os.path.isfile(os.path.join(M.ASSETS, "materials", "ashvale", f)) for f in ("world.mtl", "cloth.mtl", os.path.join("people", "bandit.mtl")))
    if ashvale:
        subs = M.file_subs("materials/ashvale/world.mtl")
        check("the file reader returns the 11 sub-materials of world.mtl with their surface types and textures", len(subs) == 11 and subs[7]["name"] == "dirtroad" and subs[7]["surface"] == "mat_soil" and subs[7]["textures"], [(s["name"], s["surface"]) for s in subs][:3])
    else:
        print("  --   SKIPPED the Ashvale-specific material checks: Assets/materials/ashvale/{world,cloth}.mtl are not in this project")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live and not ashvale:
        live = False
        print("  --   SKIPPED the live material checks: they pin the Ashvale project's materials")
    if live:
        mats = M.project_materials()
        r = M.check()
        found = [m for m in r["materials"] if m["found"]]
        check("the engine loads every .mtl of the project (97 since the three broken GameSDK imports were deleted 2026-10-09), all of them as real materials", len(mats) >= 90 and len(found) == len(mats), (len(found), len(mats)))
        own = [f for f in r["findings"] if f["where"].startswith("materials/") and f["severity"] != "info"]
        check("the project's own materials (Assets/materials) have no finding at all", not own, [(f["code"], f["where"]) for f in own][:3])
        bad = [f for f in r["findings"] if f["code"] in ("M001", "M002", "M003", "M005")]
        check("no material anywhere in Assets has an undefined surface type, a wrong shader, a missing sub-material or fails to load", not bad, [(f["code"], f["where"]) for f in bad][:3])
        by = {m["file"]: m for m in r["materials"]}
        check("the surface types resolve to what the file says now that SurfaceTypes.xml defines them: cloth -> mat_fabric (remapped), the skins -> mat_flesh, the GameSDK rock -> mat_rock",
              by["materials/ashvale/cloth.mtl"]["surfaces"] == ["mat_fabric"] and "mat_flesh" in by["materials/ashvale/people/bandit.mtl"]["surfaces"] and any("mat_rock" in m["surfaces"] for m in r["materials"]),
              (by["materials/ashvale/cloth.mtl"]["surfaces"], by["materials/ashvale/people/bandit.mtl"]["surfaces"]))
        check("the road's sub-material world.mtl [dirtroad] is mat_soil (remapped from the undefined mat_dirt)", "mat_soil" in by["materials/ashvale/world.mtl"]["surfaces"], by["materials/ashvale/world.mtl"]["surfaces"])
        dropped = [f for f in r["findings"] if f["code"] == "M004" and "head_a" in f["where"]]
        check("positive control, from the real project: the villager's Eye and Hair materials name flags the shader drops (%ENVIRONMENT_MAP, %HAIR_AMBIENT; M004)", len(dropped) == 2, [f["message"][:40] for f in dropped])
        gone = [f for f in r["findings"] if f["code"] in ("M006", "M008", "M001", "M002", "M003", "M005")]
        check("no material of the project has a missing texture, an undefined surface type, a wrong shader or fails to load (the three GameSDK imports without textures were deleted 2026-10-09)", not gone and r["ok"], sorted({f["where"] for f in gone})[:3])

        stamp = "%d_%d" % (os.getpid(), int(time.time()))
        src = open(os.path.join(M.ASSETS, "materials", "ashvale", "cloth.mtl"), encoding="utf-8").read()
        made = []

        def variant(tag, edit):
            text = edit(src)
            rel = "materials/zz_mcp_%s_%s.mtl" % (stamp, tag)
            path = os.path.join(M.ASSETS, rel.replace("/", os.sep))
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            made.append(path)
            return rel

        def raw(rel):
            names, out = os.path.join(M.SCRATCH, "t_names.txt"), os.path.join(M.SCRATCH, "t_out.json")
            open(names, "w").write(rel + "\n")
            bridge_py.send("bridge.material.check %s | %s" % (out.replace(chr(92), "/"), names.replace(chr(92), "/")), 60)
            with open(out, encoding="utf-8") as f:
                return json.load(f)[0]
        try:
            first_tex = re.search(r'<Texture Map="Bumpmap" File="([^"]+)"', src)
            rels = {
                "copy": variant("copy", lambda t: t),
                "surface": variant("surface", lambda t: t.replace('SurfaceType="mat_fabric"', 'SurfaceType="mat_zz_undefined"')),
                "shader": variant("shader", lambda t: t.replace('Shader="Illum"', 'Shader="ZzNoSuchShader"', 1)),
                "nonormal": variant("nonormal", lambda t: t.replace(first_tex.group(1), "materials/zz_mcp_no_such_ddn.tif")) if first_tex else None,
                "nodiffuse": variant("nodiffuse", lambda t: re.sub(r'(<Texture Map="Diffuse" File=")[^"]+', r"\1materials/zz_mcp_no_such_diff.tif", t, count=1)),
                "broken": variant("broken", lambda t: "<NotAMaterial"),
                "m_empty": variant("m_empty", lambda t: re.sub(r'StringGenMask="[^"]*"', 'StringGenMask=""', t, count=1)),
                "m_bogus": variant("m_bogus", lambda t: re.sub(r'StringGenMask="[^"]*"', 'StringGenMask="%NOTAREALFLAG%NORMAL_MAP"', t, count=1)),
                "m_pom": variant("m_pom", lambda t: re.sub(r'StringGenMask="[^"]*"', 'StringGenMask="%PARALLAX_OCCLUSION_MAPPING"', t, count=1)),
                "m_numeric": variant("m_numeric", lambda t: re.sub(r'\sStringGenMask="[^"]*"', "", t, count=1).replace('GenMask="80000081"', 'GenMask="80000000"', 1)),
            }
            time.sleep(1.5)
            res = M.check([v for v in rels.values() if v])
            fs = res["findings"]

            def of(tag):
                return [f for f in fs if rels[tag] and rels[tag] in f["where"]]
            check("control: an unchanged copy of cloth.mtl loads as a material with surface mat_fabric and shader Illum and no finding", not of("copy") and next(m for m in res["materials"] if m["file"] == rels["copy"])["surfaces"] == ["mat_fabric"], codes(of("copy")))
            row = raw(rels["surface"])
            check("positive control: an undefined SurfaceType resolves to mat_default in the engine, and the check says so (M002)", row["subs"][0]["surface"] == "mat_default" and "M002" in codes(of("surface")), (row["subs"][0]["surface"], codes(of("surface"))))
            check("positive control: an unknown shader name is M003, from the engine's own 'Failed to load shader' line (the engine keeps the NAME, so the comparison alone cannot see it)", "M003" in codes(of("shader")) and "ZzNoSuchShader" in " ".join(f["message"] for f in of("shader")), codes(of("shader")))
            check("positive control: a missing normal map is bound to the engine's White_ddn placeholder and reported (M006)", "M006" in codes(of("nonormal")), codes(of("nonormal")))
            check("positive control: a missing diffuse map is NOT swapped by name (a stand-in of the asked-for name is kept) and only the file shows it (M008); nothing else is wrong with the material",
                  "M008" in codes(of("nodiffuse")) and not ({"M001", "M002", "M003", "M005", "M006"} & set(codes(of("nodiffuse")))), codes(of("nodiffuse")))
            check("positive control: a file that is not a material is M001", "M001" in codes(of("broken")), codes(of("broken")))
            masks = {tag: int(raw(rels[tag])["subs"][0]["gen_mask"], 16) for tag in ("m_empty", "m_bogus", "m_pom", "m_numeric", "copy")}
            check("measured: Illum's mask carries NORMAL_MAP (0x20, from the normal-map texture) and SUBSURFACE_SCATTERING (0x4000000000000) whatever the string says: '', '%NOTAREALFLAG%NORMAL_MAP' and the original all give the same",
                  masks["m_empty"] == masks["m_bogus"] == masks["copy"] == 0x4000000000020, {k: hex(v) for k, v in masks.items()})
            check("measured: a flag that does change the mask is honoured: %PARALLAX_OCCLUSION_MAPPING adds exactly its bit 0x8000000", masks["m_pom"] == masks["m_empty"] | 0x8000000, hex(masks["m_pom"]))
            check("measured: the numeric GenMask counts only when StringGenMask is absent (GenMask 80000000 adds exactly 0x80000000, as it is); with an (even empty) string, as in the original, it is ignored",
                  masks["m_numeric"] == masks["m_empty"] | 0x80000000 and masks["copy"] == masks["m_empty"], hex(masks["m_numeric"]))
        finally:
            time.sleep(2)
            for p in made + glob.glob(os.path.join(M.ASSETS, "materials", "zz_mcp_*")):
                if os.path.exists(p):
                    os.remove(p)
        left = glob.glob(os.path.join(M.ASSETS, "materials", "zz_mcp_*"))
        check("the project is as it was: no test material and no .cryasset is left", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
