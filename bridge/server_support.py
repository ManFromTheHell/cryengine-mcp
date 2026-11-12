#!/usr/bin/env python3
"""Support for server.py that needs neither MCP nor (for the logic) a running editor, so it is testable offline (test_server_support.py).

  * BOUNDED OUTPUT: `install(mcp)` wraps every registered tool. A result over ~20,000 characters is written to job_runs/out/<id>.json and the tool returns a SUMMARY (small values kept, big
    ones described: type, count, first item), the counts and the path; `read_output(id, key, offset, limit)` pages through the saved file. Results with pictures are left alone.
  * THREADS: a plain-`def` tool is run with asyncio.to_thread, so a slow bridge call no longer freezes every other tool.
  * RECOVERY REPORT: when this process brought the editor back during a call, the result says `editor_relaunched: true`.
  * DANGER DENY-LIST: `check_python`, `check_editor_command`, `check_console` name why a call needs really=true (editor exit, plugin unload, Lua, every modal dialog function).
  * `server_info`: start time and the modules edited since (the running server keeps the OLD code until the MCP connector is restarted).
  * `load_level_logic`, `export_logic`: the tools' logic with the editor behind an object, so tests can fake it.
  * `verify_persisted`: did a component property reach the saved layer file?
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import asyncio
import base64
import functools
import inspect
import json
import os
import re
import sys
import time
import uuid

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
OUT_DIR = os.path.join(HERE, "job_runs", "out")
OUTPUT_BUDGET = 20000
BUDGET_OVERRIDE = {"tool_guide": 40000, "read_output": 10 ** 9}      # the map of all tools must be seen whole; read_output limits itself
NOT_THREADED = {"tool_guide", "server_info", "read_output", "plan_character_collider", "texture_recipes", "job_kinds"}

SERVER_START = None
_SNAPSHOT = {}


class OutputError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------------------------------------------------- bounded output
_ID_RE = re.compile(r"^out_\d{8}_\d{6}_[A-Za-z0-9_]+_[0-9a-f]{6}$")


def json_size(obj):
    """The size of what the client receives for a dict/list result (FastMCP renders it with indent=2)."""
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str, indent=2))
    except (TypeError, ValueError):
        return 0


def store_output(tool, result, out_dir=None):
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    oid = "out_%s_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), re.sub(r"[^A-Za-z0-9_]", "", tool)[:30] or "tool", uuid.uuid4().hex[:6])
    path = os.path.join(out_dir, oid + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"tool": tool, "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "result": result}, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return oid, path


def _clip(text, n):
    return text if len(text) <= n else text[:n] + "... [%d chars]" % len(text)


def describe(v, keep=600):
    """A small stand-in for a big value: scalars and small values as they are; a list as {count, first}; a dict as {count, keys}; a string clipped."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return _clip(v, 300)
    if json_size(v) <= keep:
        return v
    if isinstance(v, list):
        return {"_type": "list", "count": len(v), "first": describe(v[0], 200) if v else None}
    if isinstance(v, dict):
        scalars = {k: (x if not isinstance(x, str) else _clip(x, 80)) for k, x in v.items() if x is None or isinstance(x, (bool, int, float, str))}
        scalars = dict(list(scalars.items())[:6])
        return {"_type": "dict", "count": len(v), "scalars": scalars, "other_keys": [k for k in v if k not in scalars][:25]}
    return _clip(str(v), 200)


