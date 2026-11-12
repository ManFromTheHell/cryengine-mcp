"""The ONE place that decides which CRYENGINE project the tools work on.

Every module used to say `PROJECT = os.path.dirname(HERE)` (= "the folder this server lives in"), so the tools could only ever serve the project that holds
the server folder. They now ask here, and the answer is, in this order:

  1. the environment variable CRYENGINE_PROJECT (a project folder or its Game.cryproject), e.g. set in the MCP server's config to pin a server to a project;
  2. the file  <repository root>/active_project.txt (one line: a project folder or Game.cryproject), written by `python bridge/project_root.py set <path>` or the `use_project` tool;
  3. the parent folder of the folder that holds server.py (the old behaviour).

A project is a folder that holds Game.cryproject. The result is cached per process: the tools import PROJECT once, so after changing the active project restart the MCP server
(the editor itself is a separate matter: it runs ONE project at a time, chosen by projects.json's startup project; `python bridge/project_root.py editor <path>` shows / sets that).

    python bridge/project_root.py            -> which project is active and why
    python bridge/project_root.py set <path> -> make <path> the active project (writes active_project.txt)
    python bridge/project_root.py clear      -> back to the default
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
ACTIVE_FILE = os.path.join(HERE, "active_project.txt")
PROJECTS_JSON = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Crytek", "CRYENGINE", "projects.json")


def _cryproject_in(folder):
    """the .cryproject file in a project folder: Game.cryproject if present, else the only *.cryproject there (the first, alphabetically, if several); None if there is none"""
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".cryproject") and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return None
    if "Game.cryproject" in names:
        return os.path.join(folder, "Game.cryproject")
    return os.path.join(folder, names[0]) if names else None


def _as_project(path):
    """a folder holding a .cryproject file, normally Game.cryproject (or the cryproject file itself) -> the folder; None if it is neither"""
    if not path:
        return None
    path = os.path.abspath(os.path.expandvars(path.strip().strip('"')))
    if os.path.isfile(path) and path.lower().endswith(".cryproject"):
        return os.path.dirname(path)
    if os.path.isdir(path) and _cryproject_in(path):
        return path
    return None


def resolve():
    """(project folder, why)"""
    env = os.environ.get("CRYENGINE_PROJECT", "")
    p = _as_project(env)
    if p:
        return p, "environment variable CRYENGINE_PROJECT"
    if os.path.isfile(ACTIVE_FILE):
        try:
            line = open(ACTIVE_FILE, encoding="utf-8").read().splitlines()[0]
        except (OSError, IndexError):
            line = ""
        p = _as_project(line)
        if p:
            return p, "active_project.txt"
    return os.path.dirname(HERE), "the folder of this server (default)"


PROJECT, WHY = resolve()
NAME = os.path.basename(PROJECT)
CRYPROJECT = _cryproject_in(PROJECT) or os.path.join(PROJECT, "Game.cryproject")
ASSETS = os.path.join(PROJECT, "Assets")


def editor_startup_project():
    """the project the Sandbox editor will open at start (projects.json: the entry with startupProject true), or None"""
    try:
        d = json.load(open(PROJECTS_JSON, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for p in d.get("projects", []) if isinstance(d, dict) else []:
        if p.get("startupProject"):
            return p.get("path") or p.get("file")
    return None


def projects_json_text(project=None):
    """the contents of projects.json that makes `project` (default: the active one) the startup project, so Sandbox opens it without the Project Browser. The engine fields are taken from
    the entry the engine already wrote for another project (or the startup snapshot), because they name the installed engine."""
    project = project or PROJECT
    template = None
    # CRYENGINE_PROJECTS_TEMPLATE: optional path of a projects.json-format file to copy the engine fields from when the Launcher's own projects.json has no entry yet
    for src in (PROJECTS_JSON, os.environ.get("CRYENGINE_PROJECTS_TEMPLATE", "")):
        if not src:
            continue
        try:
            d = json.load(open(src, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("projects"):
            template = dict(d["projects"][0])
            break
    if template is None:
        raise RuntimeError("no projects.json entry to copy the engine fields from: open any project once in the CRYENGINE Launcher "
                           "(or set CRYENGINE_PROJECTS_TEMPLATE to a projects.json-format file) so there is an entry to copy")
    try:
        info = json.load(open(_cryproject_in(project) or os.path.join(project, "Game.cryproject"), encoding="utf-8")).get("info", {})
    except (OSError, ValueError):
        info = {}
    template.update({"id": str(__import__("zlib").crc32(project.lower().encode("utf-8"))), "name": info.get("name") or os.path.basename(project), "path": project,
                     "fullPathToCryProject": (_cryproject_in(project) or os.path.join(project, "Game.cryproject")).replace("\\", "/"), "startupProject": True})
    return json.dumps({"version": 1, "projects": [template]}, indent="\t")


def write_projects_json(target=None):
    """make the active project the editor's startup project (what editor_open and the watchdog do before launching Sandbox)"""
    target = target or PROJECTS_JSON
    text = projects_json_text()
    # this REPLACES the Launcher's project list with one entry (the engine needs exactly that to start on the active project); keep the first original once
    try:
        if os.path.isfile(target) and not os.path.exists(target + ".mcp-backup"):
            import shutil
            shutil.copyfile(target, target + ".mcp-backup")
    except OSError:
        pass
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)


def _main(argv):
    if not argv:
        print("active project: %s\n  folder : %s\n  chosen by: %s" % (NAME, PROJECT, WHY))
        print("editor starts with: %s" % (editor_startup_project() or "(unknown: projects.json not readable)"))
        return 0
    if argv[0] == "set" and len(argv) > 1:
        p = _as_project(argv[1])
        if not p:
            print("not a project (no Game.cryproject there): %s" % argv[1])
            return 1
        open(ACTIVE_FILE, "w", encoding="utf-8").write(p + "\n")
        print("active project is now %s (restart the MCP server for it to take effect)" % p)
        return 0
    if argv[0] == "editor":
        write_projects_json()
        print("projects.json now starts the editor on %s" % PROJECT)
        return 0
    if argv[0] == "clear":
        if os.path.isfile(ACTIVE_FILE):
            os.remove(ACTIVE_FILE)
        print("cleared: the default is the folder of this server")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
