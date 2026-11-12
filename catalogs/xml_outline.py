#!/usr/bin/env python3
"""Outline XML files - loose or inside CryPak archives - so an unfamiliar data format can be
understood without reading thousands of lines: every distinct tag path with its count and the
attributes of its first occurrence.

    xml_outline.py <asset dir or pak> <game path | regex> [--depth N] [--attrs N] [--names]

  <game path>  exact path, e.g. Scripts/Entities/Vehicles/Implementations/Xml/HMMWV.xml
  regex        with --many: outline every matching file (short form per file)
  --names      list the "name"/"Name" attribute values of the first repeated element instead
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
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


def load(target, path):
    if os.path.isdir(target):
        data = crypak.read_asset(target, path)
    else:
        data = None
        for e in crypak.entries(target):
            if e["name"].lower() == path.lower():
                data = crypak.read(e)
                break
    if data is None:
        raise SystemExit("not found: " + path)
    return ET.fromstring(data)


def outline(root, depth=6, nattrs=6, width=180):
    counts = collections.OrderedDict()
    first = {}

    def walk(e, p, d):
        key = p + "/" + e.tag
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, e)
        if d < depth:
            for c in e:
                walk(c, key, d + 1)
    walk(root, "", 0)
    lines = []
    for k, n in counts.items():
        a = first[k].attrib
        attrs = " ".join('%s="%s"' % (x, v[:40]) for x, v in list(a.items())[:nattrs])
        more = " +%d attrs" % (len(a) - nattrs) if len(a) > nattrs else ""
        line = "%5d  %s  %s%s" % (n, k, attrs, more)
        lines.append(line[:width])
    return lines


def names(root):
    groups = collections.Counter(c.tag for c in root.iter())
    for tag, n in groups.most_common():
        if n < 2:
            continue
        vals = [e.get("name") or e.get("Name") for e in root.iter(tag) if e.get("name") or e.get("Name")]
        if vals:
            return tag, vals
    return None, []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("path")
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--attrs", type=int, default=6)
    ap.add_argument("--many", action="store_true")
    ap.add_argument("--names", action="store_true")
    a = ap.parse_args()
    if a.many:
        for e in crypak.find(a.target, a.path):
            try:
                root = ET.fromstring(crypak.read(e))
            except ET.ParseError as ex:
                print("== %s  (parse error %s)" % (e["name"], ex))
                continue
            if a.names:
                tag, vals = names(root)
                print("== %s  <%s> x%d: %s" % (e["name"], tag, len(vals), ", ".join(vals[:40])))
            else:
                print("== %s" % e["name"])
                for l in outline(root, min(a.depth, 3), 4, 160)[:25]:
                    print("   " + l)
        return
    root = load(a.target, a.path)
    if a.names:
        tag, vals = names(root)
        print("<%s> x%d" % (tag, len(vals)))
        print("\n".join(vals))
    else:
        print("\n".join(outline(root, a.depth, a.attrs)))


if __name__ == "__main__":
    main()
