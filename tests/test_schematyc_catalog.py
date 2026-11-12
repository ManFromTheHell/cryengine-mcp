#!/usr/bin/env python3
"""Self-test of schematyc_catalog.py against the dump the editor's registry wrote (engine_data/schematyc_env.json). Editor-free; about 1 s.

    test_schematyc_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import schematyc_catalog as S

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-88s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    try:
        d = S.load()
    except RuntimeError as exc:                                  # no dump made yet (it needs the editor open once: python catalogs/schematyc_catalog.py --capture)
        print("  (test skipped: %s)" % exc)
        return 0
    items = d["items"]
    check("the file's own header agrees with its content (elements == items == sum of the counts: %d)" % d["elements"], d["elements"] == len(items) == sum(d["counts"].values()), d["counts"])
    check("every GUID is unique", len({i["guid"] for i in items}) == len(items), len(items))
    check("the registry holds components, functions, signals, data types and modules (the engine's vocabulary is not empty)",
          all(d["counts"].get(k, 0) > 0 for k in ("Component", "Function", "Signal", "DataType", "Module")), d["counts"])
    comps = [i for i in items if i["type"] == "Component"]
    roots = [r for r in (S.SOURCE, S.PROJECT) if r and os.path.isdir(r)]
    if not roots:
        print("  (control skipped: neither the engine source (CRYENGINE_SOURCE) nor the project folder exists here)")
    else:
        # a dump made on another project names registration files this project does not have: only the engine's own components (not Code/Components/...) must be found
        engine_comps = [c for c in comps if not c["source"].startswith("Code/Components/")]
        found = sum(1 for c in engine_comps if any(os.path.exists(os.path.join(r, c["source"])) for r in roots))
        if not os.path.isdir(S.SOURCE):
            print("  (control skipped: the engine source folder %s does not exist; set CRYENGINE_SOURCE)" % S.SOURCE)
        else:
            check("control: every engine component's registration file exists in the engine source (%d of %d)" % (found, len(engine_comps)), found == len(engine_comps), [c["name"] for c in engine_comps if not any(os.path.exists(os.path.join(r, c["source"])) for r in roots)][:3])
    own = [c for c in comps if c["source"].startswith("Code/Components/")]
    if os.path.isfile(os.path.join(S.PROJECT, "Code", "Components", "Health.cpp")):
        check("the project's own components are in the registry, with the file they are registered from (%d, e.g. Health -> Code/Components/Health.cpp)" % len(own),
              len(own) >= 15 and S.get("Health")["source"] == "Code/Components/Health.cpp", len(own))
    else:
        print("  (check skipped: this project has no Code/Components/Health.cpp, the sample game's component the dump was made with)")
    rb = S.get("Rigidbody")
    check("a component carries its members with type and flags (Rigidbody: Type, Resting, Buoyancy, Simulation; category Physics)",
          isinstance(rb, dict) and rb["category"] == "Physics" and {"Type", "Resting", "Buoyancy", "Simulation"} <= {m["name"] for m in rb["component"]["members"]},
          [m["name"] for m in rb["component"]["members"]] if isinstance(rb, dict) else rb)
    fn = S.get("Comment", "Function")
    check("a function carries its inputs and outputs with types (Standard/Log Comment takes Message and Stream)", isinstance(fn, dict) and [i["name"] for i in fn["function"]["inputs"]][:2] == ["Message", "Stream"] and fn["path"] == "Standard/Log", fn["path"] if isinstance(fn, dict) else fn)
    amb = S.get("stop")
    check("an ambiguous name ('stop': a signal and three functions) answers with ALL candidates, and type= narrows it to one",
          isinstance(amb, list) and len(amb) >= 4 and isinstance(S.get("stop", "Signal"), dict) and S.get("stop", "Signal")["type"] == "Signal", len(amb) if isinstance(amb, list) else amb)
    check("a name nothing has finds nothing", S.get("NoSuchThingAnywhere") == [], "")
    rows, total = S.search("physics", "Component", limit=100)
    check("search by type and word (components mentioning 'physics': %d)" % total, total >= 5 and all(r["type"] == "Component" for r in rows), total)
    rows, total = S.search("", "Component", category="Audio", limit=100)
    check("search by component category (Audio: %d)" % total, total >= 8 and all(r["category"] == "Audio" for r in rows), total)
    rows, total = S.search("", "Function", path_prefix="Standard/Math/Vector3", limit=100)
    check("search by module path (Standard/Math/Vector3 functions: %d)" % total, total >= 10 and all(r["path"].startswith("Standard/Math/Vector3") for r in rows), total)
    s = S.summary()
    check("the summary lists the component categories, largest first", list(s["component_categories"].values()) == sorted(s["component_categories"].values(), reverse=True) and s["component_categories"].get("Physics", 0) >= 10, list(s["component_categories"])[:3])
    raw = open(S.DUMP, "rb").read()
    check("the dump is strict UTF-8 JSON (a Latin-1 byte in an engine description broke the first one)", json.loads(raw.decode("utf-8")) is not None, len(raw))

    bad = results.count(False)
    print("schematyc catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
