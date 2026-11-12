#!/usr/bin/env python3
"""A .cryproject file as the ENGINE reads it (ENGINE_LEARNING_PLAN L1), from the open editor for the engine's side, without running the game.

The engine parses the file with Serialization (CryCommon/CrySystem/IProjectManager.h: SProject, SProjectFileParser<4>): `version`, `type`, `info {name, guid}`, `content {assets[], code[], libs[]}`,
`require {engine, plugins[{guid, type, path, platforms[]}]}`, `console_variables{}`, `console_commands{}`. The plugin verb `bridge.project.info` writes what the running engine's project manager holds
(name, GUID, engine id, directories and every plug-in entry as it read it). This module compares the two and checks the rest against the engine's own data:

  P001 the file is not valid JSON or lacks a required section          P006 a plug-in's type is not one the parser knows (EType / EPluginType: Native, Managed)
  P002 version is older than the engine's (4): migrated on load         P007 a plug-in's DLL is not where the plug-in manager will look
  P003 info.guid is not a GUID                                          P008 a console variable is not in the engine's catalog, or has no effect in this spot
  P004 require.engine does not resolve in the launcher's engine list    P009 the engine's parse differs from the file (a plug-in dropped, a path read differently)
  P005 an assets / code directory does not exist                        P010 the same plug-in is listed twice

    python checks/cryproject_check.py [Game.cryproject ...]       default: every *.cryproject in the project folder
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "project_scratch")
# Engine install folder: env CRYENGINE_ENGINE_DIR or CRYENGINE_ENGINE (asset_lint and preflight use the first name), default the Launcher's 5.7.1 LTS folder.
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.environ.get("CRYENGINE_ENGINE") or (os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
ENGINE_REGISTRY = r"C:\ProgramData\Crytek\CRYENGINE\cryengine.json"
LATEST_VERSION = 4
TYPES = ("EType::Native", "EType::Managed", "EPluginType::Native", "EPluginType::Managed")
PLATFORMS = ("Windows", "Linux", "MacOS", "XboxOne", "PS4", "Android", "iOS")                  # EPlatform, in enum order (CryCore/Platform/platform.h)
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

sys.path.insert(0, HERE)


class ProjectError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def project_files():
    return sorted(f for f in os.listdir(PROJECT) if f.lower().endswith(".cryproject"))


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def engine_registry(path=ENGINE_REGISTRY):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def plugin_dll(path, engine=ENGINE, project=PROJECT):
    """Where a plug-in entry's DLL is looked for: a path that ends in .dll is relative to the project; a bare name is an engine plug-in under the engine's bin/win_x64 (Plugins\\... too)."""
    if path.lower().endswith(".dll"):
        return os.path.join(project, path.replace("/", os.sep))
    for sub in ("bin/win_x64", "bin/win_x64/Plugins", "bin/win_x64/EditorPlugins"):
        for name in (path, path + ".dll"):
            p = os.path.join(engine, sub.replace("/", os.sep), name if name.lower().endswith(".dll") else name + ".dll")
            if os.path.isfile(p):
                return p
    return os.path.join(engine, "bin", "win_x64", path + ".dll")


def check_file(path, data, engine_info=None, registry=None, cvar_get=None, engine=ENGINE, project=PROJECT):
    """The file's content (parsed JSON) and optionally the engine's parse -> [finding]."""
    where = os.path.basename(path)
    out = []
    for key in ("version", "info", "content", "require"):
        if key not in data:
            out.append(_finding("error", "P001", where, "the section '%s' is missing (the parser reads version, info, content, require, console_variables, console_commands)" % key))
    if out:
        return out
    if data["version"] < LATEST_VERSION:
        out.append(_finding("info", "P002", where, "version %d, the engine's is %d: the file is migrated when it is loaded (default plug-ins are added)" % (data["version"], LATEST_VERSION)))
    elif data["version"] > LATEST_VERSION:
        out.append(_finding("error", "P002", where, "version %d is newer than this engine's parser (%d)" % (data["version"], LATEST_VERSION)))
    info = data["info"]
    if not (info.get("name") or "").strip():
        out.append(_finding("error", "P001", where, "info.name is empty (it is the window title and the project's name)"))
    if not GUID.match(info.get("guid") or ""):
        out.append(_finding("error", "P003", where, "info.guid '%s' is not a GUID" % info.get("guid")))
    eid = (data["require"] or {}).get("engine") or ""
    if registry is not None and eid not in registry and not GUID.match(eid):
        out.append(_finding("error", "P004", where, "require.engine '%s' is not in the launcher's engine list (%s): the version selector cannot open the project" % (eid, ", ".join(registry) or "empty"), "use one of those ids"))
    for kind in ("assets", "code"):
        for d in (data["content"].get(kind) or []):
            if not os.path.isdir(os.path.join(project, d)):
                out.append(_finding("error" if kind == "assets" else "warning", "P005", where, "content.%s directory '%s' does not exist in the project" % (kind, d)))
    seen = set()
    for i, p in enumerate((data["require"].get("plugins") or [])):
        label = "%s plugins[%d] %s" % (where, i, p.get("path"))
        if p.get("type") not in TYPES:
            out.append(_finding("error", "P006", label, "type '%s' is not one the parser knows (%s): the entry is read with the default type" % (p.get("type"), ", ".join(TYPES))))
        for pl in p.get("platforms") or []:
            if pl not in PLATFORMS:
                out.append(_finding("error", "P006", label, "platform '%s' is not an EPlatform (%s)" % (pl, ", ".join(PLATFORMS))))
        key = (p.get("path") or "").lower()
        if key in seen:
            out.append(_finding("warning", "P010", label, "the plug-in is listed more than once"))
        seen.add(key)
        if not p.get("platforms") or "Windows" in p.get("platforms"):
            dll = plugin_dll(p.get("path") or "", engine, project)
            bare_without_engine = not (p.get("path") or "").lower().endswith(".dll") and not os.path.isdir(engine)
            if not os.path.isfile(dll) and not bare_without_engine:     # no engine folder found: a bare engine plug-in name cannot be checked, so no finding
                out.append(_finding("error" if (p.get("path") or "").lower().endswith(".dll") else "warning", "P007", label, "no DLL at %s" % dll, "build it, or fix the path"))
    if cvar_get is not None:
        for name, value in (data.get("console_variables") or {}).items():
            row = cvar_get(name)
            if row is None:
                out.append(_finding("warning", "P008", "%s console_variables.%s" % (where, name), "'%s' is not a variable in the engine's catalog (a typo is silence)" % name))
                continue
            if row.get("type") in ("int", "float") and not re.match(r"^-?\d+(\.\d+)?$", str(value).strip()):
                out.append(_finding("error", "P008", "%s console_variables.%s" % (where, name), "'%s' = '%s' is not a number (the variable is %s)" % (name, value, row["type"])))
            st = (row.get("settable") or {})
            if st.get("release") == "no":
                out.append(_finding("info", "P008", "%s console_variables.%s" % (where, name), "'%s' cannot be set in a Release build (%s)" % (name, row.get("flags"))))
    if engine_info:
        if engine_info.get("name") != info.get("name"):
            out.append(_finding("error", "P009", where, "the engine reads the project name as '%s', the file says '%s'" % (engine_info.get("name"), info.get("name"))))
        if (engine_info.get("guid") or "").lower().strip("{}") != (info.get("guid") or "").lower().strip("{}"):
            out.append(_finding("error", "P009", where, "the engine reads the GUID as %s, the file says %s" % (engine_info.get("guid"), info.get("guid"))))
        want = [(0 if (p.get("type") or "").endswith("Native") else 1, (p.get("path") or "")) for p in data["require"].get("plugins") or []]
        got = [(p["type"], p["path"]) for p in engine_info.get("plugins", [])]
        if want != got:
            out.append(_finding("error", "P009", where, "the engine holds %d plug-in entries and the file lists %d%s" % (len(got), len(want), "; first difference: file %s, engine %s" % next(((a, b) for a, b in zip(want, got) if a != b), ("-", "-")) if len(want) == len(got) else "")))
    return out


def check(paths=None, with_engine=True, with_cvars=True):
    """-> {ok, errors, warnings, findings, projects: [{file, name, plugins, console_variables}], engine}"""
    paths = [p for p in (paths or project_files())]
    registry = engine_registry()
    cvar_get = None
    if with_cvars:
        try:
            import cvar_catalog
            cvar_get = cvar_catalog.get
        except Exception:                                         # noqa: BLE001 - the catalog is optional
            cvar_get = None
    engine_info = None
    if with_engine:
        try:
            import bridge_py
            os.makedirs(SCRATCH, exist_ok=True)
            out = os.path.join(SCRATCH, "project_%d.json" % os.getpid())
            if os.path.exists(out):
                os.remove(out)
            r = bridge_py.send("bridge.project.info %s" % _slash(out), 30)
            if r.get("ok"):
                with open(out, encoding="utf-8") as f:
                    engine_info = json.load(f)
                os.remove(out)
        except Exception:                                         # noqa: BLE001 - no editor: the file checks still run
            engine_info = None
    findings, projects = [], []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(PROJECT, p)
        try:
            with open(full, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            findings.append(_finding("error", "P001", os.path.basename(p), "cannot be read as JSON: %s" % exc))
            projects.append({"file": os.path.basename(p), "readable": False})
            continue
        # the engine's parse belongs to the project that is OPEN: Game.cryproject only
        ours = engine_info if engine_info and os.path.normcase(full) == os.path.normcase(engine_info.get("file", "").replace("/", os.sep)) else None
        findings.extend(check_file(full, data, ours, registry, cvar_get))
        projects.append({"file": os.path.basename(p), "readable": True, "name": data.get("info", {}).get("name"), "plugins": len((data.get("require") or {}).get("plugins") or []),
                         "console_variables": sorted((data.get("console_variables") or {})), "engine_compared": ours is not None})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "projects": projects, "engine": engine_info}


def main():
    r = check(sys.argv[1:] or None)
    print("%s: %d error(s), %d warning(s)" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"]))
    for p in r["projects"]:
        print("  ", p)
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:260]))


if __name__ == "__main__":
    main()
