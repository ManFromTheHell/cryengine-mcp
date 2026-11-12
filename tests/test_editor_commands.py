#!/usr/bin/env python3
"""Self-test of editor_commands.py against the command list the editor wrote (engine_data/editor_commands.json) and the Python API catalog. Editor-free, about 1 s.

    test_editor_commands.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_commands as C

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    rows = C.load()
    full = {r["full"] for r in rows}
    check("the editor's command manager holds hundreds of commands in dozens of modules (%d in %d)" % (len(rows), len(C.modules())), len(rows) > 500 and len(C.modules()) > 30 and len(full) == len(rows), len(rows))
    both = sum(1 for r in rows if r["in_python_api"])
    check("most commands are also in the Python API catalog (%d) but not all: %d are reachable only through ICommandManager" % (both, len(rows) - both), both > 450 and len(rows) - both > 80, both)
    ai, total = C.search("regenerate", "ai", python_only=False, limit=50)
    check("the navigation regeneration commands are commands the Python catalog does NOT list (ai.regenerate_agent_type_layer0 ... 5, regenerate_ignored)", {"ai.regenerate_agent_type_layer0", "ai.regenerate_ignored"} <= {r["full"] for r in ai}, [r["full"] for r in ai][:3])
    py, _ = C.search("", "ai", python_only=True, limit=200)
    check("control: a command the Python catalog does list is not in that answer (ai.regenerate_agent_type_layer0 is not in the Python-only list)", "ai.regenerate_agent_type_layer0" not in {r["full"] for r in py}, len(py))
    r1, _ = C.search("set position object", limit=10)
    check("search by words finds object.set_position with its parameters in the description or names", any(r["full"] == "object.set_position" for r in r1), [r["full"] for r in r1][:3])
    check("every command has a module and a name, and a description is a string", all(r["module"] and r["name"] and isinstance(r["description"], str) for r in rows), "")
    bad = results.count(False)
    print("editor commands test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
