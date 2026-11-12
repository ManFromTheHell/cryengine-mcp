#!/usr/bin/env python3
"""The Sandbox editor's Python API as a catalog (ENGINE_LEARNING_PLAN A1).

`editor_api.json` is the introspection dump of the running editor (593 functions in 25 modules, a snapshot of one editor build); CRYENGINE_BRIDGE.md is its prose form. What neither
answers is the question a person has while working: "is there a function for this, what does it take, is it safe to call, and have I ever used it?". This module
parses each function's signature and the engine's own CCommandDescription text, and adds four things the dump does not hold:

  modal          the function opens a dialog (calling it hangs the bridge until a person clicks): matched on wording plus the offenders found by reading the source
  read_only      the name says it only reads (get_/is_/has_/count_/num_/list_/exists_) AND it takes no argument: the candidates for the sweep
  project_calls  how many times a script of this project calls it (*.py and Assets/scripts): the gap between the 593 and what I have used
  sample         what it returned when the sweep called it in the open level (engine_data/editor_api_samples.json), so a return format is a lookup, not a guess

    python bridge/editor_api.py <text>            search     (--unused, --readonly, --module M)
    python bridge/editor_api.py --capture         write editor_api.json: the editor's Python modules and functions (needs the editor; run it first)
    python bridge/editor_api.py --sweep           call every read-only no-argument function in the open editor, once, and record what comes back (needs the editor)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ast
import json
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DUMP = os.path.join(HERE, "editor_api.json")
SAMPLES = os.path.join(HERE, "engine_data", "editor_api_samples.json")
PANES = os.path.join(HERE, "engine_data", "editor_panes.json")

SIG = re.compile(r"^(\w+)\((.*?)\)\s*->\s*(\S+)\s*(?::\s*(.*))?$", re.S)
ARG = re.compile(r"\((\w+)\)(\w+)")
CCD = re.compile(r'CCommandDescription\("((?:[^"\\]|\\.)*)"\)')
PARAM = re.compile(r'\.Param\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)')
PLAIN = re.compile(r'^"((?:[^"\\]|\\.)*)"')

# A function that opens a dialog blocks the editor's idle tick, which is what the bridge runs on (build_reference.py found these: by their descriptions and by reading the source).
DIALOG_HINTS = ("dialog", "opens ", "browse", "choose", "prompt", "message box", "edit box", "combo box", "file box")
KNOWN_MODAL = {"terrain.set_terrain_max_height", "terrain.import_heightmap", "terrain.export_heightmap", "terrain.resize_terrain", "terrain.generate_terrain",
               "terrain.generate_terrain_texture", "terrain.terrain_texture_dialog", "terrain.import_block", "terrain.export_block", "terrain.export_area",
               "terrain.export_area_with_objects", "terrain.set_ocean_height", "terrain.import_layers", "terrain.export_layers", "level.go_to_position"}
# The box functions (message_box*, edit_box*, combo_box, open_file_box) are not flagged by the description scan: their descriptions say "Shows ... box", not "dialog"/"opens". They block the bridge until a
# person clicks, exactly like the dialog openers. particle.show_effect opens a floating Particle Editor window that a plugin deploy would otherwise close instead of the editor.
KNOWN_MODAL |= {"general.message_box", "general.message_box_ok", "general.message_box_yes_no", "general.edit_box", "general.edit_box_check_data_type", "general.combo_box", "general.open_file_box",
                "particle.show_effect"}
# Functions that are not dialogs but must not be called by accident: they end the editor, unload the bridge's own plugin, or run a script file/Lua. execute_python / execute_editor_command refuse these (and every
# modal function) unless really=true (server_support.check_python / check_editor_command).
DANGEROUS = {"general.exit": "quits the editor", "general.unload_all_plugins": "unloads every editor plugin, the bridge's own included (the bridge then stops answering)",
             "general.load_all_plugins": "reloads every editor plugin", "general.run_lua": "runs a Lua command in the editor", "general.run_file": "runs a script file",
             "general.run_file_parameters": "runs a script file", "general.close": "closes the active window (which can be the editor)", "keybind.reset_all": "resets every key binding"}
# panes are dockable windows, not modal dialogs (editor_panes says so): the 'opens' wording must not make them need really=true
NOT_MODAL = {"general.open_pane", "general.open_or_focus_pane"}
READ_PREFIX = ("get_", "is_", "has_", "count_", "num_", "list_", "exists_", "can_")
SKIP_MODULES = {"json", "SandboxBridge"}       # json is the standard library leaking into the namespace; SandboxBridge is the plugin's own glue


def _describe(doc):
    doc = " ".join(doc.split())
    m = SIG.match(doc)
    args, ret, desc, params = [], "?", "", {}
    if m:
        _name, argtext, ret, rest = m.groups()
        args = [(n, t) for t, n in ARG.findall(argtext or "")]
        rest = (rest or "").strip()
        cm, pm = CCD.search(rest), PLAIN.match(rest)
        if cm:
            desc = cm.group(1)
            params = {k: v for k, v in PARAM.findall(rest)}
        elif pm:
            desc = pm.group(1)
        else:
            desc = rest
    return args, ret, desc, params


def project_text(root=PROJECT):
    """All the text of this project's own scripts (*.py and Assets/scripts), to count calls in. Cheap."""

    files = []
    for folder in (HERE, os.path.join(root, "Assets", "scripts")):
        for dirpath, _dirs, names in os.walk(folder):
            if any(os.sep + skip in dirpath for skip in ("job_runs", "rag", "tts_piper", "texture_src", "ci_runs", "visual_baseline", ".cache", "engine_data", "__pycache__")):
                continue
            files.extend(os.path.join(dirpath, n) for n in names if n.endswith((".py", ".lua", ".txt")) and not n.startswith(("editor_api", "test_editor_api")))
    text = []
    for f in files:
        try:
            text.append(open(f, encoding="utf-8", errors="replace").read())
        except OSError:
            pass
    blob = "\n".join(text)
    return blob


