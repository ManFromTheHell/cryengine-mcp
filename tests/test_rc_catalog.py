#!/usr/bin/env python3
"""Self-test of rc_catalog.py: the rc.ini parser and the help parser on synthetic text, then the real rc.ini and the real `rc.exe /help` (no editor needed): the presets the project's texture pipeline names
(Albedo, NormalsWithSmoothness, Displacement) exist with the formats the pipeline expects, the suffix masks pick them, and the options and converters RC lists are read. About 15 s.

    test_rc_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import rc_catalog as R

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


INI = """imagecompressor=CTsquish
[_platform]
name=pc
[_presetAliases]
Old=New
; a diffuse preset
; second line
[New]
pixelformat=BC1
filemasks=*_diff*;*_albedo*
[Other]
pixelformat=BC7
filemasks=*_other*
"""

HELP = """     Version 1.2.3.4  Jan  1 2000
     Registered ImageConverter ("tif", "hdr")
     Registered LuaCompiler ("lua")
     /animconfigfolder
          Path to a folder
     /preset
          [TIF] name of the preset
          to use for the image
     /zip_new
          Forces creation of new zip file

     Memory: working set 12.3Mb
"""


def main():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "rc.ini")
    open(p, "w").write(INI)
    ini = R.parse_ini(p)
    check("rc.ini sections: a preset keeps its options and the comment lines above it, the _platform and _presetAliases sections are not presets", sorted(ini["presets"]) == ["New", "Other"] and ini["presets"]["New"]["description"] == "a diffuse preset second line" and ini["aliases"] == {"Old": "New"} and ini["globals"] == {"imagecompressor": "CTsquish"}, ini["presets"]["New"])
    check("filemasks are matched without case and a mask list is split on ';' (x_ALBEDO.tif -> New; x_other.tif -> Other; x_nothing.tif -> none)", [h["preset"] for h in R.for_file("X_ALBEDO.tif", ini)] == ["New"] and [h["preset"] for h in R.for_file("x_other.tif", ini)] == ["Other"] and R.for_file("x_nothing.tif", ini) == [], "")
    check("an old preset name resolves to its current one through the alias table", R.resolve_alias("Old", ini) == "New" and R.resolve_alias("New", ini) == "New", "")
    h = R.parse_help(HELP)
    check("the help text is read: version, the converters with their extensions, options with their [TIF] tag and a description that spans lines", h["version"] == "1.2.3.4" and h["converters"] == [{"name": "ImageConverter", "extensions": ["tif", "hdr"]}, {"name": "LuaCompiler", "extensions": ["lua"]}]
          and {o["key"]: o["applies_to"] for o in h["options"]} == {"/animconfigfolder": [], "/preset": ["TIF"], "/zip_new": []} and "to use for the image" in [o for o in h["options"] if o["key"] == "/preset"][0]["description"], h["options"][:1])

    ini = R.parse_ini()
    check("the real rc.ini: 40+ presets, 30+ legacy aliases, the platforms, the global settings", len(ini["presets"]) >= 40 and len(ini["aliases"]) >= 30 and len(ini["platforms"]) >= 3 and "imagecompressor" in ini["globals"], (len(ini["presets"]), len(ini["aliases"])))
    pf = {p["name"]: p for p in R.presets(ini=ini)}
    check("the presets the project's texture pipeline names exist with the formats it relies on: Albedo BC1 sRGB, NormalsWithSmoothness BC5s + BC4 alpha, Displacement BC4 swizzle aaa1",
          pf["Albedo"]["pixelformat"] == "BC1" and "srgb" in pf["Albedo"]["colorspace"].lower() and pf["NormalsWithSmoothness"]["pixelformat"] == "BC5s" and pf["NormalsWithSmoothness"]["pixelformatalpha"] == "BC4"
          and pf["Displacement"]["pixelformat"] == "BC4" and pf["Displacement"]["options"].get("swizzle") == "aaa1", (pf["Albedo"]["pixelformat"], pf["Displacement"]["options"].get("swizzle")))
    check("the suffix conventions pick them: _diff -> Albedo first (then the alpha variants), _ddna -> NormalsWithSmoothness, _displ -> only Displacement, _bump -> NormalsFromDisplacement",
          [h["preset"] for h in R.for_file("a_diff.tif", ini)][0] == "Albedo" and "NormalsWithSmoothness" in [h["preset"] for h in R.for_file("a_ddna.tif", ini)] and [h["preset"] for h in R.for_file("a_displ.tif", ini)] == ["Displacement"]
          and [h["preset"] for h in R.for_file("a_bump.tif", ini)] == ["NormalsFromDisplacement"], [h["preset"] for h in R.for_file("a_ddna.tif", ini)])
    check("a legacy name the project might still have in a TIFF resolves: DisplacementMap -> Displacement, Normalmap_highQ -> Normals", R.resolve_alias("DisplacementMap", ini) == "Displacement" and R.resolve_alias("Normalmap_highQ", ini) == "Normals", "")
    found = R.presets("bc7")
    check("search finds presets by an option value (bc7: the alpha-blend albedo and others)", any(p["name"] == "AlbedoWithOpacity" for p in found), [p["name"] for p in found][:4])

    if os.path.isfile(R.RC_EXE):
        o = R.options(refresh=True)
        keys = {x["key"]: x for x in o["options"]}
        check("rc.exe /help: 150+ options, the converters RC registers (the texture, mesh, character, animation, material-metadata and pak compilers) and its version", len(o["options"]) >= 150 and o["version"].startswith("1.") and {"ImageConverter", "StatCGFCompiler", "CharacterCompiler", "AnimationConverter", "MetadataCompiler"} <= {c["name"] for c in o["converters"]}, (len(o["options"]), o["version"]))
        check("the options the pipeline passes are documented by RC itself: /preset and /refresh and /overwriteextension, and /preset is a [TIF] option", {"/preset", "/refresh", "/overwriteextension", "/targetroot", "/sourceroot"} <= set(keys) and "TIF" in keys["/preset"]["applies_to"], sorted(keys)[:3])
        check("the image converter reads tif and hdr, the mesh compiler cgf, the character compiler chr/cdf/skin", any(c["name"] == "ImageConverter" and c["extensions"] == ["tif", "hdr"] for c in o["converters"]) and any("cgf" in c["extensions"] for c in o["converters"] if c["name"] == "StatCGFCompiler")
              and any(c["extensions"] == ["chr", "cdf", "skin"] for c in o["converters"]), [c["name"] for c in o["converters"]][:5])
    else:
        print("  --   rc.exe is not at %s: the live checks are skipped" % R.RC_EXE)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
