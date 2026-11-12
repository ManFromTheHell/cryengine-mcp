#!/usr/bin/env python3
"""Offline inventory of a CRYENGINE level: what is in it and how its AI is wired.

Reads the level's layer files (*.lyr, plain XML) and level.pak directly, so it
works with Sandbox closed, on any project, and has no 200/500-object cap. Use it
to learn from a finished level before building one like it.

    level_inventory.py <level dir>                 markdown report
    level_inventory.py <level dir> --json          machine-readable
    level_inventory.py <level dir> --nav-points    also print every nav-area vertex in world space

<level dir> is the folder holding <name>.level and layers/, e.g.
    ".../CRYENGINE GameSDK Sample Project/gamesdk/levels/singleplayer/woodland"

Archetype GUIDs are resolved to names through Libs/EntityArchetypes/*.xml, found
loose or inside any .pak under the asset root (auto-detected as the folder that
contains "levels").
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
import glob
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypak  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Object types that matter for AI. Everything else is summarised by count only.
AI_TYPES = {"NavigationArea", "NavigationSeedPoint", "CoverSurface", "SmartObject",
            "TagPoint", "AIPoint", "AIAnchor", "AITerritory", "AIWave", "AIReinforcementSpot"}
# Entity properties that identify how an AI character is configured.
AI_PROPS = ("esFaction", "esModularBehaviorTree", "esBehaviorSelection", "equip_EquipmentPack",
            "esNavigationType", "fileModel", "health", "sightrange", "attackrange", "bSpawner")


def vec(s, n=3):
    try:
        v = [float(x) for x in (s or "").split(",")]
        return (v + [0.0] * n)[:n]
    except ValueError:
        return [0.0] * n


def rotate(q, v):
    """Rotate v by a CryEngine quaternion stored as w,x,y,z."""
    w, x, y, z = q
    tx = 2 * (y * v[2] - z * v[1])
    ty = 2 * (z * v[0] - x * v[2])
    tz = 2 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx)]


def yaw_deg(q):
    w, x, y, z = q
    return math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


def find_asset_root(level_dir):
    d = os.path.abspath(level_dir)
    while True:
        parent = os.path.dirname(d)
        if os.path.basename(d).lower() == "levels":
            return parent
        if parent == d:
            return None
        d = parent


def load_archetypes(asset_root):
    """{GUID(lower, no braces): (Library, Name, Class, props)}"""
    out = {}
    if not asset_root:
        return out
    names = set()
    for p in glob.glob(os.path.join(asset_root, "libs", "entityarchetypes", "*.xml")):
        names.add("Libs/EntityArchetypes/" + os.path.basename(p))
    for e in crypak.find(asset_root, r"^libs/entityarchetypes/[^/]+\.xml$"):
        names.add(e["name"])
    for rel in sorted(names):
        data = crypak.read_asset(asset_root, rel)
        if not data:
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        for p in root.iter("EntityPrototype"):
            guid = (p.get("Id") or "").strip("{}").lower()
            props = p.find("Properties")
            pv = {k: props.get(k) for k in AI_PROPS if props is not None and props.get(k) is not None}
            if props is not None:
                dmg = props.find("Damage")
                if dmg is not None and dmg.get("health"):
                    pv["health"] = dmg.get("health")
                per = props.find("Perception")
                if per is not None and per.get("sightrange"):
                    pv["sightrange"] = per.get("sightrange")
                rod = props.find("RateOfDeath")
                if rod is not None and rod.get("attackrange"):
                    pv["attackrange"] = rod.get("attackrange")
            out[guid] = (p.get("Library"), p.get("Name"), p.get("Class"), pv)
    return out


def inventory(level_dir, want_points=False):
    level_dir = os.path.abspath(level_dir)
    asset_root = find_asset_root(level_dir)
    archetypes = load_archetypes(asset_root)
    rep = {
        "level": level_dir, "asset_root": asset_root,
        "layers": {}, "types": collections.Counter(), "entity_classes": collections.Counter(),
        "archetypes": collections.Counter(), "ai_characters": [], "nav_areas": [],
        "seed_points": [], "smart_objects": collections.Counter(), "baked": {},
    }
    lyrs = sorted(glob.glob(os.path.join(level_dir, "layers", "**", "*.lyr"), recursive=True))
    for path in lyrs:
        rel = os.path.relpath(path, os.path.join(level_dir, "layers")).replace("\\", "/")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as ex:
            rep["layers"][rel] = {"error": str(ex)}
            continue
        counts = collections.Counter()
        for o in root.iter("Object"):
            t = o.get("Type", "?")
            counts[t] += 1
            rep["types"][t] += 1
            if o.get("EntityClass"):
                rep["entity_classes"][o.get("EntityClass")] += 1
            pos = vec(o.get("Pos"))
            q = vec(o.get("Rotate"), 4) if o.get("Rotate") else [1.0, 0.0, 0.0, 0.0]

            if t == "EntityArchetype":
                guid = (o.get("Prototype") or "").strip("{}").lower()
                lib, name, cls, pv = archetypes.get(guid, (o.get("PrototypeLibrary"), "?" + guid, "?", {}))
                full = "%s.%s" % (lib, name) if lib and not str(name).startswith("?") else name
                rep["archetypes"][full] += 1
                # Archetypes are also used for props (pallets, crates); only a faction or a
                # behavior tree makes one an AI character.
                if not (pv.get("esFaction") or pv.get("esModularBehaviorTree") or cls == "Human"):
                    continue
                p2 = o.find("Properties2")
                over = {}
                if p2 is not None:
                    over = {k: v for k, v in p2.attrib.items() if v not in ("", "0", "<None>")}
                rep["ai_characters"].append({
                    "name": o.get("Name"), "layer": rel, "archetype": full, "class": cls,
                    "guid": guid, "pos": [round(c, 2) for c in pos], "yaw": round(yaw_deg(q), 1),
                    "archetype_props": pv, "instance_overrides": over})
            elif o.get("EntityClass") and o.find("Properties") is not None:
                props = o.find("Properties")
                pv = {k: props.get(k) for k in AI_PROPS if props.get(k)}
                if "esFaction" in pv or "esModularBehaviorTree" in pv:
                    rep["ai_characters"].append({
                        "name": o.get("Name"), "layer": rel, "archetype": None,
                        "class": o.get("EntityClass"), "pos": [round(c, 2) for c in pos],
                        "yaw": round(yaw_deg(q), 1), "archetype_props": pv, "instance_overrides": {}})

            if t == "NavigationArea":
                pts = [rotate(q, vec(p.get("Pos"))) for p in o.iter("Point")]
                world = [[pos[0] + p[0], pos[1] + p[1], pos[2] + p[2]] for p in pts]
                agents = o.find("AffectedAgentTypes")
                on = [k for k, v in (agents.attrib.items() if agents is not None else []) if v == "1"]
                xs = [p[0] for p in world] or [pos[0]]
                ys = [p[1] for p in world] or [pos[1]]
                area = {
                    "name": o.get("Name"), "layer": rel, "exclusion": o.get("Exclusion") == "1",
                    "floor_z": round(pos[2], 2), "height": float(o.get("Height") or 0),
                    "agents": on, "points": len(world),
                    "bbox": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
                }
                if want_points:
                    area["world_points"] = [[round(c, 2) for c in p] for p in world]
                rep["nav_areas"].append(area)
            elif t == "NavigationSeedPoint" or o.get("EntityClass") == "NavigationSeedPoint":
                rep["seed_points"].append({"name": o.get("Name"), "pos": [round(c, 2) for c in pos]})
            elif t == "SmartObject" or o.get("EntityClass") == "SmartObject":
                props = o.find("Properties")
                cls = props.get("soclasses_SmartObjectClass") if props is not None else None
                rep["smart_objects"][cls or "(unset)"] += 1
        rep["layers"][rel] = dict(counts)

    pak = os.path.join(level_dir, "level.pak")
    if os.path.isfile(pak):
        for e in crypak.entries(pak):
            if e["name"].lower().endswith(".bai"):
                rep["baked"][e["name"]] = e["usize"]
    for p in glob.glob(os.path.join(level_dir, "*.bai")):
        rep["baked"]["(loose) " + os.path.basename(p)] = os.path.getsize(p)
    return rep


def markdown(rep):
    L = []
    w = L.append
    w("# Level inventory: %s" % os.path.basename(rep["level"]))
    w("")
    w("Asset root: `%s`" % rep["asset_root"])
    w("")
    w("## Object types (%d objects, %d layers)" % (sum(rep["types"].values()), len(rep["layers"])))
    w("")
    w(", ".join("%s %d" % kv for kv in rep["types"].most_common()))
    w("")
    w("## Entity classes")
    w("")
    w(", ".join("%s %d" % kv for kv in rep["entity_classes"].most_common()) or "(none)")
    w("")
    w("## AI characters (%d)" % len(rep["ai_characters"]))
    w("")
    if rep["archetypes"]:
        w("Archetypes used: " + ", ".join("%s x%d" % kv for kv in rep["archetypes"].most_common()))
        w("")
    seen = set()
    for c in rep["ai_characters"]:
        key = c["archetype"] or c["class"]
        if key in seen:
            continue
        seen.add(key)
        w("- **%s** (class %s): %s" % (key, c["class"],
          ", ".join("%s=%s" % kv for kv in sorted(c["archetype_props"].items())) or "no AI props found"))
    for c in rep["ai_characters"][:40]:
        w("  - `%s` at %s yaw %s [%s]%s" % (c["name"], c["pos"], c["yaw"], c["layer"],
          (" overrides " + json.dumps(c["instance_overrides"])) if c["instance_overrides"] else ""))
    w("")
    inc = [a for a in rep["nav_areas"] if not a["exclusion"]]
    exc = [a for a in rep["nav_areas"] if a["exclusion"]]
    w("## Navigation: %d inclusion areas, %d exclusion volumes, %d seed points" %
      (len(inc), len(exc), len(rep["seed_points"])))
    w("")
    for a in inc + exc:
        w("- %s `%s` floor z=%s height=%s agents=%s pts=%d bbox=%s [%s]" % (
            "EXCL" if a["exclusion"] else "AREA", a["name"], a["floor_z"], a["height"],
            ",".join(a["agents"]) or "-", a["points"], a["bbox"], a["layer"]))
        if a.get("world_points"):
            w("    " + " ".join(str(p) for p in a["world_points"]))
    for s in rep["seed_points"]:
        w("- SEED `%s` at %s" % (s["name"], s["pos"]))
    w("")
    w("## Cover and smart objects")
    w("")
    w("CoverSurface objects: %d" % rep["types"].get("CoverSurface", 0))
    w("SmartObject classes: " + (", ".join("%s x%d" % kv for kv in rep["smart_objects"].most_common()) or "(none)"))
    w("")
    w("## Baked AI data in level.pak")
    w("")
    if rep["baked"]:
        for k, v in sorted(rep["baked"].items()):
            w("- %s  %d bytes" % (k, v))
        w("")
        w("mnmnav*.bai present means the navmesh was exported (exporter.export_to_engine) and "
          "loads with the level; without it the mesh exists only until the editor closes.")
    else:
        w("(none - navmesh has never been exported for this level)")
    w("")
    w("## Per layer")
    w("")
    for k, v in rep["layers"].items():
        interesting = {t: n for t, n in v.items() if t in AI_TYPES or t in ("EntityArchetype", "Entity")} if isinstance(v, dict) else v
        w("- `%s`: %s" % (k, ", ".join("%s %d" % kv for kv in sorted(interesting.items())) if isinstance(interesting, dict) else interesting))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level_dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--nav-points", action="store_true")
    a = ap.parse_args()
    rep = inventory(a.level_dir, a.nav_points)
    if a.json:
        print(json.dumps(rep, indent=1, default=lambda o: dict(o)))
    else:
        print(markdown(rep))


if __name__ == "__main__":
    main()
