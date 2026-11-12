#!/usr/bin/env python3
"""Map everything a CRYENGINE project ships: which pak holds what, how big each content
area is, and which data libraries exist (items, vehicles, particles, materials, UI, game
rules, prefabs...). Offline, standard library + crypak.py.

    project_inventory.py <asset dir>                  markdown overview
    project_inventory.py <asset dir> --depth 3        finer folder breakdown
    project_inventory.py <asset dir> --list REGEX     every file whose path matches (packed and loose)
    project_inventory.py <asset dir> --json

<asset dir> is the folder the .cryproject lists under content.assets (GameSDK: "gamesdk").
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypak  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Content areas worth naming, matched against lower-case game paths.
AREAS = collections.OrderedDict([
    ("levels", r"^levels/"),
    ("ai scripts", r"^scripts/ai/"),
    ("entity scripts", r"^scripts/entities/"),
    ("game rules", r"^scripts/gamerules/"),
    ("other scripts", r"^scripts/"),
    ("items / weapons data", r"^(libs/items|scripts/entities/items|libs/equipmentpacks)"),
    ("vehicles data", r"^(scripts/entities/vehicles|libs/vehicles)"),
    ("archetypes", r"^libs/entityarchetypes/"),
    ("particles", r"^(libs/particles/|particles/)"),
    ("material effects", r"^libs/materialeffects/"),
    ("materials", r"^materials/"),
    ("ui / flash", r"^libs/ui/"),
    ("flowgraph libs", r"^libs/(flownodes|flowgraphmodules|actiongraphs)"),
    ("prefabs", r"^prefabs/"),
    ("damage / hit reactions", r"^libs/(bodydamage|hitdeathreactionsdata)"),
    ("config / input", r"^(libs/config|config/|difficulty/)"),
    ("dialog / DRS / localization", r"^(libs/dynamicresponsesystem|libs/localization|libs/communication|localization/)"),
    ("animations", r"^animations/"),
    ("geom caches", r"^(geomcaches/|objects/.*\.cax$)"),
    ("objects (meshes)", r"^objects/"),
    ("textures", r"^textures/"),
    ("audio", r"^audio/"),
    ("videos", r"^videos/"),
    ("other libs", r"^libs/"),
])


def classify(path):
    p = path.lower()
    for name, rx in AREAS.items():
        if re.search(rx, p):
            return name
    return "other"


def walk_loose(root):
    for d, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(d, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if f.lower().endswith(".pak"):
                continue
            yield rel, os.path.getsize(full)


def inventory(root, depth=2):
    root = os.path.abspath(root)
    paks = []
    areas = collections.defaultdict(lambda: {"files": 0, "bytes": 0, "paks": set()})
    folders = collections.defaultdict(lambda: {"files": 0, "bytes": 0})
    for pak in crypak.paks_under(root):
        es = crypak.entries(pak)
        rel_pak = os.path.relpath(pak, root).replace("\\", "/")
        size = os.path.getsize(pak)
        top = collections.Counter("/".join(e["name"].split("/")[:1]) for e in es)
        paks.append({"pak": rel_pak, "mb": round(size / 1048576, 1), "files": len(es), "top": dict(top.most_common(6))})
        # level.pak entries are relative to their level folder; prefix so they classify as levels
        prefix = "" if not rel_pak.lower().startswith("levels/") else os.path.dirname(rel_pak) + "/"
        for e in es:
            name = prefix + e["name"]
            a = areas[classify(name)]
            a["files"] += 1
            a["bytes"] += e["usize"]
            a["paks"].add(rel_pak if not prefix else "levels/*/*.pak")
            k = "/".join(name.lower().split("/")[:depth])
            folders[k]["files"] += 1
            folders[k]["bytes"] += e["usize"]
    loose = collections.defaultdict(lambda: {"files": 0, "bytes": 0})
    for rel, sz in walk_loose(root):
        a = areas[classify(rel)]
        a["files"] += 1
        a["bytes"] += sz
        a["paks"].add("(loose)")
        k = "/".join(rel.lower().split("/")[:depth])
        loose[k]["files"] += 1
        loose[k]["bytes"] += sz
    return {"root": root, "paks": paks,
            "areas": {k: {"files": v["files"], "mb": round(v["bytes"] / 1048576, 2), "where": sorted(v["paks"])} for k, v in areas.items()},
            "packed_folders": {k: v for k, v in sorted(folders.items())},
            "loose_folders": {k: v for k, v in sorted(loose.items())}}


def list_matching(root, regex):
    rx = re.compile(regex, re.I)
    out = []
    for e in crypak.find(root, regex):
        out.append((e["name"], e["usize"], os.path.basename(e["pak"])))
    for rel, sz in walk_loose(root):
        if rx.search(rel):
            out.append((rel, sz, "(loose)"))
    return sorted(out)


def markdown(inv):
    L = ["# Project inventory: %s" % inv["root"], "", "## Paks", ""]
    L.append("| pak | MB | files | top folders |")
    L.append("|---|---|---|---|")
    for p in sorted(inv["paks"], key=lambda p: -p["mb"]):
        if p["pak"].lower().startswith("levels/"):
            continue
        L.append("| %s | %s | %d | %s |" % (p["pak"], p["mb"], p["files"], ", ".join("%s %d" % kv for kv in p["top"].items())))
    lv = [p for p in inv["paks"] if p["pak"].lower().startswith("levels/")]
    if lv:
        L.append("| levels/**.pak (%d) | %.1f | %d | |" % (len(lv), sum(p["mb"] for p in lv), sum(p["files"] for p in lv)))
    L += ["", "## Content areas (uncompressed size)", "", "| area | files | MB | where |", "|---|---|---|---|"]
    for k, v in sorted(inv["areas"].items(), key=lambda kv: -kv[1]["mb"]):
        L.append("| %s | %d | %s | %s |" % (k, v["files"], v["mb"], ", ".join(v["where"])))
    L += ["", "## Packed folders", ""]
    for k, v in inv["packed_folders"].items():
        L.append("- `%s` %d files, %.1f MB" % (k, v["files"], v["bytes"] / 1048576))
    L += ["", "## Loose folders", ""]
    for k, v in inv["loose_folders"].items():
        L.append("- `%s` %d files, %.1f MB" % (k, v["files"], v["bytes"] / 1048576))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asset_dir")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--list")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.list:
        for name, sz, src in list_matching(a.asset_dir, a.list):
            print("%10d  %s  [%s]" % (sz, name, src))
        return
    inv = inventory(a.asset_dir, a.depth)
    print(json.dumps(inv, indent=1) if a.json else markdown(inv))


if __name__ == "__main__":
    main()
