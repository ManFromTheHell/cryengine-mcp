#!/usr/bin/env python3
"""Which build of the editor made each catalog dump.

cvars.json, editor_commands.json and editor_api.json are dumps of the RUNNING editor (taken with the bridge plugin, the editor and the game loaded). A dump says nothing about which build made it, so after
a plugin or editor rebuild nothing warned that the catalogs describe the old one (an older editor_api.json keeps describing the build that made it). A STAMP is recorded next to the dump:

    {"plugin_sha256": <first 16 hex of the installed ClaudeBridgePlugin.dll>, "plugin_mtime", "sandbox_size", "sandbox_mtime", "engine_version": "5.7.1"}

and `status()` / `note(name)` compare it with the installed build. A catalog WITHOUT a stamp is judged by its file time against the newest of the plugin DLL and Sandbox.exe: older = the dump
describes an older build (warn), newer = made after the current build (probably current; stamp it).

    python catalogs/catalog_stamps.py                     status of every catalog
    python catalogs/catalog_stamps.py --stamp NAME ...    record the current build as the one that made NAME (only if the file is newer than the build; --force overrides)
    python catalogs/catalog_stamps.py --stamp-all

Run `--stamp` right after re-capturing a catalog (editor_api.py --sweep, editor_commands.py --capture, the cvar dump). The stamps live in engine_data/catalog_stamps.json; nothing else is touched.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import hashlib
import json
import os
import sys
import time

HERE = _paths.ROOT
STAMPS = os.path.join(HERE, "engine_data", "catalog_stamps.json")
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")
CATALOGS = {"cvars.json": os.path.join(HERE, "engine_data", "cvars.json"),
            "editor_commands.json": os.path.join(HERE, "engine_data", "editor_commands.json"),
            "editor_api.json": os.path.join(HERE, "editor_api.json")}

_sha_cache = {}


def _plugin_path(engine=None):
    # CRYENGINE_SOURCE: an engine source checkout that holds a locally built plugin (optional)
    for root in [r for r in (engine or ENGINE, _paths.ENGINE_SOURCE) if r]:
        p = os.path.join(root, "bin", "win_x64", "EditorPlugins", "ClaudeBridgePlugin.dll")
        if os.path.isfile(p):
            return p
    return None


def _sha16(path):
    key = (path, os.path.getmtime(path), os.path.getsize(path))
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()[:16]
    return _sha_cache[key]


def engine_version(engine=None):
    try:
        return json.load(open(os.path.join(engine or ENGINE, "cryengine.cryengine"), encoding="utf-8"))["info"]["version"]
    except (OSError, ValueError, KeyError):
        return None


def current_stamp(engine=None):
    """The installed build: plugin hash and file time, Sandbox.exe size and file time, engine version. Missing pieces are None."""
    engine = engine or ENGINE
    plugin, sandbox = _plugin_path(engine), os.path.join(engine, "bin", "win_x64", "Sandbox.exe")
    return {"plugin_sha256": _sha16(plugin) if plugin else None, "plugin_mtime": round(os.path.getmtime(plugin)) if plugin else None,
            "sandbox_size": os.path.getsize(sandbox) if os.path.isfile(sandbox) else None, "sandbox_mtime": round(os.path.getmtime(sandbox)) if os.path.isfile(sandbox) else None,
            "engine_version": engine_version(engine)}


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def compare(stamp, now):
    """-> list of differences between a recorded stamp and the installed build (empty = same build)."""
    out = []
    for key, label in (("plugin_sha256", "ClaudeBridge plugin"), ("sandbox_size", "Sandbox.exe size"), ("sandbox_mtime", "Sandbox.exe build"), ("engine_version", "engine version")):
        if stamp.get(key) != now.get(key):
            out.append("%s: stamped %s, installed %s" % (label, stamp.get(key), now.get(key)))
    return out


def status(names=None, stamps_path=STAMPS, paths=None, engine=None):
    """-> {name: {state: ok | unstamped-current | unstamped-old | stale | missing, detail}} for each catalog."""
    paths = paths or CATALOGS
    now = current_stamp(engine)
    stamps = _load(stamps_path)
    built = max([t for t in (now.get("plugin_mtime"), now.get("sandbox_mtime")) if t] or [0])
    out = {}
    for name in names or list(paths):
        path = paths.get(name)
        if not path or not os.path.isfile(path):
            out[name] = {"state": "missing", "detail": "no such catalog file"}
            continue
        rec = stamps.get(name)
        if rec:
            diff = compare(rec["stamp"], now)
            out[name] = ({"state": "ok", "detail": "made with the installed build (stamped %s)" % rec.get("recorded")} if not diff else
                         {"state": "stale", "detail": "made by a different build: " + "; ".join(diff) + ". Re-capture it, then `python catalogs/catalog_stamps.py --stamp %s`" % name})
        else:
            mt = os.path.getmtime(path)
            if built and mt < built:
                out[name] = {"state": "unstamped-old", "detail": "no stamp, and the file (%s) is older than the installed plugin/editor build (%s): it may describe an older build. Re-capture it, then `python catalogs/catalog_stamps.py --stamp %s`" % (_day(mt), _day(built), name)}
            else:
                out[name] = {"state": "unstamped-current", "detail": "no stamp, but the file (%s) is newer than the installed build (%s): probably current. `python catalogs/catalog_stamps.py --stamp %s` records it" % (_day(mt), _day(built) if built else "?", name)}
    return out


def note(name, **kw):
    """A one-sentence warning for a tool that serves catalog `name`, or None when the catalog is stamped current / probably current. Never raises."""
    try:
        st = status([name], **kw).get(name)
    except Exception:                                           # noqa: BLE001 - a stamp problem must never break a catalog tool
        return None
    if st and st["state"] in ("stale", "unstamped-old"):
        return "catalog %s: %s" % (name, st["detail"])
    return None


def stamp(names, force=False, stamps_path=STAMPS, paths=None, engine=None):
    """Record the installed build as the one that made `names`. Refuses a file older than the build unless force (stamping an old dump would make the warning lie)."""
    paths = paths or CATALOGS
    now = current_stamp(engine)
    built = max([t for t in (now.get("plugin_mtime"), now.get("sandbox_mtime")) if t] or [0])
    stamps = _load(stamps_path)
    done, refused = [], []
    for name in names:
        path = paths.get(name)
        if not path or not os.path.isfile(path):
            refused.append("%s: no such catalog" % name)
        elif os.path.getmtime(path) < built and not force:
            refused.append("%s: the file (%s) is older than the installed build (%s): re-capture it first, or --force" % (name, _day(os.path.getmtime(path)), _day(built)))
        else:
            stamps[name] = {"stamp": now, "recorded": time.strftime("%Y-%m-%d %H:%M"), "file_mtime": round(os.path.getmtime(path))}
            done.append(name)
    if done:
        os.makedirs(os.path.dirname(stamps_path), exist_ok=True)
        tmp = stamps_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(stamps, f, indent=1)
        os.replace(tmp, stamps_path)
    return {"stamped": done, "refused": refused}


def main(argv):
    force = "--force" in argv
    names = [a for a in argv if not a.startswith("--")]
    if "--stamp-all" in argv:
        names = list(CATALOGS)
    if "--stamp" in argv or "--stamp-all" in argv:
        res = stamp(names, force)
        print(json.dumps(res, indent=1))
        return 1 if res["refused"] else 0
    for name, st in status().items():
        print("%-22s %-18s %s" % (name, st["state"], st["detail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
