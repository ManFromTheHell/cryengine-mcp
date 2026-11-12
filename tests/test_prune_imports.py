#!/usr/bin/env python3
"""Self-test of prune_imports.py and of import_gamesdk_asset's rule that an existing identical file is left alone, on a throwaway Assets tree: a file the baseline has is NEVER deleted (even if it
is brand new in time and no material names it), a new file no kept material names is, a new texture a kept material names (with its split parts) is not, and a dry run deletes nothing.

    test_prune_imports.py        # exit 1 on any failure; editor-free
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import prune_imports as P  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    tmp = tempfile.mkdtemp(prefix="zz_prune_")
    try:
        a = os.path.join(tmp, "Assets")
        def put(rel, data=b"x"):
            p = os.path.join(a, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(data)
            return p
        put("objects/natural/rocks/old_rock.mtl")                                    # in the baseline, and its mtime is NOW (as after a careless re-copy)
        put("textures/generic/brick/used_diff.dds"); put("textures/generic/brick/used_diff.dds.1"); put("textures/generic/brick/used_diff.dds.cryasset")
        put("textures/generic/brick/unused_diff.dds"); put("textures/generic/brick/unused_diff.dds.1")
        put("materials/generic/brick/preview_only.mtl")
        put("objects/architecture/bridges/bridge.mtl")                               # in the baseline
        put("levels/ashvale/level.pak"); put("materials/ashvale/mine.mtl", b"""<Material Shader="Illum"><Textures><Texture Map="Diffuse" File="textures/generic/brick/used_diff.tif"/></Textures></Material>""")
        baseline = {"project/Assets/objects/natural/rocks/old_rock.mtl", "project/Assets/objects/architecture/bridges/bridge.mtl"}
        found = {os.path.relpath(p, a).replace("\\", "/") for p in P.candidates(baseline, assets=a)}
        check("a file the baseline has is never a candidate, however new it is", "objects/natural/rocks/old_rock.mtl" not in found and "objects/architecture/bridges/bridge.mtl" not in found, sorted(found))
        check("a new texture a kept material names is kept, with its split parts and its .cryasset", not any(f.startswith("textures/generic/brick/used_diff") for f in found), sorted(found))
        check("a new texture and a new material that nothing names are candidates", {"textures/generic/brick/unused_diff.dds", "textures/generic/brick/unused_diff.dds.1", "materials/generic/brick/preview_only.mtl"} <= found, sorted(found))
        check("the project's own folders (levels, materials/ashvale) are never touched", not any(f.startswith(("levels/", "materials/ashvale/")) for f in found), sorted(found))
        # 2026-10-15: a kept material OUTSIDE the touched folders (materials/roads) lost its textures; and a material only a level layer names must be kept too
        put("materials/roads/new_road.mtl", b"""<Material Shader="Illum"><Textures><Texture Map="Diffuse" File="textures/roads/new_road_diff.tif"/></Textures></Material>""")
        put("textures/roads/new_road_diff.dds"); put("textures/roads/new_road_diff.dds.1"); put("textures/roads/old_preview_diff.dds")
        put("materials/generic/mud/layer_only.mtl", b"""<Material Shader="Illum"><Textures><Texture Map="Diffuse" File="textures/generic/mud/layer_only_diff.tif"/></Textures></Material>""")
        put("textures/generic/mud/layer_only_diff.dds"); put("textures/generic/mud/layer_only_diff.dds.1")
        put("levels/ashvale/layers/main.lyr", b"""<Objects><Object Type="Road" Name="R" Material="materials/generic/mud/layer_only"/></Objects>""")
        found_b = {os.path.relpath(p, a).replace("\\", "/") for p in P.candidates(baseline, assets=a)}
        check("a texture named by a material outside the touched folders (materials/roads) is kept, with its split part", not any(f.startswith("textures/roads/new_road_diff") for f in found_b), sorted(found_b))
        check("a preview texture that no kept material names is still a candidate", "textures/roads/old_preview_diff.dds" in found_b, sorted(found_b))
        check("a material that only a level layer names is kept, and so are its textures", not any("layer_only" in f for f in found_b), sorted(f for f in found_b if "layer_only" in f))
        os.utime(os.path.join(a, "objects", "natural", "rocks", "old_rock.mtl"), (time.time() + 5, time.time() + 5))
        found2 = {os.path.relpath(p, a).replace("\\", "/") for p in P.candidates(baseline, assets=a)}
        check("time plays no part: the same answer with a file's modified time in the future", found2 == found_b, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # the import tool leaves an identical existing file untouched (same bytes, same time)
    src = open(_paths.script("import_gamesdk_asset.py"), encoding="utf-8").read()
    check("import_gamesdk_asset.Paks.copy skips a file that already exists with the same size and counts it as untouched", 'self.untouched += 1' in src and 'os.path.getsize(out) == e["usize"]' in src, "")
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
