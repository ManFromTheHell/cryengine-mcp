#!/usr/bin/env python3
"""The level's entities and their components' properties as the ENGINE kept them (ENGINE_LEARNING_PLAN A9/B), from the open editor, without running the game.

A level layer (`layers/*.lyr`) stores each entity with its class and, per component, a `<properties .../>` element. When the level loads the engine gives every component the values it can read: a property
name the component does not have (a typo, or a member renamed in the C++ `Reflect`) is IGNORED WITHOUT A MESSAGE, a component type that is not registered is dropped, an entity class that is not registered
creates no entity, and a value that cannot be read (a word where a number goes) leaves the default. The bridge's `bridge.component.properties <entity> <component>` serialises a live component with the engine's
own serialization (the values the Inspector shows); this module compares that with the layer file:

  N001 an entity in a layer that the editor did not load, or that has no entity (its class is not registered)     N005 an entity in the editor that no layer file holds (made at run time, or by another layer)
  N002 a component the file lists that the live entity does not have (the engine lists the ones it has)           N006 the file has components or properties this check cannot compare (legacy Lua <Properties>,
  N003 a property in the file that the component does not have (ignored silently)                                      a component type that occurs twice on one entity)
  N004 a value the engine holds differs from the file's (not read, clamped, or the property is another type)

    python checks/entity_check.py [level folder name]        default: ashvale
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
LEVELS = os.path.join(PROJECT, "Assets", "levels")

sys.path.insert(0, HERE)


class EntityError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def read_layers(level, levels_dir=LEVELS):
    """-> [{name, cls, layer, file, components: [{type, props (an element or None)}], legacy: bool}] for every Entity object in the level's layers."""
    folder = os.path.join(levels_dir, level, "layers")
    if not os.path.isdir(folder):
        raise EntityError("no layers folder at %s" % folder)
    out = []
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".lyr"):
            continue
        try:
            root = ET.parse(os.path.join(folder, f)).getroot()
        except ET.ParseError as exc:
            raise EntityError("%s is not valid XML: %s" % (f, exc)) from exc
        for o in root.iter("Object"):
            if o.get("Type") != "Entity":
                continue
            comps = []
            for c in o.findall("Components/Component"):
                comps.append({"type": c.get("TypeName") or c.get("Name") or "", "props": c.find("properties")})
            legacy = o.find("Properties")
            out.append({"name": o.get("Name", ""), "cls": o.get("EntityClass", ""), "layer": o.get("Layer", os.path.splitext(f)[0]), "file": f, "components": comps,
                        "legacy": legacy is not None and bool(legacy.attrib or len(legacy))})
    return out


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def same_value(file_value, engine_value):
    """A file attribute (a string) against the value the engine serialised."""
    if isinstance(engine_value, bool):
        v = (file_value or "").strip().lower()
        return (v in ("true", "1")) if engine_value else (v in ("false", "0"))
    if isinstance(engine_value, (int, float)):
        n = _num(file_value)
        return n is not None and abs(n - engine_value) <= 1e-4 * max(1.0, abs(engine_value))
    return (file_value or "") == (engine_value if isinstance(engine_value, str) else str(engine_value))


def compare_props(element, engine, where, path=""):
    """A <properties> element against the engine's serialised dict -> [finding]"""
    out = []
    if element is None:
        return out
    for key, value in element.attrib.items():
        label = "%s.%s" % (path, key) if path else key
        if key not in engine:
            out.append(_finding("warning", "N003", where, "the file sets '%s' and the component has no such property: the engine ignores it (a typo, or renamed in the code). The component has: %s" % (label, ", ".join(sorted(engine)) or "-")))
        elif isinstance(engine[key], dict):
            out.append(_finding("warning", "N004", where, "'%s' is a structure in the engine and a single value in the file" % label))
        elif not same_value(value, engine[key]):
            out.append(_finding("warning", "N004", where, "'%s': the file says %r and the engine holds %r" % (label, value, engine[key])))
    for child in element:
        label = "%s.%s" % (path, child.tag) if path else child.tag
        if child.tag not in engine:
            out.append(_finding("warning", "N003", where, "the file has the element '%s' and the component has no such property: the engine ignores it" % label))
        elif isinstance(engine[child.tag], dict):
            out.extend(compare_props(child, engine[child.tag], where, label))
        else:
            out.append(_finding("info", "N006", where, "'%s' is an element in the file and %s in the engine: not compared" % (label, type(engine[child.tag]).__name__)))
    return out


