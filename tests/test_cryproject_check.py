#!/usr/bin/env python3
"""Self-test of cryproject_check.py: the file checks on synthetic project data with positive controls (offline), then, with the editor open, Game.cryproject against what the engine's project
manager parsed from it.

    test_cryproject_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import cryproject_check as P

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


REGISTRY = {"engine-5.7": {"info": {"id": "engine-5.7"}, "uri": "x"}}


def cvar(name):
    return {"sys_spec": {"type": "int", "settable": {"release": "yes"}, "flags": []}, "r_driver": {"type": "string", "settable": {"release": "no"}, "flags": ["READONLY"]}}.get(name.lower())


def main():
    # The synthetic cases are built from the active project's Game.cryproject and index its plug-in list (entries 0, 5 and 6): they need a project file with 7 or more plug-ins.
    try:
        with open(os.path.join(P.PROJECT, "Game.cryproject"), encoding="utf-8") as f:
            game = json.load(f)
        n_plugins = len(game["require"]["plugins"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("SKIP test_cryproject_check: no usable Game.cryproject in %s (%s)" % (P.PROJECT, exc))
        return 0
    if n_plugins < 7:
        print("SKIP test_cryproject_check: the project's Game.cryproject lists %d plug-ins, the cases index entries 0, 5 and 6 (need 7 or more)" % n_plugins)
        return 0
    base =lambda: copy.deepcopy(game)                             # noqa: E731

    r = P.check_file("Game.cryproject", base(), None, REGISTRY, None)
    check("control: the project's own Game.cryproject has no finding when the engine id is known and the catalog is not asked", r == [], codes(r))
    d = base()
    del d["require"]
    check("positive control: a missing section is P001", codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)) == ["P001"], "")
    d = base()
    d["version"] = 3
    check("positive control: an older version is P002, information (migrated on load)", [f["severity"] for f in P.check_file("Game.cryproject", d, None, REGISTRY, None) if f["code"] == "P002"] == ["info"], "")
    d = base()
    d["info"]["guid"] = "cf957e42-2f59-03e6-d8cf"
    check("positive control: a GUID that is not a GUID is P003", codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)) == ["P003"], "")
    d = base()
    d["require"]["engine"] = "engine-9.9"
    check("positive control: an engine id the launcher does not list is P004 and names the known ones", any("engine-5.7" in f["message"] for f in P.check_file("Game.cryproject", d, None, REGISTRY, None) if f["code"] == "P004"), "")
    d = base()
    d["content"]["assets"] = ["NoSuchAssets"]
    check("positive control: an assets directory that does not exist is P005, an error", [f["severity"] for f in P.check_file("Game.cryproject", d, None, REGISTRY, None) if f["code"] == "P005"] == ["error"], "")
    d = base()
    d["require"]["plugins"][0]["type"] = "EType::Natve"
    check("positive control: a plug-in type the parser does not know is P006", "P006" in codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)), "")
    d = base()
    d["require"]["plugins"][6]["platforms"] = ["PS5"]
    check("positive control: a platform that is not an EPlatform is P006", "P006" in codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)), "")
    d = base()
    d["require"]["plugins"][0]["path"] = "CryNoSuchPlugin"
    check("positive control: an engine plug-in whose DLL is not in the engine's bin is P007", "P007" in codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)), "")
    d = base()
    d["require"]["plugins"][5]["path"] = "bin/win_x64/NoGame.dll"
    check("positive control: a game DLL path that does not exist is P007, an error", [f["severity"] for f in P.check_file("Game.cryproject", d, None, REGISTRY, None) if f["code"] == "P007"] == ["error"], "")
    d = base()
    d["require"]["plugins"].append(dict(d["require"]["plugins"][0]))
    check("positive control: a plug-in listed twice is P010", "P010" in codes(P.check_file("Game.cryproject", d, None, REGISTRY, None)), "")
    d = base()
    d["console_variables"]["sys_spek"] = "2"
    d["console_variables"]["sys_spec"] = "high"
    r = P.check_file("Game.cryproject", d, None, REGISTRY, cvar)
    check("positive control: a misspelt variable is P008 (silence otherwise) and a non-number for an int variable is P008, an error", any("sys_spek" in f["where"] for f in r) and any(f["severity"] == "error" and "sys_spec" in f["where"] for f in r), codes(r))
    d = base()
    d["console_variables"]["r_driver"] = "vk"
    check("a variable the engine cannot set in Release is reported (information)", any("cannot be set in a Release build" in f["message"] for f in P.check_file("Game.cryproject", d, None, REGISTRY, cvar)), "")
    eng = {"name": "My Project", "guid": game["info"]["guid"], "plugins": [{"type": (0 if p["type"].endswith("Native") else 1), "path": p["path"], "platforms": []} for p in game["require"]["plugins"]]}
    check("control: an engine parse that matches the file has no P009", P.check_file("Game.cryproject", base(), eng, REGISTRY, None) == [], "")
    eng2 = copy.deepcopy(eng)
    eng2["plugins"].pop(2)
    check("positive control: an engine that holds a plug-in fewer than the file lists is P009", "P009" in codes(P.check_file("Game.cryproject", base(), eng2, REGISTRY, None)), "")
    eng3 = copy.deepcopy(eng)
    eng3["name"] = "Other"
    check("positive control: an engine that read another project name is P009", "P009" in codes(P.check_file("Game.cryproject", base(), eng3, REGISTRY, None)), "")
    check("a plug-in without an extension is looked for under the engine's bin, one with .dll under the project", P.plugin_dll("CrySensorSystem").lower().endswith(os.path.join("bin", "win_x64", "crysensorsystem.dll")) and P.plugin_dll("bin/win_x64/Game.dll").lower().endswith(os.path.join("bin", "win_x64", "game.dll")), P.plugin_dll("CrySensorSystem")[-40:])
    check("the launcher's engine registry lists engine-5.7", "engine-5.7" in (P.engine_registry() or {}), list(P.engine_registry() or {}))

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        res = P.check(["Game.cryproject"])
        eng = res["engine"]
        check("the engine's project manager read Game.cryproject: name 'My Project', the same GUID, engine-5.7, 7 plug-in entries (the two spellings EType and EPluginType both read as Native)",
              eng and eng["name"] == "My Project" and eng["guid"].lower() == game["info"]["guid"].lower() and eng["engine"] == "engine-5.7" and len(eng["plugins"]) == 7 and all(p["type"] == 0 for p in eng["plugins"]), eng and (eng["name"], len(eng["plugins"])))
        check("and it read the platform list of the last entry (CryGamePlatform, PS4 = 4)", eng and eng["plugins"][6]["path"] == "CryGamePlatform" and eng["plugins"][6]["platforms"] == [4], eng and eng["plugins"][6])
        check("the comparison finds nothing wrong (no P009): the engine holds exactly what the file says; no error in the project's file", res["projects"][0]["engine_compared"] and res["ok"] and "P009" not in [f["code"] for f in res["findings"]], res["errors"])

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