def bound(tool, result, budget=None, out_dir=None):
    """The result as it is when small; when over the budget a stand-in of the SAME kind (dict -> dict, list -> [dict], str -> str) that names the saved file."""
    budget = BUDGET_OVERRIDE.get(tool, budget or OUTPUT_BUDGET)
    if isinstance(result, str):
        if len(result) <= budget:
            return result
        oid, path = store_output(tool, {"text": result}, out_dir)
        return (result[:3000] + "\n... [output truncated: %d chars in all. Saved to %s. read_output(id='%s', key='text', offset=0, limit=100) pages it by lines]"
                % (len(result), path, oid))
    if isinstance(result, list):
        if any(type(x).__name__ in ("Image", "ImageContent") for x in result):
            return result
    elif not isinstance(result, dict):
        return result
    size = json_size(result)
    if size <= budget:
        return result
    oid, path = store_output(tool, result, out_dir)
    meta = {"output_truncated": True, "output_id": oid, "chars": size, "path": path,
            "read_with": "read_output(id='%s', key='<name or index>', offset=0, limit=50)" % oid}
    if isinstance(result, list):
        meta["count"] = len(result)
        return [{"_output": meta, "first_items": [describe(x, 300) for x in result[:3]]}]
    counts = {k: len(v) for k, v in result.items() if isinstance(v, (list, dict))}
    summary, used, omitted = {}, 0, []
    for k, v in result.items():
        d = describe(v)
        n = json_size(d) + len(str(k))
        if used + n > 8000:
            omitted.append(k)
            continue
        summary[k] = d
        used += n
    meta["counts"] = counts
    if omitted:
        meta["omitted_keys"] = omitted[:60]
    summary["_output"] = meta
    return summary


def _walk(node, key):
    while key:
        if isinstance(node, dict):
            if key in node:
                return node[key]
            head, _, rest = key.partition(".")
            if head not in node:
                raise OutputError("no key %r here; the keys are: %s" % (head, ", ".join(map(str, list(node)[:40]))))
            node, key = node[head], rest
        elif isinstance(node, list):
            head, _, rest = key.partition(".")
            try:
                i = int(head)
            except ValueError:
                raise OutputError("this is a list of %d items: the key must be an index, not %r" % (len(node), head))
            if not -len(node) <= i < len(node):
                raise OutputError("index %d is outside the list of %d items" % (i, len(node)))
            node, key = node[i], rest
        else:
            raise OutputError("%r goes below a plain value" % key)
    return node


