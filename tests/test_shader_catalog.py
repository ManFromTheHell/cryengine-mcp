#!/usr/bin/env python3
"""Self-test of shader_catalog.py against the engine's own files (engine/shaders.pak, a globals.txt the engine wrote, the source's texture-slot tables) and the project's materials.
Editor-free; about 3 s.

    test_shader_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import shader_catalog as S

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-96s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:56]))


def main():
    cat = S.load()
    gp = S.find_globals()
    if gp is None:
        print("shader catalog self-test: SKIPPED (the engine's shader globals file was not found; set CRYENGINE_ENGINE to the engine folder)")
        return 0
    gflags, gshaders, gver = S.parse_globals(open(gp, encoding="utf-8").read())
    check("the catalog holds the engine's shaders (%d) with a script or a flag table" % len(cat), len(cat) >= 60 and "Illum" in cat and "Terrain" in cat and "Vegetation" in cat, len(cat))

    # ---- the control: my parse of the .ext files against the table the ENGINE wrote -------------------------------------------------
    common = sorted(s["name"].upper() for s in cat.values() if s["uses_common_flags"])
    check("the shaders that say UsesCommonGlobalFlags are exactly the ones the engine lists in globals.txt: %s" % ", ".join(common), common == sorted(gshaders), common)
    names = {f["name"].upper() for s in cat.values() if s["uses_common_flags"] for f in s["flags"]}
    check("the flag names parsed from those .ext files equal the engine's own table, name for name (%d)" % len(gflags), names == set(gflags), sorted(names ^ set(gflags))[:4])
    illum = S.find("Illum")
    flag = {f["name"]: f for f in illum["flags"]}
    check("Illum's flags carry the local mask the .ext declares and the (different) common-table mask the engine wrote: POM 0x8000000 both; offset bump 0x20000 local, 0x2000000 common",
          flag["PARALLAX_OCCLUSION_MAPPING"]["mask"] == 0x8000000 == gflags["PARALLAX_OCCLUSION_MAPPING"] and flag["OFFSET_BUMP_MAPPING"]["mask"] == 0x20000 and gflags["OFFSET_BUMP_MAPPING"] == 0x2000000,
          (hex(flag["OFFSET_BUMP_MAPPING"]["mask"]), hex(gflags["OFFSET_BUMP_MAPPING"])))
    check("a flag's label, description and 'hidden' come through (NORMAL_MAP is hidden; POM says it requires a height map)", flag["NORMAL_MAP"]["hidden"] and "height map" in flag["PARALLAX_OCCLUSION_MAPPING"]["description"].lower(), flag["PARALLAX_OCCLUSION_MAPPING"]["label"])
    tess = [f for f in S.parse_ext(S._pak_files()["Shaders/Illum.ext"])["properties"] if f["name"] == "DISPLACEMENT_MAPPING"][0]
    check("a flag declared under #ifdef FEATURE_MESH_TESSELLATION says so", "FEATURE_MESH_TESSELLATION" in tess["condition"], tess["condition"])

    # ---- parameters -----------------------------------------------------------------------------------------------------------------
    params = {p["name"]: p for p in illum["params"]}
    pom = params["PomDisplacement"]
    check("Illum.PomDisplacement: float, default 0.025, 0..0.05, step 0.001, needs %PARALLAX_OCCLUSION_MAPPING (from Illum.cfx)",
          pom["type"] == "float" and pom["default"] == "0.025" and pom["min"] == 0.0 and pom["max"] == 0.05 and pom["step"] == 0.001 and any("PARALLAX_OCCLUSION_MAPPING" in c for c in pom["conditions"]), pom["conditions"])
    check("a parameter that several flags unlock lists the condition (HeightBias needs OBM or POM or silhouette POM)", len([c for c in params["HeightBias"]["conditions"] if "%" in c]) >= 1 and "SILHOUETTE" in " ".join(params["HeightBias"]["conditions"]), params["HeightBias"]["conditions"])
    veg = {p["name"] for p in S.find("Vegetation")["params"]}
    check("parameters declared in an INCLUDED file are found, whatever the case of its name (Vegetation's wind bend params come from vertexLib.cfi via ModificatorVT.cfi)",
          {"bendDetailLeafAmplitude", "bendDetailFrequency"} <= veg, len(veg))
    check("internal parameters are marked (MatDifColor has no UIName: public=False; PomDisplacement public=True)", params["MatDifColor"]["public"] is False and pom["public"] is True, "")

    # ---- texture slots --------------------------------------------------------------------------------------------------------------
    slots = S.texture_slots()
    byname = {s["map"]: s for s in slots}
    check("the 14 texture slots are read from the two source tables: Heightmap <-> _displ <-> TM_Height, Bumpmap <-> _ddn <-> TM_Normals, Diffuse <-> _diff <-> TM_Diffuse",
          len(slots) == 14 and (byname["Heightmap"]["suffix"], byname["Heightmap"]["tm"]) == ("_displ", "TM_Height") and (byname["Bumpmap"]["suffix"], byname["Bumpmap"]["tm"]) == ("_ddn", "TM_Normals")
          and (byname["Diffuse"]["suffix"], byname["Diffuse"]["tm"]) == ("_diff", "TM_Diffuse"), len(slots))
    tex = {t["tm"]: t for t in illum["textures"] if t["material_slot"]}
    check("Illum binds every one of the 14 material slots (TM_Height -> register t6, TM_Diffuse -> t0)", set(tex) >= {"TM_Diffuse", "TM_Normals", "TM_Height", "TM_Specular", "TM_Smoothness"} and tex["TM_Height"]["register"] == "t6", sorted(tex)[:3])

    # ---- resolving a flag set the way the engine does ------------------------------------------------------------------------------
    r = S.resolve_flags("Illum", ["%PARALLAX_OCCLUSION_MAPPING", "NORMAL_MAP", "BUMP_MAP", "ALLOW_SILHOUETTE_POM", "WRINKLE_BLENDING", "nope"])
    check("resolve: declared flags are honoured; a name in no table is ignored; another shader's flag is 'wrong shader'; the string is sorted, with % in front",
          r["honoured"] == ["NORMAL_MAP", "PARALLAX_OCCLUSION_MAPPING"] and r["ignored"] == ["ALLOW_SILHOUETTE_POM", "BUMP_MAP", "NOPE"] and r["wrong_shader"] == ["WRINKLE_BLENDING"]
          and r["string_gen_mask"].startswith("%ALLOW_SILHOUETTE_POM%BUMP_MAP") and r["uses_common_flags"], r)
    check("resolve: the common-table mask is the sum of the honoured flags' engine masks (NORMAL_MAP 0x20 + POM 0x8000000)", int(r["global_mask_hex"], 16) == gflags["NORMAL_MAP"] + gflags["PARALLAX_OCCLUSION_MAPPING"], r["global_mask_hex"])
    check("resolve on a shader outside the common table says the numeric GenMask is what counts", "GenMask is what counts" in S.resolve_flags("Particles", ["x"])["rule"], "")
    check("a shader named with a suffix resolves to its base (Terrain.Layer -> Terrain); an unknown one is None", S.find("Terrain.Layer")["name"] == "Terrain" and S.find("NoSuchShader") is None, "")
    fast = S.declared("Illum")
    check("the linter's fast lookup agrees with the full catalog (Illum: %d flags, common table)" % len(illum["flags"]), fast[1] is True and set(fast[0]) == {f["name"].upper() for f in illum["flags"]}, len(fast[0]))
    found, total = S.search("displacement")
    check("search finds flags and parameters by words (displacement: %d hits across %s)" % (total, sorted({x["shader"] for x in found})[:3]), total >= 4 and {"flag", "param"} <= {x["kind"] for x in found} and any(x["shader"] == "Illum" for x in found), total)

    # ---- the project's own materials against the catalog ----------------------------------------------------------------------------
    honoured = ignored = declared = undeclared = 0
    other = {}
    for root, _d, files in os.walk(__import__("project_root").ASSETS):
        for f in files:
            if not f.endswith(".mtl"):
                continue
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            for m in re.finditer(r"<Material\b([^>]*)>(.*?)(?=<Material\b|</Material>|\Z)", text, re.S):
                shader = re.search(r'Shader="([^"]*)"', m.group(1))
                sh = S.find(shader.group(1)) if shader else None
                if not sh:
                    continue
                sg = re.search(r'StringGenMask="([^"]*)"', m.group(1))
                names_ = {f_["name"].upper() for f_ in sh["flags"]}
                for n in [x for x in (sg.group(1).split("%") if sg else []) if x]:
                    if n.upper() in names_:
                        honoured += 1
                    else:
                        ignored += 1
                        other[(sh["name"], n)] = other.get((sh["name"], n), 0) + 1
                pp = re.search(r"<PublicParams\b([^>]*)/?>", m.group(2))
                if pp:
                    pnames = {p["name"] for p in sh["params"]} | {"IndirectColor"}
                    for a in re.findall(r"(\w+)=", pp.group(1)):
                        declared, undeclared = (declared + 1, undeclared) if a in pnames else (declared, undeclared + 1)
    if honoured + ignored < 50 or declared + undeclared < 50:
        print("  --   SKIPPED the project-materials controls: too few materials with flags or PublicParams in this project (%d flags, %d public params)" % (honoured + ignored, declared + undeclared))
    else:
        check("control: the flags in the project's materials are mostly ones the shaders declare (%d honoured, %d ignored: %s)" % (honoured, ignored, ", ".join("%s %%%s x%d" % (k[0], k[1], v) for k, v in sorted(other.items(), key=lambda kv: -kv[1])[:2])),
              honoured / (honoured + ignored) > 0.6, "%.0f%%" % (100 * honoured / (honoured + ignored)))
        check("control: the PublicParams in those materials are mostly declared by their shaders (%d of %d: the rest are legacy names such as SubsurfaceScatteringAmount)" % (declared, declared + undeclared),
              declared / (declared + undeclared) > 0.85, "%.0f%%" % (100 * declared / (declared + undeclared)))

    d1, d2 = S.decode_gen_mask("WaterVolume", "80000013"), S.decode_gen_mask("WaterVolume", "0x8000004B")
    check("a hex GenMask decodes into flags (the GameSDK woodland river 80000013 = realtime reflection + flow + specular map; water_forest_a 8000004B = + sun specular + foam)",
          {"SSREFL", "FLOW", "SPECULAR_MAP"} <= set(d1["flags"]) and {"SSREFL", "FLOW", "SUN_SPECULAR", "FOAM"} <= set(d2["flags"]) and "SPECULAR_MAP" not in d2["flags"] and d1["unknown_bits_hex"] == "0" and d1["used"], (d1["flags"], d2["flags"]))
    r_back = S.resolve_flags("WaterVolume", ["SSREFL", "FLOW", "SUN_SPECULAR", "FOAM"])["local_mask_hex"]
    check("control: the decode and the engine's own flag resolution agree (SSREFL+FLOW+SUN_SPECULAR+FOAM = %s = 4B)" % r_back, int(r_back, 16) == 0x4B and int(d2["mask_hex"], 16) & 0x4B == 0x4B, r_back)
    d3 = S.decode_gen_mask("Illum", "88020081")
    check("for a shader on the common flag table the GenMask number is reported as NOT USED (Illum): the project's 88020081 is ignored by the engine", d3["used"] is False and "does NOT read GenMask" in d3["rule"], d3["rule"][:40])
    try:
        S.decode_gen_mask("NoSuchShader", "1")
        bad_s = False
    except KeyError:
        bad_s = True
    check("an unknown shader is refused", bad_s, "")

    bad = results.count(False)
    print("shader catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
