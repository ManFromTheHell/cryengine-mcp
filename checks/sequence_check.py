#!/usr/bin/env python3
"""What the ENGINE holds of a Track View sequence (ENGINE_LEARNING_PLAN A2): the editor loads the sequence XML with CMovieSystem::LoadSequence (the engine's own loader), saves it back with
IAnimSequence::Serialize, and this compares the two documents. The intro of the game (libs/ashvale/sequences/ashvale_intro.xml) was written by hand as XML; this says whether the engine
accepts every node, track, key and attribute of such a file, or silently drops some, the way the particle loader does (particle_catalog.check).

    python checks/sequence_check.py <sequence.xml>        needs the editor (bridge verb bridge.sequence.roundtrip)

The copy is loaded under a throw-away name, GUID and id and removed again; the level's own sequences are not touched (LoadSequence REPLACES a sequence of the same name).
Numbers are compared as numbers: the engine writes 512 where a hand-written file says 512.0000.
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
SCRATCH = os.path.join(HERE, "engine_data", "pfx_scratch")


class SequenceError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def _numbers(v):
    try:
        return [float(x) for x in v.split(",")]
    except ValueError:
        return None


def same_value(a, b, name=""):
    """Equal as text, or as the same list of numbers within 1e-4 (relative); a GUID half (GUIDLo/GUIDHi) is hex, the engine writes it without leading zeros."""
    if a == b:
        return True
    if name in ("GUIDLo", "GUIDHi"):
        try:
            return int(a, 16) == int(b, 16)
        except ValueError:
            return False
    na, nb = _numbers(a), _numbers(b)
    if na is None or nb is None or len(na) != len(nb):
        return False
    return all(abs(x - y) <= 1e-4 * max(1.0, abs(x), abs(y)) for x, y in zip(na, nb))


def _walk(el, key=""):
    """{path: attributes}: an element is addressed by its parent's path, its tag and its position among the SAME tag (Key#1, Key#2 ...)."""
    key = key or "/" + el.tag
    out = {key: dict(el.attrib)}
    seen = {}
    for c in el:
        seen[c.tag] = seen.get(c.tag, 0) + 1
        out.update(_walk(c, "%s/%s#%d" % (key, c.tag, seen[c.tag])))
    return out


def diff_documents(src, out):
    """src, out: ElementTree roots. -> {dropped_elements, added_elements, dropped_attributes: [(path, name, value)], changed: [(path, name, was, now)], added_attributes}"""
    a, b = _walk(src), _walk(out)
    rep = {"dropped_elements": [k for k in a if k not in b], "added_elements": [k for k in b if k not in a], "dropped_attributes": [], "changed": [], "added_attributes": 0}
    for k, attrs in a.items():
        if k not in b:
            continue
        for name, v in attrs.items():
            if name not in b[k]:
                rep["dropped_attributes"].append((k, name, v))
            elif not same_value(v, b[k][name], name):
                rep["changed"].append((k, name, v, b[k][name]))
        rep["added_attributes"] += sum(1 for n in b[k] if n not in attrs)
    return rep


TICKS_PER_SECOND = 6000.0         # SAnimTime: 78000 ticks = the intro's 13 seconds


def late_keys(root):
    """Keys that lie after the sequence's end time: the engine KEEPS them (measured: a key at 150 s in a 13 s sequence loads and saves unchanged), they simply never play.
    -> [text]"""
    try:
        end = float(root.get("endTimeTicks", "0"))
    except ValueError:
        return []
    out = []
    for el in root.iter("Key"):
        t = el.get("timeTicks")
        if t is not None:
            try:
                if end and float(t) > end:
                    out.append("a key at %.1f s is after the sequence's end (%.1f s): it never plays" % (float(t) / TICKS_PER_SECOND, end / TICKS_PER_SECOND))
            except ValueError:
                pass
    return out[:20]


def check_text(text, name="sequence"):
    """-> {loaded, name, start, end, nodes: [{name, type, tracks: [{param, keys}]}], dropped_elements, dropped_attributes, changed, added_elements, added_attributes, ok, output}"""
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    fin, fout = os.path.join(SCRATCH, safe + ".seq_in.xml"), os.path.join(SCRATCH, safe + ".seq_engine.xml")
    with open(fin, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        os.remove(fin)
        raise SequenceError("not well-formed XML (%s): the engine skips such a file and the packager ships a stale copy" % exc)
    reply = bridge_py.send("bridge.sequence.roundtrip %s | %s" % (_slash(fin), _slash(fout)), 90)
    os.remove(fin)
    if not reply.get("ok"):
        raise SequenceError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % reply.get("error"))
    rep = json.loads(reply["output"])
    d = diff_documents(ET.fromstring(text), ET.parse(fout).getroot())
    rep.update(d)
    rep["output"] = _slash(fout)
    rep["warnings"] = late_keys(ET.fromstring(text))
    rep["ok"] = bool(rep.get("loaded")) and not (d["dropped_elements"] or d["dropped_attributes"] or d["changed"])
    return rep


def check(path):
    tried = [path] if os.path.isabs(path) else [os.path.join(PROJECT, path), os.path.join(PROJECT, "Assets", path)]      # the project's root, then Assets (how every other tool names an asset: libs/x/y.xml)
    full = next((t for t in tried if os.path.isfile(t)), None)
    if full is None:
        raise SequenceError("no such file: tried %s" % " and ".join(tried))
    return check_text(open(full, encoding="utf-8").read(), os.path.splitext(os.path.basename(full))[0])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    r = check(sys.argv[1])
    print("loaded" if r["loaded"] else "NOT LOADED", r["name"], "%.2f..%.2f s" % (r["start"], r["end"]))
    for n in r["nodes"]:
        print("  node %-20s type %d  %s" % (n["name"], n["type"], ", ".join("%s x%d" % (t["param"], t["keys"]) for t in n["tracks"])))
    for w in r.get("warnings", []):
        print("  warning: " + w)
    for k in ("dropped_elements", "dropped_attributes", "changed"):
        for it in r[k][:20]:
            print("  %-18s %s" % (k, it))
    print("OK: the engine kept everything" if r["ok"] else "the engine did NOT keep everything; its version: " + r["output"])


if __name__ == "__main__":
    main()
