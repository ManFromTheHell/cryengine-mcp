#!/usr/bin/env python3
"""The level's NON-ENTITY objects (brushes, navigation areas, probes, shapes ...) as the editor kept them, against the layer files, without running the game (ENGINE_LEARNING_PLAN A9 / F8).

A layer file (`layers/*.lyr`) stores every object as <Object Type=.. Name=.. attr=...> with child nodes (CollisionFiltering, Points ...). When the level loads, each object reads the attributes it knows
from that node (CBaseObject::Serialize and its subclasses): a name it does not know is IGNORED WITHOUT A MESSAGE, a value it cannot read leaves the default, a value outside its range is clamped. The
bridge verb `bridge.object.xml <name>` writes the live object back through the same serializer (what the Properties panel shows and what the next save would write); this compares the two, the way
`entity_check` does for the components of entities:

  O001 an object in a layer file that the editor does not hold (not loaded, or its class is not registered)     O004 (information) an attribute the live object has and the file lacks: the file predates it
  O002 an attribute in the file that the live object does not serialize: the engine ignored it, silently        O005 a child node that differs (a missing node, a different attribute set or value)
  O003 a value that differs (not read, clamped, or another type): both are shown                                  O006 (information) the editor holds a different NUMBER of objects than the layer files

    python checks/object_check.py [level folder name]        default: ashvale
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import collections
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
LEVELS = os.path.join(PROJECT, "Assets", "levels")
TOLERANCE = 1e-3                                           # the editor writes floats with 6-7 significant digits and positions in the order of 100 m

sys.path.insert(0, HERE)

NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class ObjectCheckError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def read_objects(level="ashvale", levels_dir=LEVELS):
    """-> [Element] for every Object that is not an Entity (entity_check covers those), and the total number of Object elements in the layers"""
    folder = os.path.join(levels_dir, level, "layers")
    if not os.path.isdir(folder):
        raise ObjectCheckError("no layers folder at %s" % folder)
    out, total = [], 0
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".lyr"):
            continue
        try:
            root = ET.parse(os.path.join(folder, f)).getroot()
        except ET.ParseError as exc:
            raise ObjectCheckError("%s is not valid XML: %s" % (f, exc)) from exc
        for o in root.iter("Object"):
            total += 1
            if o.get("Type") != "Entity":
                o.set("_file", f)
                out.append(o)
    return out, total


def same_value(a, b):
    """equal as text, or equal as numbers (a comma list is compared member by member) within the tolerance"""
    if a == b:
        return True
    pa, pb = [s.strip() for s in a.split(",")], [s.strip() for s in b.split(",")]
    if len(pa) != len(pb):
        return False
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if NUMBER.match(x) and NUMBER.match(y) and abs(float(x) - float(y)) <= TOLERANCE:
            continue
        return False
    return True


def compare_node(file_node, live_node, where, findings):
    """attributes of one node, then its children by tag (and position among the same tags)"""
    fa, la = dict(file_node.attrib), dict(live_node.attrib)
    fa.pop("_file", None)
    if file_node.tag == "Component":
        # a component INSTANCE id: the editor makes a new one every time the level loads (measured: stable within a session, different from the saved file's), so it is identity, not a setting
        fa.pop("guid", None)
        la.pop("guid", None)
    for k in sorted(set(fa) - set(la)):
        findings.append(_finding("warning", "O002", where, "the file sets %s=%r but the live %s does not have that attribute: the engine ignored it" % (k, fa[k][:40], file_node.get("Type") or file_node.tag),
                                 "a misspelt or renamed property: remove it from the layer file"))
    for k in sorted(set(la) - set(fa)):
        findings.append(_finding("info", "O004", where, "the live object has %s=%r that the file does not: the file predates the property (the engine's default applies)" % (k, la[k][:40])))
    for k in sorted(set(fa) & set(la)):
        if not same_value(fa[k], la[k]):
            findings.append(_finding("warning", "O003", where, "%s: the file says %r, the live object holds %r (not read, clamped, or another type)" % (k, fa[k][:40], la[k][:40])))
    fkids, lkids = collections.defaultdict(list), collections.defaultdict(list)
    for c in file_node:
        fkids[c.tag].append(c)
    for c in live_node:
        lkids[c.tag].append(c)
    for tag in sorted(set(fkids) | set(lkids)):
        fl, ll = fkids.get(tag, []), lkids.get(tag, [])
        if len(fl) != len(ll):
            findings.append(_finding("warning", "O005", where, "child <%s>: the file has %d, the live object %d" % (tag, len(fl), len(ll))))
            continue
        for i, (fc, lc) in enumerate(zip(fl, ll)):
            compare_node(fc, lc, "%s/%s%s" % (where, tag, ("[%d]" % i) if len(fl) > 1 else ""), findings)


def check(level=None, levels_dir=LEVELS, live=None, objects=None, total=None, live_count="read"):
    """-> {"level", "objects", "compared", "findings", "counts"}; level: the folder under Assets/levels, default the level the open editor holds; live: a function name -> the live object's Element (None when the editor does not hold it); default: the open editor's bridge.object.xml"""
    if level is None:
        import open_level
        level = open_level.name()
    if objects is None:
        objects, total = read_objects(level, levels_dir)
    if live is None:
        import object_props
        try:
            object_props.object_count()                            # one cheap call first: with no editor every object would otherwise read as "not held"
        except Exception as exc:                                   # noqa: BLE001
            raise ObjectCheckError("the editor does not answer (%s): this check needs Sandbox open with the level" % str(exc)[:80]) from exc
        live = object_props.get_xml
    findings = []
    names = collections.Counter(o.get("Name") for o in objects)
    compared = 0
    for o in objects:
        name = o.get("Name", "")
        where = "%s (%s)" % (name, o.get("Type"))
        if names[name] > 1:
            findings.append(_finding("info", "O007", where, "the name occurs %d times in the layer files: the editor's names are unique, so only one of them can be addressed by name" % names[name]))
            names[name] = -1
        try:
            live_node = live(name)
        except Exception:                                          # noqa: BLE001  (a name the editor does not hold)
            live_node = None
        if live_node is None:
            findings.append(_finding("error", "O001", where, "the layer file %s holds this object but the editor does not (not loaded, or its class is not registered)" % o.get("_file")))
            continue
        compared += 1
        compare_node(o, live_node, where, findings)
    if live_count == "read" and total is not None:
        try:
            import object_props
            n = object_props.object_count()
            if n != total:
                findings.append(_finding("info", "O006", level, "the editor holds %d objects, the layer files %d: objects were made or removed in the editor and not saved, or by a game script" % (n, total)))
        except Exception:                                          # noqa: BLE001
            pass
    elif isinstance(live_count, int) and total is not None and live_count != total:
        findings.append(_finding("info", "O006", level, "the editor holds %d objects, the layer files %d" % (live_count, total)))
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return {"level": level, "objects": len(objects), "compared": compared, "types": dict(collections.Counter(o.get("Type") for o in objects)), "findings": findings, "counts": counts}


def main():
    level = sys.argv[1] if len(sys.argv) > 1 else "ashvale"
    rep = check(level)
    print("level %s: %d non-entity objects %s, %d compared with the editor" % (level, rep["objects"], rep["types"], rep["compared"]))
    for f in rep["findings"][:40]:
        print("  %-7s %s %s: %s" % (f["severity"], f["code"], f["where"], f["message"]))
    print("%(error)d error(s), %(warning)d warning(s), %(info)d information" % rep["counts"])
    return 1 if rep["counts"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