_CACHE = {}


def catalog(dump=DUMP, samples=SAMPLES, root=PROJECT):
    key = (dump, os.path.getmtime(dump), os.path.getmtime(samples) if os.path.exists(samples) else 0)
    if _CACHE.get("key") == key:
        return _CACHE["rows"]
    raw = json.load(open(dump, encoding="utf-8"))
    swept = json.load(open(samples, encoding="utf-8")) if os.path.exists(samples) else {}
    blob = project_text(root)
    counts = {}                                              # one pass over the text for every `module.function(`, not 586 passes (that took 11 s)
    for m_ in re.finditer(r"\b(\w+)\.(\w+)\s*\(", blob):
        counts[(m_.group(1), m_.group(2))] = counts.get((m_.group(1), m_.group(2)), 0) + 1
    rows = []
    for module, fns in sorted(raw.items()):
        if module in SKIP_MODULES or not isinstance(fns, dict):
            continue
        for fn, doc in sorted(fns.items()):
            args, ret, desc, params = _describe(doc)
            name = "%s.%s" % (module, fn)
            low = (desc + " " + name).lower()
            modal = (name in KNOWN_MODAL or any(h in low for h in DIALOG_HINTS)) and name not in NOT_MODAL
            read_only = fn.startswith(READ_PREFIX) and not args and not modal
            calls = counts.get((module, fn), 0)
            rows.append({"name": name, "module": module, "args": args, "returns": ret, "description": desc, "param_help": params, "modal": modal,
                         "callable": ret != "?", "read_only": read_only, "dangerous": name in DANGEROUS, "project_calls": calls, "sample": swept.get(name)})
    _CACHE.update(key=key, rows=rows)
    return rows


def search(text="", module="", unused_only=False, read_only=False, include_modal=True, limit=25):
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    out = []
    for r in catalog():
        if module and r["module"] != module:
            continue
        if unused_only and r["project_calls"]:
            continue
        if read_only and not r["read_only"]:
            continue
        if not include_modal and r["modal"]:
            continue
        hay = (r["name"] + " " + r["description"] + " " + " ".join(r["param_help"].values())).lower()
        if all(w in hay for w in words):
            out.append((0 if all(w in r["name"].lower() for w in words) else 1, r["name"], r))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out[:limit]], len(out)


def denied():
    """{module.function: why} for every function execute_python / execute_editor_command refuse without really=true: each modal function (opens a dialog and hangs the bridge) and each DANGEROUS one."""
    out = {r["name"]: "opens a dialog or window and would hang the bridge until a person clicks it" for r in catalog() if r["modal"]}
    out.update(DANGEROUS)
    return out


def panes(refresh=False):
    """The editor's pane class names (general.get_pane_class_names): the real list of tools the editor offers (57 in this build). refresh=True asks the open editor and keeps the answer."""
    if refresh or not os.path.exists(PANES):
        import bridge_py
        reply = bridge_py.run_source("print(general.get_pane_class_names())")
        names = ast.literal_eval((reply.get("log") or "").strip().splitlines()[-1])
        os.makedirs(os.path.dirname(PANES), exist_ok=True)
        json.dump(names, open(PANES, "w", encoding="utf-8", newline=chr(10)), indent=0)
    return json.load(open(PANES, encoding="utf-8"))


