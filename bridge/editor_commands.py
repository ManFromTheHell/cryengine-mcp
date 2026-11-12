#!/usr/bin/env python3
"""The editor's own command list (ENGINE_LEARNING_PLAN A6): everything ICommandManager holds, 642 commands in 40 modules, from the open editor (bridge verb `bridge.commands.dump`).

It is NOT the same list as the Python API (editor_api: 586 functions). 528 names are in both; 114 commands are NOT in the Python catalog (ai.regenerate_agent_type_layer0..5,
ai.generate_cover_surfaces, ai.set_navigation_update_*, asset.show_dependency_graph, camera.toggle_object_collisions ...: menu and toolbar actions), and 58 Python functions are not commands
(general.* helpers). A command that is not in the Python catalog is run through ICommandManager::Execute (the MCP tool execute_editor_command, bridge.execute_command).

    python bridge/editor_commands.py --capture        needs the editor (writes engine_data/editor_commands.json)
    python bridge/editor_commands.py <text> [--module ai]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
DATA = os.path.join(HERE, "engine_data", "editor_commands.json")
_CACHE = {}


def capture(path=DATA):
    import bridge_py
    os.makedirs(os.path.dirname(path), exist_ok=True)
    reply = bridge_py.send("bridge.commands.dump " + path.replace(chr(92), "/"), 60)
    if not reply.get("ok"):
        raise RuntimeError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % reply.get("error"))
    return json.loads(reply["output"])


def load(path=DATA):
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if _CACHE.get("path") != path or _CACHE.get("mt") != mt:
        if not mt:
            raise RuntimeError("no command catalog at %s: run  python bridge/editor_commands.py --capture  with the editor open" % path)
        rows = json.load(open(path, encoding="utf-8"))
        python_names = set()
        try:
            import editor_api
            api = editor_api.search("", limit=5000)
            python_names = {x["name"] for x in (api[0] if isinstance(api, tuple) else api)}
        except Exception:                                        # noqa: BLE001 - without the API catalog the overlap is simply not known
            python_names = set()
        for r in rows:
            r["full"] = r["module"] + "." + r["name"]
            r["in_python_api"] = r["full"] in python_names if python_names else None
        _CACHE.update(path=path, mt=mt, rows=rows)
    return _CACHE["rows"]


def search(text="", module="", python_only=None, limit=40, path=DATA):
    """Commands whose module.name, description or parameter names contain every word. python_only: True = only those in the Python API catalog, False = only those that are NOT (run through ICommandManager)."""
    words = [w for w in text.lower().split() if w]
    rows = []
    for r in load(path):
        if module and r["module"].lower() != module.lower():
            continue
        if python_only is not None and r["in_python_api"] is not None and r["in_python_api"] != python_only:
            continue
        hay = (r["full"] + " " + r["description"] + " " + " ".join(p["name"] for p in r["params"])).lower()
        if all(w in hay for w in words):
            rows.append(r)
    return rows[:limit], len(rows)


def modules(path=DATA):
    out = {}
    for r in load(path):
        out[r["module"]] = out.get(r["module"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        print(capture())
        return
    mod = args[args.index("--module") + 1] if "--module" in args else ""
    text = " ".join(a for a in args if not a.startswith("--") and a != mod)
    rows, total = search(text, mod, None, 60)
    for r in rows:
        print("%-44s %-6s %s" % (r["full"], "py" if r["in_python_api"] else "cmd", r["description"][:70]))
    print("%d of %d shown" % (len(rows), total))


if __name__ == "__main__":
    main()
