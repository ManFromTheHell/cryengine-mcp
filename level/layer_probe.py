#!/usr/bin/env python3
"""Dump example objects out of a level's layer files, so you can see exactly how a
finished level configured something (an audio area, a collision wall, a probe, a
camera) and copy it.

    layer_probe.py <level dir> --summary                    type/class counts per layer
    layer_probe.py <level dir> --type AudioAreaAmbience      example objects of a type
    layer_probe.py <level dir> --class AudioTriggerSpot --limit 3
    layer_probe.py <level dir> --layer player_collisions --limit 2
    layer_probe.py <level dir> --name "AB01_LS" --full       whole XML, children included

Filters are case-insensitive regexes and combine (AND). --type matches the object
Type attribute (Brush, Entity, Designer, Shape, ...), --class the EntityClass.
--distinct N shows at most N objects per distinct (type, class, layer) so one run
gives a representative sample of every variant. Output is the object's XML with long
point lists / binary blobs shortened unless --full.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def shorten(elem, max_children=6, max_attr=160):
    """Copy of elem with long attribute values and long child lists trimmed."""
    e = ET.Element(elem.tag, {k: (v if len(v) <= max_attr else v[:max_attr] + "...[%d chars]" % len(v))
                              for k, v in elem.attrib.items()})
    e.text = elem.text if elem.text and len(elem.text) < 200 else (elem.text[:200] + "..." if elem.text else None)
    kids = list(elem)
    for k in kids[:max_children]:
        e.append(shorten(k, max_children, max_attr))
    if len(kids) > max_children:
        more = ET.Element("more")
        more.text = "%d more <%s> children" % (len(kids) - max_children, kids[max_children].tag)
        e.append(more)
    return e


def indent(elem, level=0):
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for child in elem:
            indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad


def objects(level_dir):
    for path in sorted(glob.glob(os.path.join(level_dir, "layers", "**", "*.lyr"), recursive=True)):
        rel = os.path.relpath(path, os.path.join(level_dir, "layers")).replace("\\", "/")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as ex:
            print("!! %s: %s" % (rel, ex), file=sys.stderr)
            continue
        for layer in root.iter("Layer"):
            info = {k: layer.get(k) for k in ("Name", "Hidden", "Frozen", "Exportable", "DefaultLoaded", "HavePhysics") if layer.get(k) is not None}
            for o in layer.iter("Object"):
                yield rel, info, o


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level_dir")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--type")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--layer")
    ap.add_argument("--name")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--distinct", type=int, default=0)
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()

    rx = lambda s: re.compile(s, re.I) if s else None
    rt, rc, rl, rn = rx(a.type), rx(a.cls), rx(a.layer), rx(a.name)

    if a.summary:
        per = collections.OrderedDict()
        flags = {}
        for rel, info, o in objects(a.level_dir):
            key = o.get("Type", "?") + (":" + o.get("EntityClass") if o.get("EntityClass") else "")
            per.setdefault(rel, collections.Counter())[key] += 1
            flags[rel] = info
        for rel, c in per.items():
            f = flags[rel]
            print("%s  [%s]" % (rel, " ".join("%s=%s" % kv for kv in f.items())))
            print("    " + ", ".join("%s %d" % kv for kv in c.most_common()))
        return

    shown = 0
    per_variant = collections.Counter()
    for rel, info, o in objects(a.level_dir):
        if rt and not rt.search(o.get("Type", "")):
            continue
        if rc and not rc.search(o.get("EntityClass", "") or ""):
            continue
        if rl and not rl.search(rel):
            continue
        if rn and not rn.search(o.get("Name", "")):
            continue
        variant = (o.get("Type"), o.get("EntityClass"), rel)
        if a.distinct:
            if per_variant[variant] >= a.distinct:
                continue
            per_variant[variant] += 1
        e = o if a.full else shorten(o)
        indent(e)
        print("---- %s  (layer flags: %s)" % (rel, " ".join("%s=%s" % kv for kv in info.items())))
        print(ET.tostring(e, encoding="unicode"))
        shown += 1
        if shown >= a.limit:
            break
    if not shown:
        print("no objects matched")


if __name__ == "__main__":
    main()