def modules():
    counts = {}
    for r in catalog():
        m = counts.setdefault(r["module"], {"functions": 0, "used_by_the_project": 0, "read_only_no_argument": 0, "modal": 0})
        m["functions"] += 1
        m["used_by_the_project"] += 1 if r["project_calls"] else 0
        m["read_only_no_argument"] += 1 if r["read_only"] else 0
        m["modal"] += 1 if r["modal"] else 0
    return counts


# ---- the sweep: call the read-only, no-argument functions once, in the open editor ---------------------------------------------------------------

SWEEP_SOURCE = """
import json, time
names = %s
out = {}
for name in names:
    mod, fn = name.split('.', 1)
    t0 = time.time()
    try:
        value = getattr(globals()[mod], fn)()
        text = repr(value)
        out[name] = {'ok': True, 'type': type(value).__name__, 'sample': text[:240], 'length': len(text), 'seconds': round(time.time() - t0, 4)}
    except Exception as exc:
        out[name] = {'ok': False, 'type': type(exc).__name__, 'sample': str(exc)[:240], 'seconds': round(time.time() - t0, 4)}
open(%r, 'w').write(json.dumps(out))
print('swept ' + str(len(out)))
"""


def sweep(batch=30, out_path=SAMPLES):
    """Call every read-only no-argument function once (in batches, so no single call holds the editor's main thread for long) and keep what came back."""
    import bridge_py
    names = [r["name"] for r in catalog() if r["read_only"]]
    results = json.load(open(out_path, encoding="utf-8")) if os.path.exists(out_path) else {}
    tmp = os.path.join(os.environ.get("TEMP", HERE), "editor_api_sweep_part.json")
    for i in range(0, len(names), batch):
        part = names[i:i + batch]
        todo = [n for n in part if n not in results]
        if not todo:
            continue
        if os.path.exists(tmp):
            os.remove(tmp)
        reply = bridge_py.run_source(SWEEP_SOURCE % (json.dumps(todo), tmp.replace(chr(92), "/")), timeout=60)
        if not os.path.exists(tmp):
            raise RuntimeError("the editor wrote no sweep file for %s: %s" % (part[:3], str(reply)[:200]))
        results.update(json.load(open(tmp, encoding="utf-8")))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        json.dump(results, open(out_path, "w", encoding="utf-8", newline=chr(10)), indent=0)
        print("swept %d of %d" % (min(i + batch, len(names)), len(names)), flush=True)
    return results


CAPTURE_SOURCE = (
    "import json; mods=sorted(k for k,v in globals().items() if not k.startswith('_') "
    "and type(v).__name__=='module' and not k.startswith('Qt') and k not in ('os','sys','importlib','traceback','module','path_utils','json','io','P','SandboxBridge')); "
    "d={m:{f:str(getattr(getattr(globals()[m],f),'__doc__',None) or '') for f in dir(globals()[m]) if not f.startswith('_')} for m in mods}; "
    "d.pop('json', None); open(%r,'w').write(json.dumps(d,indent=1))")


def capture(out_path=DUMP):
    """Dump every function of every module of the editor's embedded Python (name -> its engine description) to editor_api.json. Needs the editor; takes a second."""
    import bridge_py
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    reply = bridge_py.run_source(CAPTURE_SOURCE % out_path.replace(chr(92), "/"), timeout=60)
    if not os.path.exists(out_path):
        raise RuntimeError("the editor wrote no dump: %s" % str(reply)[:300])
    d = json.load(open(out_path, encoding="utf-8"))
    return len(d), sum(len(v) for v in d.values() if isinstance(v, dict))


def main():
    args = sys.argv[1:]
    if "--capture" in args:
        modules, functions = capture()
        print("%d modules, %d functions written to %s" % (modules, functions, DUMP))
        return
    if "--sweep" in args:
        res = sweep()
        print("%d functions called, %d returned, %d raised" % (len(res), sum(1 for v in res.values() if v["ok"]), sum(1 for v in res.values() if not v["ok"])))
        return
    flags = {a for a in args if a.startswith("--")}
    module = args[args.index("--module") + 1] if "--module" in args else ""
    text = " ".join(a for a in args if not a.startswith("--") and a != module)
    rows, total = search(text, module, "--unused" in flags, "--readonly" in flags)
    for r in rows:
        print("%-44s (%s) -> %s   [%s%s calls %d]" % (r["name"], ", ".join("%s %s" % (t, n) for n, t in r["args"]), r["returns"], "MODAL " if r["modal"] else "", "readonly " if r["read_only"] else "", r["project_calls"]))
        if r["description"]:
            print("      " + r["description"][:150])
    print("%d of %d shown" % (len(rows), total))


if __name__ == "__main__":
    main()
