#!/usr/bin/env python3
"""Self-test of pbs_check.py: the Physically Based Shading rules on synthetic materials with positive controls, then the project's own materials (editor-free, about 1 s).

    test_pbs_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import pbs_check as P

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings):
    return sorted({f["code"] for f in findings})


def mat(**kw):
    f = {"name": "m", "shader": "Illum", "diffuse": [1.0, 1.0, 1.0], "specular": [0.0212, 0.0212, 0.0212], "shininess": 255.0, "surface": "mat_default", "textures": {"diffuse": "a_diff.tif", "bumpmap": "a_ddna.tif"}}
    f.update(kw)
    return f


def main():
    check("sRGB conversion: linear 0.0212 is sRGB 40, 0.0452 is 60, 0.0802 is 80, 0.5 is 188", [P.srgb(0.0212), P.srgb(0.0452), P.srgb(0.0802), P.srgb(0.5)] == [40, 60, 80, 188], [P.srgb(0.0212), P.srgb(0.0452), P.srgb(0.0802), P.srgb(0.5)])
    check("control: a textured non-metal with Diffuse 1,1,1, Specular sRGB 40 and Shininess 255 has no finding", P.check_facts(mat(), "m") == [], codes(P.check_facts(mat(), "m")))
    r = P.check_facts(mat(diffuse=[1.7, 1.6, 1.5]), "m")
    check("positive control: a Diffuse channel above 1.0 is S001, an error", [f["severity"] for f in r if f["code"] == "S001"] == ["error"], codes(r))
    r = P.check_facts(mat(diffuse=[0.5, 0.5, 0.5]), "m")
    check("positive control: a tinted Diffuse on a textured material is S002, information", [(f["code"], f["severity"]) for f in r] == [("S002", "info")], codes(r))
    check("an untextured material with a colour is not judged by S002 (the colour IS its albedo)", P.check_facts(mat(diffuse=[0.5, 0.4, 0.3], textures={"diffuse": "x.dds"}), "m") == [], "")
    r = P.check_facts(mat(specular=[0.2, 0.2, 0.2]), "m")
    check("positive control: a non-metal Specular above sRGB 80 is S003, an error ('never higher than 80')", [f["severity"] for f in r if f["code"] == "S003"] == ["error"] and not [f for f in r if f["code"] == "S005"], codes(r))
    r = P.check_facts(mat(specular=[0.06, 0.06, 0.06]), "m")
    check("positive control: a non-metal Specular at sRGB 70 is S003, a warning (outside 40..60, under 80)", [f["severity"] for f in r if f["code"] == "S003"] == ["warning"], [f["severity"] for f in r])
    r = P.check_facts(mat(specular=[0.005, 0.005, 0.005]), "m")
    check("a very dark Specular (below water's 38) is information only", [f["severity"] for f in r if f["code"] == "S003"] == ["info"], [f["severity"] for f in r])
    r = P.check_facts(mat(specular=[0.02, 0.025, 0.04]), "m")
    check("positive control: a coloured Specular on a non-metal is S007", "S007" in codes(r), codes(r))
    r = P.check_facts(mat(textures={"diffuse": "a.tif", "bumpmap": "a_ddna.tif", "specular": "a_spec.tif"}, specular=[0.1, 0.1, 0.1]), "m")
    check("positive control: a specular map with a Specular colour that is not white is S004 (and S003 is not asked)", codes(r) == ["S004"], codes(r))
    check("a specular map with a white Specular colour is fine", P.check_facts(mat(textures={"diffuse": "a.tif", "bumpmap": "a_ddna.tif", "specular": "a_spec.tif"}, specular=[1.0, 1.0, 1.0]), "m") == [], "")
    r = P.check_facts(mat(name="gold", diffuse=[0.8, 0.55, 0.1], specular=[0.9, 0.7, 0.2]), "m")
    check("positive control: a metal (by name) with a bright Diffuse is S005", "S005" in codes(r) and any("black or very dark" in f["message"] for f in r), codes(r))
    r = P.check_facts(mat(surface="mat_metal", diffuse=[0.02, 0.02, 0.02], specular=[0.15, 0.15, 0.15]), "m")
    check("positive control: a metal (by surface type) with Specular below sRGB 180 is S005", any("below 180" in f["message"] or "above 180" in f["message"] for f in r if f["code"] == "S005"), codes(r))
    check("control: a dark-diffuse metal with a bright Specular has no finding", P.check_facts(mat(surface="mat_metal", diffuse=[0.02, 0.02, 0.02], specular=[0.9, 0.9, 0.9]), "m") == [], "")
    r = P.check_facts(mat(shininess=50.0), "m")
    check("positive control: Shininess 50 with a normal map is S006 and says the map is scaled to 20%", any(f["code"] == "S006" and "20%" in f["message"] for f in r), [f["message"][-40:] for f in r])
    check("Shininess below 255 WITHOUT a normal map is not S006 (the slider is then the only smoothness)", "S006" not in codes(P.check_facts(mat(shininess=50.0, textures={"diffuse": "x.dds"}), "m")), "")
    check("other shaders are not judged (terrain, vegetation, skin, glass, water have their own conventions)", all(P.check_facts(mat(shader=s, diffuse=[2, 2, 2], specular=[0.9, 0.9, 0.9]), "m") == [] for s in ("Terrain", "Vegetation", "HumanSkin", "Glass", "Watervolume")), "")

    if not os.path.isfile(os.path.join(P.ASSETS, "materials", "ashvale", "gold.mtl")):
        print("  SKIP: the real-project checks pin the Ashvale example project's materials (materials/ashvale/gold.mtl is absent in the active project); the synthetic checks above ran")
    else:
        res = P.check()
        by = {}
        for f in res["findings"]:
            by.setdefault(f["code"], []).append(f)
        check("the project's own materials are read: at least 100 (sub-)materials (the exact count moves with every material the project gains or loses, so it is not pinned)", res["checked"] >= 100, res["checked"])
        check("no material has an error (the one error that used to be here, cloth_pale's Diffuse 1.7,1.6,1.5, is gone with the procedural cloth; every tint is at most 1.0)", [f["where"] for f in res["findings"] if f["severity"] == "error"] == [], [f["where"] for f in res["findings"] if f["severity"] == "error"])
        check("the metals found are the two old flat-colour ones: gold.mtl (bright Diffuse) and the arrow head (Diffuse 0.45); the sword's steel, the dungeon's iron and gold and the hatch's iron are PBS metals now",
              {f["where"] for f in by.get("S005", [])} == {"materials/ashvale/gold.mtl", "materials/ashvale/weapons.mtl [arrow_steel]"}, sorted({f["where"] for f in by.get("S005", [])}))
        check("the villagers' jacket/jeans/buttons use a specular map with Specular sRGB 97 (S004): 9 characters x 3 sub-materials", len(by.get("S004", [])) == 27, len(by.get("S004", [])))
        check("every textured world material has its smoothness map scaled by a Shininess of 50..160 (S006)", len(by.get("S006", [])) >= 20 and all("Shininess" in f["message"] for f in by["S006"]), len(by.get("S006", [])))
        imports = P.check(include_imports=True)
        check("with the GameSDK imports included more is checked (the default stays the project's own)", imports["checked"] > res["checked"], imports["checked"])

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