def read_output(output_id, key="", offset=0, limit=50, out_dir=None, budget=18000):
    """One page of a saved tool result. key: a path into it ('items', 'items.3', 'rows.0.name'). A list/dict is paged by items/keys, text by lines. -> {id, key, type, total, offset, returned, next_offset, items|text|value}"""
    if not _ID_RE.match(output_id or ""):
        raise OutputError("not an output id: %r (they look like out_20261023_101500_list_objects_ab12cd and come from a truncated tool result)" % (output_id,))
    path = os.path.join(out_dir or OUT_DIR, output_id + ".json")
    if not os.path.isfile(path):
        raise OutputError("no saved output %s (saved results are in %s)" % (output_id, out_dir or OUT_DIR))
    if offset < 0 or not 1 <= limit <= 1000:
        raise OutputError("offset must be 0 or more and limit 1 to 1000")
    saved = json.load(open(path, encoding="utf-8"))
    node = _walk(saved["result"], key or "")
    head = {"id": output_id, "key": key, "tool": saved.get("tool")}
    if isinstance(node, str) and len(node.splitlines()) <= 1 and len(node) <= budget:
        return dict(head, type="text", value=node)
    if isinstance(node, str):
        lines = node.splitlines()
        page = lines[offset:offset + limit]
        text = "\n".join(page)
        clipped = len(text) > budget
        if clipped:
            text = text[:budget]
        nxt = offset + len(page)
        return dict(head, type="text", total=len(lines), offset=offset, returned=len(page), next_offset=nxt if nxt < len(lines) else None, clipped=clipped, text=text)
    if isinstance(node, list):
        total, items = len(node), node[offset:offset + limit]
    elif isinstance(node, dict):
        keys = list(node)
        total, items = len(keys), {k: node[k] for k in keys[offset:offset + limit]}
    else:
        return dict(head, type=type(node).__name__, value=node)
    clipped = False
    while json_size(items) > budget and len(items) > 1:
        if isinstance(items, list):
            items = items[:-max(1, len(items) // 4)]
        else:
            items = {k: items[k] for k in list(items)[:-max(1, len(items) // 4)]}
    if json_size(items) > budget:                                   # one item alone is over the page: describe it, the caller goes one key deeper
        clipped = True
        items = [describe(items[0], 400)] if isinstance(items, list) else {k: describe(v, 400) for k, v in items.items()}
    nxt = offset + len(items)
    return dict(head, type="list" if isinstance(node, list) else "dict", total=total, offset=offset, returned=len(items), next_offset=nxt if nxt < total else None, clipped=clipped, items=items)


# ----------------------------------------------------------------------------------------------------------------------------------------- recovery report
def attach_recovery(result, since):
    """If this process brought the editor back since `since`, say so in the result (a dict gets keys; a list gets a last element; a string gets a last line)."""
    try:
        import bridge_py
        events = bridge_py.recoveries_since(since)
    except Exception:                                               # noqa: BLE001
        return result
    if not events:
        return result
    info = {"editor_relaunched": True, "editor_recovery": [{k: e[k] for k in ("action", "seconds")} for e in events]}
    if isinstance(result, dict):
        return dict(result, **info)
    if isinstance(result, list):
        return list(result) + [info]
    if isinstance(result, str):
        return result + "\n[editor_relaunched: true -- %s]" % events[-1].get("action")
    return result


# ----------------------------------------------------------------------------------------------------------------------------------------- paging
def page(rows, limit=100, offset=0, cap=None, total=None):
    """A list tool's answer with its limits stated. `cap`: the most the bridge ever returns (200 entities, 500 objects); `total`: the real count when it is known. -> {items, shown, offset, limit,
    total, total_is_lower_bound, truncated, next_offset, note}. truncated is true whenever there is more than `items` (this page ends early, or the bridge's own cap cut the list)."""
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError("offset must be 0 or more and limit 1 to 500")
    items = rows[offset:offset + limit]
    capped = cap is not None and len(rows) >= cap
    known = total if total is not None else len(rows)
    lower_bound = capped and total is None
    more_after = offset + len(items) < len(rows)
    truncated = more_after or (capped and (total is None or total > len(rows)))
    note = ""
    if capped and (total is None or total > len(rows)):
        note = "the bridge returns at most %d; narrow the name filter to see the rest" % cap
    if more_after:
        note = (note + "; " if note else "") + "more on the next page: offset=%d" % (offset + len(items))
    return {"items": items, "shown": len(items), "offset": offset, "limit": limit, "total": known, "total_is_lower_bound": lower_bound, "truncated": truncated,
            "next_offset": offset + len(items) if more_after else None, "note": note}


# ----------------------------------------------------------------------------------------------------------------------------------------- install
RESULT_NOTES = {"editor_api": "editor_api.json", "cvar_search": "cvars.json", "cvar_info": "cvars.json", "editor_commands": "editor_commands.json"}      # tools that serve a stamped catalog


def attach_catalog_note(name, result):
    cat = RESULT_NOTES.get(name)
    if not cat or not isinstance(result, dict):
        return result
    try:
        import catalog_stamps
        w = catalog_stamps.note(cat)
    except Exception:                                               # noqa: BLE001
        w = None
    return dict(result, catalog_warning=w) if w else result


def _wrap(tool, budget):
    fn, name = tool.fn, tool.name

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            t0 = time.time()
            return attach_recovery(attach_catalog_note(name, bound(name, await fn(*a, **kw), budget)), t0)
    elif name in NOT_THREADED:
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            t0 = time.time()
            return attach_recovery(attach_catalog_note(name, bound(name, fn(*a, **kw), budget)), t0)
    else:
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            t0 = time.time()
            return attach_recovery(attach_catalog_note(name, bound(name, await asyncio.to_thread(fn, *a, **kw), budget)), t0)
        tool.is_async = True
    tool.fn = wrapper


def install(mcp, budget=OUTPUT_BUDGET):
    """Wrap every registered tool (bounded output, a worker thread for blocking ones, the relaunch report) and remember which modules were loaded when the server started.
    Uses FastMCP's private tool manager (tested with mcp 1.x; requirements.txt pins the range): raises if it finds no tools."""
    tools = list(mcp._tool_manager.list_tools())
    if not tools:
        raise RuntimeError("server_support.install: found no registered tools (did the mcp package change its internals?)")
    for tool in tools:
        _wrap(tool, budget)
    mark_start()
    snapshot_modules()


# ----------------------------------------------------------------------------------------------------------------------------------------- danger deny-list
_REF = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\b")
_PY_PATTERNS = [(re.compile(r"\bos\s*\.\s*(system|popen|remove|unlink|rmdir|removedirs|startfile|_exit|kill|killpg)\b"), "runs a program or deletes files"),
                (re.compile(r"\bsubprocess\b"), "runs a program"), (re.compile(r"\bshutil\s*\.\s*(rmtree|move)\b"), "deletes or moves files"),
                (re.compile(r"\bsys\s*\.\s*exit\b|(?<![\w.])(exit|quit)\s*\("), "ends the interpreter / the editor"),
                (re.compile(r"\bgetattr\s*\(\s*(general|keybind)\b"), "looks a function up by name, which hides a dangerous one from this check")]
_CONSOLE_DANGER = {"quit": "quits the editor", "exit": "quits the editor", "bridge.window.close": "closes a window of the editor (the editor itself when no title is given)"}


def _denied():
    import editor_api
    return editor_api.denied()


def _why(name, why):
    return "%s (%s)" % (name, why)


def check_python(code):
    """Best-effort guard against ACCIDENTS, not a sandbox: execute_python runs arbitrary Python inside the editor, and a determined script can bypass these text patterns.
    -> [reasons] a script needs really=true: it names a dangerous or modal editor function, or runs programs / deletes files."""
    den, hits = _denied(), []
    for m in _REF.finditer(code or ""):
        name = "%s.%s" % m.groups()
        if name in den and _why(name, den[name]) not in hits:
            hits.append(_why(name, den[name]))
    for rx, why in _PY_PATTERNS:
        mt = rx.search(code or "")
        if mt:
            hits.append(_why(mt.group(0).strip(), why))
    return hits


def check_editor_command(cmd):
    """-> [reasons] for an ICommandManager command line ('general.exit', 'general.open_level levels/x' ...)."""
    parts = (cmd or "").strip().split()
    if not parts:
        return []
    name, den = parts[0].lower(), _denied()
    return [_why(name, den[name])] if name in den else []


def check_console(cmd):
    """-> [reasons] for a run_console_command line: quit/exit, bridge.window.close, and the bridge.execute_* passthroughs (the command or code inside is checked too)."""
    text = (cmd or "").strip()
    parts = text.split(None, 1)
    if not parts:
        return []
    verb, rest = parts[0].lower(), parts[1] if len(parts) > 1 else ""
    if verb in _CONSOLE_DANGER:
        return [_why(verb, _CONSOLE_DANGER[verb])]
    if verb == "bridge.execute_command":
        return check_editor_command(rest)
    if verb == "bridge.execute_python":
        return check_python(rest)
    if verb == "bridge.execute_python.b64":
        try:
            return check_python(base64.b64decode(rest.strip()).decode("utf-8", "replace"))
        except ValueError:
            return ["bridge.execute_python.b64 with a payload that is not base64"]
    return []


def refuse_danger(reasons, what):
    return {"ok": False, "refused": "%s needs really=true: it would call %s. These end the editor, unload the bridge, run files or Lua, or open a dialog that blocks the bridge until a person clicks it. "
                                     "Look for a safe equivalent first (editor_api, tool_guide); pass really=true only when that is what is wanted." % (what, "; ".join(reasons))}


# ----------------------------------------------------------------------------------------------------------------------------------------- server_info
def mark_start():
    global SERVER_START
    if SERVER_START is None:
        SERVER_START = time.time()


def _module_files():
    out = {}
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if f and f.endswith(".py"):
            f = os.path.abspath(f)
            if f.startswith(HERE + os.sep):
                out[f] = name
    return out


def snapshot_modules():
    """The modules loaded at start, with the file time they were loaded at."""
    _SNAPSHOT.clear()
    for f, name in _module_files().items():
        try:
            _SNAPSHOT[f] = (name, os.path.getmtime(f))
        except OSError:
            pass


def stale_report(start=None, snapshot=None):
    """-> None when this process is not the server; else {started, stale: [module files edited after start], lazy: [modules imported later and edited after start], restart_note}."""
    start = start if start is not None else SERVER_START
    snap = _SNAPSHOT if snapshot is None else snapshot
    if start is None:
        return None
    stale, lazy = [], []
    for f, (name, _t) in sorted(snap.items()):
        try:
            if os.path.getmtime(f) > start:
                stale.append(os.path.relpath(f, HERE).replace("\\", "/"))
        except OSError:
            continue
    if snapshot is None:
        for f, name in _module_files().items():
            if f not in snap:
                try:
                    if os.path.getmtime(f) > start:
                        lazy.append(os.path.relpath(f, HERE).replace("\\", "/"))
                except OSError:
                    pass
    note = ("restart the MCP connector to load: " + ", ".join(stale)) if stale else ""
    return {"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)), "uptime_s": round(time.time() - start), "stale": stale, "lazy_maybe_stale": sorted(lazy), "restart_note": note}


def server_info():
    rep = stale_report() or {}
    info = {"pid": os.getpid(), "python": sys.version.split()[0], "file": _paths.script("server.py"), "auto_recover": os.environ.get("CLAUDE_AUTO_RECOVER") == "1", "modules_loaded": len(_SNAPSHOT)}
    info.update(rep)
    try:
        import bridge_py
        info["editor_closed_on_purpose"] = bridge_py.closed_on_purpose()
        info["editor_relaunches_by_this_server"] = len([e for e in bridge_py.RECOVERY_EVENTS if e["state"] == "recovered"])
    except Exception:                                               # noqa: BLE001
        pass
    try:
        import catalog_stamps
        info["catalogs"] = {k: v["state"] for k, v in catalog_stamps.status().items()}
    except Exception:                                               # noqa: BLE001
        pass
    info["output_dir"] = OUT_DIR
    info["saved_outputs"] = len([f for f in os.listdir(OUT_DIR) if f.endswith(".json")]) if os.path.isdir(OUT_DIR) else 0
    return info


# ----------------------------------------------------------------------------------------------------------------------------------------- load_level
LEVEL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}$")


def _count_warnings(log):
    rows = [l.strip() for l in (log or "").splitlines() if re.search(r"\bwarning\b", l, re.I)]
    return len(rows), rows[:5]


def load_level_logic(level, save_current, io, wait_s=180.0):
    """Open levels/<level>/<level>.level in the editor and WAIT for it. `io` provides: level_exists(name) -> bool, levels() -> [names], current() -> {name, objects}, save() -> {saved, ...},
    send(cmd, timeout) -> response (dict, may raise), dialogs() -> [{title, buttons}], sleep(s), clock().
    -> {ok, level, seconds, n_warnings, ...}; a refusal names the next step (a failed save, an open dialog, a level that does not exist)."""
    if not LEVEL_NAME.match(level or ""):
        return {"ok": False, "refused": "level name %r: letters, digits and _, starting with a letter (it is the folder under Assets/levels)" % (level,)}
    if not io.level_exists(level):
        return {"ok": False, "refused": "no level file Assets/levels/%s/%s.level. The levels there are: %s" % (level, level, ", ".join(io.levels()) or "none")}
    t0 = io.clock()
    before = io.current()
    out = {"ok": True, "level": level, "was": before.get("name")}
    if before.get("name") == level:
        return dict(out, seconds=0.0, n_warnings=0, already_open=True, note="that level is already open; nothing was loaded (reloading would drop unsaved work)")
    if save_current and before.get("name"):
        try:
            saved = io.save()
        except Exception as exc:                                    # noqa: BLE001
            saved = {"saved": False, "error": str(exc)[:200]}
        out["saved_previous"] = saved
        if not saved.get("saved"):
            return {"ok": False, "level": level, "was": before.get("name"), "save_failed": saved,
                    "refused": "saving the open level %r failed, so it was NOT left: leaving a modified level opens a blocking 'Save Level?' prompt and discards work. Check list_dialogs, "
                               "save by hand (save_level), or pass save_current=false to discard the changes" % before.get("name")}
    warnings = []
    try:
        resp = io.send("bridge.execute_command asset.edit levels/%s/%s.level" % (level, level), 240)
        log = ""
        try:
            log = json.loads(resp.get("output") or "{}").get("log", "")
        except (ValueError, AttributeError):
            log = str(resp.get("output") or "")
        n, warnings = _count_warnings(log)
        out["n_warnings"], out["warnings"] = n, warnings
    except Exception as exc:                                        # noqa: BLE001 - usually the main thread stuck behind a dialog
        out["n_warnings"], out["warnings"] = 0, []
        out["command_error"] = str(exc)[:300]
    deadline = io.clock() + wait_s
    while True:
        dlg = io.dialogs()
        if dlg:
            return dict(out, ok=False, level=level, seconds=round(io.clock() - t0, 1), dialog_open=dlg,
                        next="answer it with click_dialog_button (a 'Save Level?' prompt: 'no' discards, 'yes' saves), then wait_for_editor")
        try:
            cur = io.current()
        except Exception:                                           # noqa: BLE001 - busy loading
            cur = {}
        if cur.get("name") == level:
            return dict(out, ok=True, level=level, seconds=round(io.clock() - t0, 1), objects=cur.get("objects"))
        if io.clock() > deadline:
            return dict(out, ok=False, level=level, seconds=round(io.clock() - t0, 1), now_open=cur.get("name"),
                        refused="the editor has %r open %.0f s after asking for %r. It may still be loading: wait_for_editor, then check; list_dialogs if nothing changes" % (cur.get("name"), wait_s, level))
        io.sleep(1.0)


# ----------------------------------------------------------------------------------------------------------------------------------------- export_level
def export_logic(io, wait_s=120.0, answer=True, max_attempts=3):
    """exporter.export_to_engine the way export_level.py does it: ai.regenerate_ignored first, then the export on a thread while the open dialogs are read and (answer=True) answered with their DEFAULT button
    (ONLY 'Navigation ...' dialogs are answered; any other dialog is left open and reported as unexpected_dialog. 'Navigation System Warning': Cancel is the default of 'still processing' so stale data is not exported; Yes of 'pending changes' applies them), retried while the navigation is still processing.
    `io`: send(cmd, timeout), start(fn) -> handle with .alive()/.join(t) , dialogs() -> [{title, buttons}], click_default(title), sleep, clock.
    -> {ok, attempts, dialogs_seen, answered, output}"""
    seen, answered, last = [], [], ""
    t0 = io.clock()
    attempts = 0
    while attempts < max_attempts and io.clock() - t0 < wait_s:
        attempts += 1
        try:
            io.send("bridge.execute_command ai.regenerate_ignored", 60)
        except Exception as exc:                                    # noqa: BLE001
            last = "ai.regenerate_ignored failed: %s" % str(exc)[:200]
        res = {}

        def run():
            try:
                res["r"] = io.send("bridge.execute_command exporter.export_to_engine", max(30, wait_s))
            except Exception as exc:                                # noqa: BLE001
                res["r"] = {"error": repr(exc)}
        handle = io.start(run)
        blocked = False
        unexpected = None
        while handle.alive() and io.clock() - t0 < wait_s + 30:
            io.sleep(1.5)
            try:
                dlgs = io.dialogs()
            except Exception:                                       # noqa: BLE001
                continue
            for d in dlgs:
                title = d.get("title", "")
                if title and title not in [x["title"] for x in seen]:
                    seen.append({"title": title, "buttons": d.get("buttons", [])})
                if "Navigation" in title:
                    blocked = True
                if answer and title and "Navigation" not in title:
                    unexpected = unexpected or title                # only the navigation question is answered by default; anything else is left for the caller
                elif answer and title:
                    try:
                        io.click_default(title)
                        answered.append(title)
                    except Exception as exc:                        # noqa: BLE001
                        last = "could not answer %r: %s" % (title, str(exc)[:100])
                    io.sleep(1.0)
            if unexpected or (not answer and dlgs):
                break
        handle.join(5)
        last = str(res.get("r", last))
        if handle.alive() and unexpected:
            return {"ok": False, "attempts": attempts, "still_running": True, "unexpected_dialog": unexpected, "dialogs_seen": seen, "answered": answered,
                    "next": "an unexpected dialog (%r) is open and was NOT answered: list_dialogs / click_dialog_button, then export_level again" % unexpected}
        if handle.alive():
            return {"ok": False, "attempts": attempts, "still_running": True, "dialogs_seen": seen, "answered": answered,
                    "next": "the export is still running or blocked by a dialog: list_dialogs / click_dialog_button, then export_level again"}
        if not blocked and "successful" in last.lower():
            return {"ok": True, "attempts": attempts, "dialogs_seen": seen, "answered": answered, "output": last[-200:]}
        if not blocked and not answer:
            break
        io.sleep(5.0)
    return {"ok": False, "attempts": attempts, "dialogs_seen": seen, "answered": answered, "output": last[-300:],
            "next": "navigation was still processing or the export reported no success: wait, ai.regenerate_ignored, export again; list_dialogs shows anything open"}


# ----------------------------------------------------------------------------------------------------------------------------------------- component_properties persist
def verify_persisted(entities, entity, component, requested):
    """After the layer was saved: does the layer file hold the requested values? `entities`: entity_check.read_layers() rows. -> {ok, checked, missing, mismatched, where}"""
    import entity_check
    rows = [e for e in entities if e["name"] == entity]
    if not rows:
        return {"ok": False, "checked": 0, "problem": "no entity named %r in the level's layer files (is it a Lua/legacy entity, or in an unsaved layer?)" % entity}
    comp = [c for c in rows[0]["components"] if c["type"].lower() == component.split("#")[0].lower()]
    if not comp:
        return {"ok": False, "checked": 0, "problem": "the layer file %s lists no %s on %s (it lists: %s)" % (rows[0]["file"], component, entity, ", ".join(c["type"] for c in rows[0]["components"]) or "none")}
    props = comp[0]["props"]
    if props is None:
        return {"ok": False, "checked": 0, "problem": "the component has no <properties> element in the layer file"}
    missing, mismatched, checked = [], [], 0

    def walk(elem, want, path):
        nonlocal checked
        for k, v in want.items():
            label = "%s.%s" % (path, k) if path else k
            if isinstance(v, dict):
                child = elem.find(k) if elem is not None else None
                if child is None:
                    missing.append(label)
                else:
                    walk(child, v, label)
                continue
            checked += 1
            if elem is None or k not in elem.attrib:
                missing.append(label)
            elif not entity_check.same_value(elem.attrib[k], v if isinstance(v, (bool, int, float)) else str(v)):
                mismatched.append("%s: file %r, asked %r" % (label, elem.attrib[k], v))
    walk(props, requested, "")
    return {"ok": not missing and not mismatched, "checked": checked, "missing": missing, "mismatched": mismatched, "where": rows[0]["file"]}
