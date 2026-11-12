#!/usr/bin/env python3
"""Remove the files that were copied into Assets for PREVIEWING and that no project material uses: the SNAPSHOT is the ground truth, never a timestamp.

    python checks/prune_imports.py <snapshot id>              dry run: what would be deleted
    python checks/prune_imports.py <snapshot id> --really     delete it (a snapshot is taken first)

A file is deleted only if ALL hold: it is under Assets/{textures, materials/generic, materials/terrain, materials/decals, objects/natural, objects/architecture, objects/props/misc}, it was NOT
in the snapshot (so it did not exist before the preview imports), and no kept material names it (its split parts, .cryasset and 'a' parts go with its texture). The snapshot must have been
taken BEFORE the imports: `snapshot_project` first, then import, then prune.

Why: a clean-up that selects 'files newer than a cutoff' can delete files the level needs (a bridge, rocks, trees), because an import tool that rewrites files that already exist gives
them a fresh time. import_gamesdk_asset.py leaves an identical existing file untouched, and this tool ignores time altogether.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
sys.path.insert(0, HERE)
TOUCHED = ("textures", "materials/generic", "materials/terrain", "materials/decals", "objects/natural", "objects/architecture", "objects/props/misc")


def keeper_materials(assets=ASSETS):
    """The .mtl files that STAY, so the textures they name must stay: every material outside the touched folders (materials/ashvale, materials/roads, materials/wood_dark.mtl ...; the tool never deletes
    those, and a texture it deleted would leave them dangling) and every material a level layer names (a Brush, Road ... with Material="..."), wherever it lives. A keep-list limited to
    materials/ashvale would delete the textures of road materials given to the level later (materials/roads/*); the keep-list is therefore built from everything that stays."""
    keepers = set()
    for p in glob.glob(os.path.join(assets, "materials", "**", "*.mtl"), recursive=True):
        rel = os.path.relpath(p, assets).replace("\\", "/")
        if not rel.startswith(TOUCHED):
            keepers.add(p)
    for lyr in glob.glob(os.path.join(assets, "levels", "**", "*.lyr"), recursive=True):
        try:
            text = open(lyr, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in set(re.findall(r'\bMaterial="([^"]+)"', text)):
            rel = m.replace("\\", "/")
            p = os.path.join(assets, *(rel if rel.lower().endswith(".mtl") else rel + ".mtl").split("/"))
            if os.path.isfile(p):
                keepers.add(p)
    return sorted(keepers)


def kept_textures(assets=ASSETS, keepers=None):
    """The normalised paths of every texture file (all parts) that a kept material names. keepers: .mtl files (default: keeper_materials())."""
    if keepers is None:
        keepers = keeper_materials(assets)
    keep = set()
    for path in keepers:
        if not os.path.isfile(path):
            continue
        for t in ET.parse(path).getroot().iter("Texture"):
            f = (t.get("File") or "").replace("\\", "/")
            if f and not f.startswith("%"):
                base = os.path.join(assets, *re.sub(r"\.(tif|tga|png)$", ".dds", f).split("/"))
                keep.update(os.path.normcase(p) for p in glob.glob(base + "*"))
    return keep


def candidates(baseline_keys, assets=ASSETS, keepers=None):
    """-> [path]: files under the touched folders that the baseline did not have and no kept material names."""
    if keepers is None:
        keepers = keeper_materials(assets)
    keep = kept_textures(assets, keepers)
    keep.update(os.path.normcase(p) for p in keepers)                      # a kept material file is never a candidate itself
    keep.update(os.path.normcase(p + ".cryasset") for p in keepers)
    out = []
    for dp, _, files in os.walk(assets):
        rel_dir = os.path.relpath(dp, assets).replace("\\", "/")
        if not rel_dir.startswith(TOUCHED):
            continue
        for f in files:
            p = os.path.join(dp, f)
            key = "project/Assets/" + os.path.relpath(p, assets).replace("\\", "/")
            if key in baseline_keys or os.path.normcase(p) in keep:
                continue
            out.append(p)
    return out


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    import snapshot
    base = set(snapshot.load(sys.argv[1])["files"])
    doomed = candidates(base)
    size = sum(os.path.getsize(p) for p in doomed)
    print("snapshot %s has %d files; to delete: %d (%.0f MB)" % (sys.argv[1], len(base), len(doomed), size / 1e6))
    if "--really" in sys.argv:
        snapshot.snapshot("before_prune_imports", "taken automatically before deleting %d preview files" % len(doomed))
        for p in doomed:
            os.remove(p)
        for dp, _, _ in sorted(os.walk(ASSETS), key=lambda x: -len(x[0])):
            if not os.listdir(dp) and os.path.relpath(dp, ASSETS).replace("\\", "/").startswith(TOUCHED):
                os.rmdir(dp)
        print("deleted")
    else:
        for p in doomed[:8]:
            print("   ", os.path.relpath(p, ASSETS))


if __name__ == "__main__":
    main()