def engine_component(name, component, timeout=30):
    """The live component's serialised properties -> (properties dict | None, error text)"""
    import bridge_py
    r = bridge_py.send("bridge.component.properties %s %s" % (name, component), timeout)
    if not r.get("ok"):
        return None, str(r.get("error") or r)
    try:
        d = json.loads(r.get("output") or "{}")
    except ValueError as exc:
        return None, "unreadable answer: %s" % exc
    if not d.get("ok"):
        return None, str(d.get("error") or d)
    return d.get("properties") or {}, ""


def engine_objects():
    """-> {name: type} of the editor's objects"""
    import bridge_py
    r = bridge_py.send("bridge.object.list", 60)
    try:
        rows = json.loads(r.get("output") or "[]")
    except ValueError:
        rows = []
    return {o["name"]: o.get("type", "") for o in rows if isinstance(o, dict) and "name" in o}


def check(level=None, entities=None, objects=None, fetch=None, levels_dir=LEVELS):
    """-> {ok, errors, warnings, findings, entities, components, compared}.

    `level`: the folder under Assets/levels, default the level the open editor holds. `entities`, `objects` (name -> type) and `fetch(name, component)` can be given (tests); otherwise the layer files and the open editor are read."""
    if level is None and entities is None:
        import open_level
        level = open_level.name()
    entities = entities if entities is not None else read_layers(level, levels_dir)
    objects = objects if objects is not None else engine_objects()
    fetch = fetch or engine_component
    findings, compared, comps = [], 0, 0
    seen_names = set()
    for e in entities:
        where = "%s %s [%s]" % (e["file"], e["name"], e["cls"])
        seen_names.add(e["name"])
        if " " in e["name"] or not e["name"]:
            findings.append(_finding("info", "N006", where, "the name has a space or is empty: the bridge cannot address it, not compared"))
            continue
        if e["name"] not in objects:
            findings.append(_finding("error", "N001", where, "the editor did not load this entity (is the class '%s' registered?)" % e["cls"]))
            continue
        if e["legacy"]:
            findings.append(_finding("info", "N006", where, "legacy <Properties> (a Lua entity class) are not compared here: see entity_class_properties"))
        counts = {}
        for c in e["components"]:
            counts[c["type"]] = counts.get(c["type"], 0) + 1
        for c in e["components"]:
            comps += 1
            cwhere = "%s %s / %s" % (e["file"], e["name"], c["type"])
            if counts[c["type"]] > 1:
                findings.append(_finding("info", "N006", cwhere, "this component type occurs %d times on the entity: not compared" % counts[c["type"]]))
                continue
            props, err = fetch(e["name"], c["type"])
            if props is None:
                findings.append(_finding("error", "N002", cwhere, err[:300]))
                continue
            compared += 1
            findings.extend(compare_props(c["props"], props, cwhere))
    if objects and len(objects) < 500:
        extra = sorted(n for n, t in objects.items() if t == "Entity" and n not in seen_names)
        for n in extra[:20]:
            findings.append(_finding("info", "N005", n, "an entity in the editor that no layer file of the level holds"))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "entities": len(entities), "components": comps, "compared": compared}


def main():
    r = check(sys.argv[1] if len(sys.argv) > 1 else None)       # None: the level the open editor holds
    print("%s: %d error(s), %d warning(s); %d entities, %d components, %d compared with the engine" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["entities"], r["components"], r["compared"]))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:300]))


if __name__ == "__main__":
    main()
