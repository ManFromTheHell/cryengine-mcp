#!/usr/bin/env python3
"""Explain a CRYENGINE project's DATA libraries (not levels): what weapons, vehicles, props,
effects, UI and game modes exist and how they are parameterised. Offline; reads loose files
and .paks under the asset dir through crypak.py. The level-side counterpart is level_study.py.

    gamedata_study.py <asset dir> [section ...] [--json]

Sections (default all):
    items       weapons / ammo / accessories: class, category, fire modes (rate, damage, clip),
                zoom modes, ammo physics (speed, mass, pierceability), accessories
    vehicles    each vehicle XML: movement type, seats, parts, components (damageMax), actions
    mfx         MaterialEffects.xml matrix: which effect library row x surface column fires what
    surfaces    Libs/MaterialEffects/SurfaceTypes.xml: physics (friction, pierceability,
                breakability), AI sound radii
    archetypes  every Libs/EntityArchetypes library: prototypes and their entity classes
    particles   every Libs/Particles library: top-level effects and emitter counts
    ui          Libs/UI: UI elements (gfx files) and UI action flowgraphs
    gamerules   Scripts/GameRules/GameModes.xml + each mode's module classes
    input       action maps and actions (Libs/Config/defaultProfile.xml)
    difficulty  Difficulty/*.cfg cvars side by side
    prefabs     prefab libraries and object counts
    locale      localization packs and string counts
    entities    entities.xml: every Lua entity class and its script
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
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypak  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SS = "{urn:schemas-microsoft-com:office:spreadsheet}"


class Assets:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._index = None

    def index(self):
        if self._index is None:
            idx = {}
            for pak in crypak.paks_under(self.root):
                for e in crypak.entries(pak):
                    idx.setdefault(e["name"].lower(), e)
            for d, _dirs, files in os.walk(self.root):
                for f in files:
                    full = os.path.join(d, f)
                    rel = os.path.relpath(full, self.root).replace("\\", "/").lower()
                    idx[rel] = {"loose": full, "name": rel}
            self._index = idx
        return self._index

    def find(self, regex):
        rx = re.compile(regex, re.I)
        return sorted(k for k in self.index() if rx.search(k))

    def read(self, path):
        e = self.index().get(path.lower())
        if e is None:
            return None
        if "loose" in e:
            with open(e["loose"], "rb") as f:
                return f.read()
        return crypak.read(e)

    def xml(self, path):
        data = self.read(path)
        if data is None:
            return None
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            # some CryEngine XML files carry leading junk / BOM
            try:
                return ET.fromstring(data.decode("utf-8", "replace").lstrip("﻿"))
            except ET.ParseError:
                return None


def params(elem, game="SP"):
    """<param name value [GAME]> children -> dict, preferring the requested GAME variant."""
    out = {}
    if elem is None:
        return out
    for p in elem.findall("param"):
        g = p.get("GAME")
        if g and g != game:
            continue
        out[p.get("name")] = p.get("value")
    return out


def s_items(A):
    rows = []
    for path in A.find(r"^scripts/entities/items/xml/.*\.xml$"):
        root = A.xml(path)
        if root is None:
            continue
        kind = path.split("/")[4] if len(path.split("/")) > 5 else "?"
        if root.tag == "item":
            info = {"file": path, "kind": kind, "name": root.get("name"), "class": root.get("class"),
                    "category": root.get("category"), "params": {k: v for k, v in params(root.find("params")).items()
                                                               if k in ("display_name", "giveable", "selectable", "mass", "itemClass", "tag", "adb")}}
            geo = root.find("geometry")
            if geo is not None:
                info["geometry"] = {c.tag: c.get("name") for c in geo if c.get("GAME") in (None, "SP")}
            info["ammos"] = [dict(a.attrib) for a in root.findall("ammos/ammo") if a.get("GAME") in (None, "SP")]
            fms = []
            default = {}
            for fm in root.findall("firemodes/firemode"):
                fire = {}
                for f in fm.findall("fire"):
                    fire.update(params(f))
                if fm.get("type") == "default":
                    default = fire
                    continue
                merged = dict(default, **fire)
                fms.append({"name": fm.get("name"), "type": fm.get("type"),
                            **{k: merged.get(k) for k in ("ammo_type", "rate", "damage", "clip_size", "hit_type",
                                                        "damage_drop_per_meter", "damage_drop_min_distance") if merged.get(k)}})
            if not fms and default:
                fms.append({"type": "default", **{k: default.get(k) for k in ("ammo_type", "rate", "damage", "clip_size") if default.get(k)}})
            info["firemodes"] = fms
            info["zoommodes"] = [{"name": z.get("name"), "type": z.get("type")} for z in root.findall("zoommodes/zoommode")]
            info["accessories"] = [a.get("name") for a in root.findall("accessories/accessory")]
            mm = [m for m in root.findall("MovementModifiers") if m.get("GAME") in (None, "SP")]
            if mm:
                info["movement_modifiers"] = dict(mm[0].attrib)
            rows.append(info)
        elif root.tag == "ammo":
            ph = root.find("physics")
            rows.append({"file": path, "kind": "Ammo", "name": root.get("name"), "class": root.get("class"),
                         "physics_type": ph.get("type") if ph is not None else None,
                         "physics": {k: v for k, v in params(ph).items() if k in ("mass", "speed", "gravity", "pierceability", "material")},
                         "params": {k: v for k, v in params(root.find("params")).items() if k in ("lifetime", "ammo_category", "showtime")},
                         "explosion": params(root.find("explosion")) or None})
    hit_types = A.xml("scripts/entities/items/hittypes.xml")
    return {"items": rows,
            "hit_types": [(h.get("name"), h.get("flags")) for h in hit_types.findall("hit_type")] if hit_types is not None else [],
            "equipment_packs": {p.rsplit("/", 1)[-1]: [c.tag for c in (A.xml(p).find("Items") or [])] for p in A.find(r"^libs/equipmentpacks/.*\.xml$") if A.xml(p) is not None}}


def s_vehicles(A):
    rows = []
    for path in A.find(r"^scripts/entities/vehicles/implementations/xml/.*\.xml$"):
        root = A.xml(path)
        if root is None:
            continue
        mp = root.find("MovementParams")
        movement = [c.tag for c in mp] if mp is not None else []
        rows.append({
            "name": root.get("name"), "file": path, "movement": movement,
            "movement_params": {c.tag: {k: v for k, v in list(c.attrib.items())[:10]} for c in (mp if mp is not None else [])},
            "seats": [{"name": s.get("name"), "isDriver": s.get("isDriver"), "views": [v.get("class") for v in s.findall("Views/View")],
                       "weapons": [w.get("class") for w in s.findall("SeatActions/SeatAction")]} for s in root.findall("Seats/Seat")],
            "parts": collections.Counter(p.get("class") for p in root.iter("Part")),
            "components": [(c.get("name"), c.get("damageMax")) for c in root.findall("Components/Component")][:30],
            "damage_group_templates": [g.get("useTemplate") for g in root.findall("Damages/DamagesGroups/DamagesGroup")],
            "actions": [a.get("class") for a in root.findall("Actions/Action")],
            "physics": {c.tag: dict(c.attrib) for c in (root.find("Physics") or [])},
            "mannequin": dict(root.find("Mannequin").attrib) if root.find("Mannequin") is not None else None,
        })
    return {"vehicles": rows, "lua_entities": A.find(r"^scripts/entities/vehicles/implementations/.*\.lua$"),
            "damage_templates": A.find(r"^scripts/entities/vehicles/damagestemplates/")}


def spreadsheet_rows(root):
    """SpreadsheetML -> list of rows, honouring ss:Index gaps."""
    out = []
    for ws in root.iter(SS + "Worksheet"):
        for row in ws.iter(SS + "Row"):
            cells, col = [], 0
            for cell in row.findall(SS + "Cell"):
                idx = cell.get(SS + "Index")
                if idx:
                    col = int(idx) - 1
                while len(cells) < col:
                    cells.append("")
                data = cell.find(SS + "Data")
                cells.append(data.text if data is not None and data.text else "")
                col += 1
            out.append(cells)
        break   # first worksheet only
    return out


def s_mfx(A):
    root = A.xml("libs/materialeffects/materialeffects.xml")
    if root is None:
        return {"error": "no MaterialEffects.xml"}
    rows = [r for r in spreadsheet_rows(root) if any(c.strip() for c in r)]
    if not rows:
        return {"error": "empty sheet"}
    header = rows[0]
    table = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        entries = {header[i]: r[i] for i in range(1, min(len(r), len(header))) if r[i].strip()}
        table.append({"row": r[0], "surfaces_with_effect": len(entries), "sample": dict(list(entries.items())[:8])})
    return {"columns_surfaces": [h for h in header[1:] if h], "rows": table,
            "fx_libraries": {p.rsplit("/", 1)[-1]: len((A.xml(p) or ET.Element("x")).findall("Effect")) for p in A.find(r"^libs/materialeffects/fxlibs/.*\.xml$")},
            "note": "Each row is an effect 'library:row' looked up by game code (e.g. footstep_player, bullet hit, vehicle); each column a surface type; the cell names the effect in the FXLib."}


def s_surfaces(A):
    root = A.xml("libs/materialeffects/surfacetypes.xml")
    if root is None:
        return {"error": "no SurfaceTypes.xml"}
    out = []
    for s in root.findall("SurfaceType"):
        ph = s.find("Physics")
        ai = s.find("AI")
        out.append({"name": s.get("name"), **({k: ph.get(k) for k in ("friction", "elasticity", "pierceability", "can_shatter", "coll_types", "no_collide") if ph is not None and ph.get(k) is not None}),
                    "breakable_2d": s.find("breakable_2d") is not None, "breakage_particles": s.find("BreakageParticles") is not None,
                    "ai": {k: ai.get(k) for k in ("fImpactRadius", "fFootStepRadius") if ai is not None and ai.get(k)}})
    return {"surface_types": out}


def s_archetypes(A):
    out = {}
    for path in A.find(r"^libs/entityarchetypes/.*\.xml$"):
        root = A.xml(path)
        if root is None:
            continue
        protos = root.findall(".//EntityPrototype")
        out[path.rsplit("/", 1)[-1]] = {"count": len(protos), "classes": dict(collections.Counter(p.get("Class") for p in protos)),
                                        "names": [p.get("Name") for p in protos][:60]}
    return out


def s_particles(A):
    out = {}
    for path in A.find(r"^libs/particles/.*\.xml$"):
        root = A.xml(path)
        if root is None:
            continue
        top = [p for p in root if p.tag == "Particles"]
        out[path.rsplit("/", 1)[-1]] = {"top_level_effects": len(top), "all_emitters": len(root.findall(".//Particles")),
                                        "examples": [p.get("Name") for p in top][:25]}
    pfx = A.find(r"^particles/.*\.pfx$")
    return {"xml_libraries": out, "pfx_files": pfx,
            "note": "Libs/Particles/*.xml are legacy (pfx1) libraries referenced as 'library.effect'; *.pfx are wavicle (pfx2) effects."}


def s_ui(A):
    elements = []
    for p in A.find(r"^libs/ui/uielements/.*\.xml$"):
        root = A.xml(p)
        if root is None:
            continue
        for el in root.iter("UIElement"):
            gfx = el.find("GFx")
            elements.append({"name": el.get("name"), "gfx": gfx.get("file") if gfx is not None else None,
                             "functions": len(el.findall(".//function")), "events": len(el.findall(".//event")),
                             "variables": len(el.findall(".//variable"))})
    actions = []
    for p in A.find(r"^libs/ui/uiactions/.*\.xml$"):
        root = A.xml(p)
        nodes = collections.Counter(n.get("Class") for n in root.iter("Node")) if root is not None else {}
        actions.append({"file": p.rsplit("/", 1)[-1], "nodes": sum(nodes.values()),
                        "ui_nodes": sorted({c for c in nodes if c and c.startswith("UI")})[:12]})
    return {"ui_elements": elements, "ui_actions": actions, "gfx_files": A.find(r"^libs/ui/.*\.gfx$")}


def s_gamerules(A):
    modes = A.xml("scripts/gamerules/gamemodes.xml")
    out = []
    if modes is not None:
        for gm in modes.findall("GameMode"):
            rules_path = (gm.find("Rules").get("path") if gm.find("Rules") is not None else "").lower()
            rules = A.xml(rules_path) if rules_path else None
            out.append({"mode": gm.get("name"), "aliases": [a.get("name") for a in gm.findall("Alias")],
                        "teamBased": gm.get("teamBased"), "rules": rules_path,
                        "modules": {c.tag: c.get("class") for c in (rules if rules is not None else [])}})
    return {"game_modes": out, "lua": A.find(r"^scripts/gamerules/.*\.lua$")}


def s_input(A):
    root = A.xml("libs/config/defaultprofile.xml")
    if root is None:
        return {"error": "no defaultProfile.xml"}
    maps = {}
    for am in root.iter("actionmap"):
        acts = []
        for a in am.findall("action"):
            key = a.get("keyboard") or (a.find("keyboard").get("input") if a.find("keyboard") is not None and a.find("keyboard").get("input") else
                                        ",".join(i.get("input") for i in a.findall("keyboard/inputdata")))
            acts.append("%s=%s" % (a.get("name"), key or "-"))
        maps[am.get("name")] = acts
    return {"actionmaps": maps}


def s_difficulty(A):
    table = collections.OrderedDict()
    files = A.find(r"^difficulty/.*\.cfg$")
    for p in files:
        for line in (A.read(p) or b"").decode("utf-8", "replace").splitlines():
            m = re.match(r"\s*([A-Za-z_][\w.]*)\s*=\s*([^\s-]+)", line)
            if m:
                table.setdefault(m.group(1), {})[p.rsplit("/", 1)[-1]] = m.group(2)
    return {"files": files, "cvars": table}


def s_prefabs(A):
    out = {}
    for p in A.find(r"^prefabs/.*\.xml$"):
        root = A.xml(p)
        if root is None:
            continue
        items = root.findall(".//Prefab") or root.findall("./*")
        out[p] = {"prefabs": len(root.findall(".//Prefab")), "objects": len(root.findall(".//Object")),
                  "examples": [i.get("Name") or i.get("name") for i in items][:15]}
    return out


def s_locale(A):
    out = {}
    for p in A.find(r"^[a-z]+(_xml)?/.*\.(xml|json)$"):
        if not re.match(r"^(english|german|french|spanish|italian|russian|polish|japanese|chinese|korean|portuguese|turkish|czech)", p):
            continue
        data = A.read(p) or b""
        out[p] = {"bytes": len(data), "rows": data.count(b"<Row")}
    loc = A.xml("libs/localization/localization.xml")
    return {"files": out,
            "localization_xml": [{"tag": c.tag, **dict(c.attrib), "children": [x.tag for x in c][:10]} for c in loc] if loc is not None else None}


def s_entities(A):
    root = A.xml("entities.xml")
    if root is None:
        return {"error": "no entities.xml"}
    by_folder = collections.defaultdict(list)
    for e in root.findall("Entity"):
        script = e.get("Script") or ""
        folder = script.split("/")[2] if script.count("/") >= 3 else "?"
        by_folder[folder].append(e.get("Name") + (" (invisible)" if e.get("Invisible") == "1" else ""))
    return {"lua_entity_classes": dict(by_folder), "count": sum(len(v) for v in by_folder.values())}


def lua_table_after(text, key):
    """Return the {...} source that follows `key =` (first match), brace-matched, comments and
    strings respected well enough for CryEngine entity scripts."""
    m = re.search(r"\b%s\s*=\s*\{" % re.escape(key), text)
    if not m:
        return None
    i = m.end() - 1
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if text.startswith("--", j):
            k = text.find("\n", j)
            j = n if k < 0 else k
            continue
        if c in "\"'":
            k = j + 1
            while k < n and text[k] != c:
                k += 2 if text[k] == "\\" else 1
            j = k + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None


def lua_to_dict(src):
    """Very small Lua table reader for entity Properties: nested tables, strings, numbers,
    booleans, identifiers. Array parts and function values are skipped."""
    src = re.sub(r"--\[\[.*?\]\]", "", src, flags=re.S)
    src = re.sub(r"--[^\n]*", "", src)
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}=,;]|[^\s{}=,;]+', src)
    pos = 0

    def value():
        nonlocal pos
        t = tokens[pos]
        if t == "{":
            return table()
        pos += 1
        if t[0] in "\"'":
            return t[1:-1]
        if t in ("true", "false"):
            return t == "true"
        try:
            return float(t) if "." in t or "e" in t.lower() else int(t)
        except ValueError:
            return t

    def table():
        nonlocal pos
        pos += 1  # {
        out = {}
        while pos < len(tokens) and tokens[pos] != "}":
            if tokens[pos] in (",", ";"):
                pos += 1
                continue
            if pos + 1 < len(tokens) and tokens[pos + 1] == "=":
                k = tokens[pos].strip("[]\"'")
                pos += 2
                out[k] = value()
            else:
                value()   # array element: skipped
        pos += 1  # }
        return out

    try:
        return table()
    except (IndexError, RecursionError):
        return {}


def s_entityprops(A, classes=None):
    """Default Properties of each Lua entity class - what a designer can set in the editor."""
    root = A.xml("entities.xml")
    out = collections.OrderedDict()
    for e in (root.findall("Entity") if root is not None else []):
        name, script = e.get("Name"), (e.get("Script") or "").lower()
        if classes and name not in classes:
            continue
        text = (A.read(script) or b"").decode("utf-8", "replace")
        src = lua_table_after(text, "Properties")
        if src is None:
            continue
        props = lua_to_dict(src)
        flat = {}

        def walk(d, prefix=""):
            for k, v in d.items():
                if isinstance(v, dict):
                    walk(v, prefix + k + ".")
                else:
                    flat[prefix + k] = v
        walk(props)
        out[name] = {"script": script, "count": len(flat), "properties": flat}
    return out


SECTIONS = collections.OrderedDict([
    ("items", s_items), ("vehicles", s_vehicles), ("mfx", s_mfx), ("surfaces", s_surfaces), ("archetypes", s_archetypes),
    ("particles", s_particles), ("ui", s_ui), ("gamerules", s_gamerules), ("input", s_input),
    ("difficulty", s_difficulty), ("prefabs", s_prefabs), ("locale", s_locale), ("entities", s_entities),
    ("entityprops", s_entityprops)])


def study(root, sections=None):
    A = Assets(root)
    out = collections.OrderedDict()
    for s in (sections or SECTIONS):
        try:
            out[s] = SECTIONS[s](A)
        except Exception as ex:  # noqa: BLE001 -- one broken library must not hide the others
            out[s] = {"error": "%s: %s" % (type(ex).__name__, ex)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asset_dir")
    ap.add_argument("sections", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bad = [s for s in a.sections if s not in SECTIONS]
    if bad:
        sys.exit("unknown section(s): %s; choose from %s" % (bad, ", ".join(SECTIONS)))
    data = study(a.asset_dir, a.sections or None)
    if a.json:
        print(json.dumps(data, indent=1, default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o)))
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import level_study
    for name, sec in data.items():
        print("\n########## %s ##########" % name.upper())
        print(level_study.to_text(json.loads(json.dumps(sec, default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o)))))


if __name__ == "__main__":
    main()
