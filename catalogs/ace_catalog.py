#!/usr/bin/env python3
"""The project's audio controls (the Audio Controls Editor's ACE libraries) as a catalog, and the check the editor does not make (ENGINE_LEARNING_PLAN H1).

An ACE library (Assets/audio/<middleware>/ace/*.xml) defines triggers (each with events that play or stop a sample: volume, loops, fades, panning), parameters, switches, environments.
The game refers to them by NAME through CryAudio::StringToId("name") and the id is just a hash: a name that is misspelt or was never defined produces NO error, only silence.
This reads every library, finds every StringToId("...") in the game code, and says which names the code uses that no library defines (silence), which sample files an event names that
are missing (the linter's L008 does that too), and which triggers nothing refers to by a literal (dead, or built from a name at run time, like the voice lines).

    python catalogs/ace_catalog.py                  the summary and the problems
    python catalogs/ace_catalog.py <text>           triggers, parameters and switches whose name contains the text
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
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")


def libraries(assets=ASSETS):
    out = []
    for root_dir, _, files in os.walk(os.path.join(assets, "audio")):
        if os.path.basename(root_dir).lower() != "ace":
            continue
        for f in sorted(files):
            if f.lower().endswith(".xml"):
                out.append(os.path.join(root_dir, f))
    return out


def load(assets=ASSETS):
    """{triggers: {name: {library, events: [{name, type, volume, loop_count, fade_in_time, fade_out_time, enable_panning}]}}, parameters: {name: library}, switches: {...}, libraries: [...]}"""
    data = {"triggers": {}, "parameters": {}, "switches": {}, "environments": {}, "libraries": [], "duplicates": []}
    for path in libraries(assets):
        lib = os.path.splitext(os.path.basename(path))[0]
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            data["libraries"].append({"library": lib, "error": str(exc)})
            continue
        data["libraries"].append({"library": lib, "path": os.path.relpath(path, PROJECT).replace(chr(92), "/"), "triggers": len(root.findall("./Triggers/Trigger")), "parameters": len(root.findall("./Parameters/Parameter"))})
        for t in root.findall("./Triggers/Trigger"):
            name = t.get("name")
            if name in data["triggers"]:
                data["duplicates"].append((name, data["triggers"][name]["library"], lib))
            data["triggers"][name] = {"library": lib, "events": [dict(e.attrib) for e in t.findall("Event")]}
        for kind, tag in (("parameters", "Parameter"), ("switches", "Switch"), ("environments", "Environment")):
            for p in root.iter(tag):
                if p.get("name"):
                    data[kind][p.get("name")] = lib
    return data


STRING_TO_ID = re.compile(r'StringToId\(\s*"([^"]+)"\s*\)')


def code_references(code=CODE):
    """{name: [file:line, ...]} for every CryAudio::StringToId("name") literal in the game's C++."""
    refs = {}
    for root_dir, _, files in os.walk(code):
        for f in files:
            if not f.lower().endswith((".cpp", ".h", ".hpp")):
                continue
            path = os.path.join(root_dir, f)
            for i, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                if line.lstrip().startswith("//"):
                    continue
                for m in STRING_TO_ID.finditer(line):
                    refs.setdefault(m.group(1), []).append("%s:%d" % (os.path.relpath(path, PROJECT).replace(chr(92), "/"), i))
    return refs


PREFIX = re.compile(r'"([a-z][a-z0-9]*_[a-z0-9_]*_)"')


def code_prefixes(code=CODE):
    """String literals ending in '_' ("ash_vox_"): the front half of a trigger name the game builds at run time (prefix + dialogue id + "_" + index)."""
    found = set()
    for root_dir, _, files in os.walk(code):
        for f in files:
            if f.lower().endswith((".cpp", ".h", ".hpp")):
                for line in open(os.path.join(root_dir, f), encoding="utf-8", errors="replace"):
                    if not line.lstrip().startswith("//"):
                        found.update(m.group(1) for m in PREFIX.finditer(line))
    return found


def problems(assets=ASSETS, code=CODE):
    """-> {undefined_in_code: {name: [where]}, unreferenced_triggers: [name], missing_samples: [(trigger, file)], duplicates: [...], broken_libraries: [...]}"""
    d = load(assets)
    refs = code_references(code)
    prefixes = code_prefixes(code)
    known = set(d["triggers"]) | set(d["parameters"]) | set(d["switches"]) | set(d["environments"])
    # the files an event names, under audio/<middleware>/assets (what the linter's L008 also checks)
    present = set()
    for root_dir, _, files in os.walk(os.path.join(assets, "audio")):
        for f in files:
            present.add(f.lower())
    missing = []
    for name, t in d["triggers"].items():
        for e in t["events"]:
            sample = e.get("name")
            if sample and sample.lower() not in present:
                missing.append((name, sample))
    return {"undefined_in_code": {n: w for n, w in sorted(refs.items()) if n not in known},
            "built_at_run_time": sorted(n for n in d["triggers"] if n not in refs and any(n.startswith(pre) for pre in prefixes)),
            "unreferenced_triggers": sorted(n for n in d["triggers"] if n not in refs and not any(n.startswith(pre) for pre in prefixes)),
            "missing_samples": missing, "duplicates": d["duplicates"], "broken_libraries": [l for l in d["libraries"] if "error" in l],
            "code_names": len(refs), "triggers": len(d["triggers"]), "parameters": len(d["parameters"])}


def search(text="", assets=ASSETS):
    words = [w for w in text.lower().split() if w]
    d = load(assets)
    rows = []
    for kind in ("triggers", "parameters", "switches", "environments"):
        for name, v in d[kind].items():
            if all(w in name.lower() for w in words):
                lib = v["library"] if isinstance(v, dict) else v
                row = {"kind": kind[:-1], "name": name, "library": lib}
                if kind == "triggers":
                    row["events"] = [{k: e[k] for k in ("name", "type", "volume", "loop_count", "fade_in_time", "fade_out_time", "enable_panning") if k in e} for e in v["events"]]
                rows.append(row)
    return rows


def main():
    args = sys.argv[1:]
    if args:
        for r in search(" ".join(args)):
            print(r["kind"], r["name"], r["library"], r.get("events", ""))
        return
    p = problems()
    print("%d triggers, %d parameters, %d names in the code" % (p["triggers"], p["parameters"], p["code_names"]))
    for k in ("undefined_in_code", "missing_samples", "duplicates", "broken_libraries"):
        print(k, json.dumps(p[k])[:600])
    print("built at run time from a prefix in the code: %d; referenced by nothing: %d (%s)" % (len(p["built_at_run_time"]), len(p["unreferenced_triggers"]), ", ".join(p["unreferenced_triggers"][:8])))


if __name__ == "__main__":
    main()
