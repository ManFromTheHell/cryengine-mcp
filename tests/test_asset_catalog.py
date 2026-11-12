#!/usr/bin/env python3
"""Self-test of asset_catalog.py: first on a throwaway tree whose answers are known, then on the real project's metadata. Editor-free; about 1 s.

    test_asset_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import asset_catalog as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-100s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


META = '<AssetMetadata version="0" type="%s" guid="%s"><Files>%s</Files><Details>%s</Details><Dependencies>%s</Dependencies></AssetMetadata>'


def put(root, rel, text):
    path = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)


def main():
    tmp = tempfile.mkdtemp(prefix="asset_catalog_test_")
    try:
        # a mesh with a LOD chain depending on a material (relative './'), the material depending on a texture's SOURCE name (.tif) and an engine texture; an unused material; a texture
        put(tmp, "objects/a/rock.cgf.cryasset", META % ("Mesh", "g-mesh", '<File path="rock.cgf"/><File path="rock_lod1.cgf"/>', '<Detail name="triangleCount">5000</Detail>', '<Path usageCount="1">./rock.mtl</Path>'))
        put(tmp, "objects/a/rock.mtl.cryasset", META % ("Material", "g-mtl", '<File path="rock.mtl"/>', "", '<Path usageCount="2">textures/rock_diff.tif</Path><Path>%ENGINE%/EngineAssets/TextureMsg/color_White.dds</Path>'))
        put(tmp, "objects/a/lonely.mtl.cryasset", META % ("Material", "g-lonely", '<File path="lonely.mtl"/>', "", ""))
        put(tmp, "textures/rock_diff.dds.cryasset", META % ("Texture", "g-tex", '<File path="rock_diff.dds"/>', '<Detail name="width">1024</Detail><Detail name="height">512</Detail>', ""))
        put(tmp, "objects/a/rock.cgf", "x")
        put(tmp, "objects/a/rock_lod1.cgf", "x")
        put(tmp, "objects/a/rock.mtl", "x")
        put(tmp, "objects/a/uncatalogued.cgf", "x")
        put(tmp, "textures/rock_diff.dds", "x")

        rows = A.scan(tmp)
        check("every .cryasset is read (4)", len(rows) == 4 and {r["type"] for r in rows} == {"Mesh", "Material", "Texture"}, len(rows))
        mesh = A.get("objects/a/rock.cgf", tmp)
        check("a mesh keeps its LOD chain as its files and the details the engine measured", mesh["files"] == ["objects/a/rock.cgf", "objects/a/rock_lod1.cgf"] and mesh["details"]["triangleCount"] == "5000", mesh["files"])
        check("a './x.mtl' dependency is resolved against the owner's folder", mesh["deps"][0]["path"] == "objects/a/rock.mtl", mesh["deps"])
        check("a LOD file finds its asset (get('rock_lod1.cgf')), and so does a GUID", A.get("objects/a/rock_lod1.cgf", tmp)["guid"] == "g-mesh" and A.get("G-MTL", tmp)["path"] == "objects/a/rock.mtl", "")
        check("who uses the material: the mesh (reverse index)", A.users("objects/a/rock.mtl", tmp) == ["objects/a/rock.cgf"], A.users("objects/a/rock.mtl", tmp))
        check("who uses the texture: the material, though it names the .tif source and the asset is the .dds", A.users("textures/rock_diff.dds", tmp) == ["objects/a/rock.mtl"], A.users("textures/rock_diff.dds", tmp))
        check("control: an asset nobody depends on has no users, and 'unused' lists exactly it (and the engine's own texture is not an asset here)",
              A.users("objects/a/lonely.mtl", tmp) == [] and A.unused("Material", tmp)[0] == ["objects/a/lonely.mtl"] and A.unused("Mesh", tmp)[0] == ["objects/a/rock.cgf"], A.unused("Material", tmp))
        check("heaviest: a mesh by triangles, a texture by pixels", A.heaviest("Mesh", 5, tmp)[0]["weight"] == 5000 and A.heaviest("Texture", 5, tmp)[0]["weight"] == 1024 * 512, "")
        cov = A.coverage(tmp)
        check("coverage: the LOD file counts as covered; a mesh no metadata lists is reported", cov["by_extension"][".cgf"] == {"with_metadata": 2, "without": 1} and cov["without_examples"] == ["objects/a/uncatalogued.cgf"], cov["by_extension"])
        check("a path nothing knows is None", A.get("objects/nothing.cgf", tmp) is None, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    import os as _os0
    if not _os0.path.isfile(_os0.path.join(A.ASSETS, "materials", "ashvale", "ash_broken.mtl")):
        print("SKIP: the real-project checks pin the Ashvale example project's assets (materials/ashvale/ash_broken.mtl is absent in the active project)")
    else:
        s = A.summary()
        check("the real project: the engine recorded %d assets, %d dependencies (types: %s)" % (s["assets_with_metadata"], s["dependencies_recorded"], ", ".join("%s %d" % kv for kv in list(s["by_type"].items())[:3])),
              s["assets_with_metadata"] > 300 and s["by_type"].get("Texture", 0) > 100 and s["by_type"].get("Mesh", 0) > 100, s["assets_with_metadata"])
        house = A.heaviest("Mesh", 3)
        check("the real project's heaviest mesh has an engine-measured triangle count above 10,000 (every mesh has metadata since asset_metadata, 2026-10-11)", bool(house) and house[0]["weight"] > 10000 and house[0]["files"] >= 1, "%s %s" % (house[0]["path"], house[0]["weight"]) if house else "")
        mtl = A.get("materials/ashvale/ash_broken.mtl")
        check("a real material's dependency on an ENGINE texture is kept as an engine path", mtl and mtl["deps"][0]["path"].startswith("%engine%/engineassets/"), mtl["deps"] if mtl else "")
        cov = A.coverage()
        import os as _os, re as _re
        gap = {".cgf": 0, ".dds": 0}
        for root, _dirs, files in _os.walk(A.ASSETS):
            low = {f.lower() for f in files}
            for f in files:
                e = _os.path.splitext(f)[1].lower()
                if e in gap and (f + ".cryasset").lower() not in low and not _re.search(r"_lod\d+\.cgf$", f, _re.I):
                    gap[e] += 1
        check("coverage on the real project says exactly how many .cgf and .dds files have no metadata (%d and %d, counted independently; asset_metadata makes them 0)" % (cov["by_extension"][".cgf"]["without"], cov["by_extension"][".dds"]["without"]),
              cov["by_extension"][".cgf"]["with_metadata"] > 100 and cov["by_extension"][".cgf"]["without"] == gap[".cgf"] and cov["by_extension"][".dds"]["without"] == gap[".dds"], gap)

    bad = results.count(False)
    print("asset catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
