#!/usr/bin/env python3
"""Start Sandbox (if it is not running) and open a level, waiting until the level is really loaded.

    editor_open.py [level]          (default: env CRYENGINE_DEFAULT_LEVEL, else ashvale)

- The project opens without the Project Browser because projects.json gets the startup copy
  (written by project_root.write_projects_json; the original is kept once as projects.json.mcp-backup).
- The bridge answers before the level system is ready, so this polls with a real Python call.
- The level is opened with `asset.edit levels/<l>/<l>.level`, the Asset Browser's way (and MCP load_level's).
  `general.open_level` can return ok and load nothing.
- "Loaded" means general.get_current_level_name() names it.
Exit code 0 when the level is open.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import socket
import subprocess
import sys
import time

# Engine install folder: env CRYENGINE_ENGINE (same variable as asset_meta / engine_tests / cryproject_check), default the Launcher's 5.7.1 LTS folder.
ENGINE = os.environ.get("CRYENGINE_ENGINE", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
SANDBOX = os.path.join(ENGINE, "bin", "win_x64", "Sandbox.exe")
PORT = int(os.environ.get("CRYENGINE_BRIDGE_PORT", "37778"))
PROJECT = __import__("project_root").PROJECT
PROJECTS = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Crytek", "CRYENGINE", "projects.json")


def bridge(cmd, timeout=30):
    with socket.create_connection(("127.0.0.1", PORT), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def level_name():
    try:
        r = bridge("bridge.execute_python import sandbox.general as g; print(g.get_current_level_name())", 20)
        return str(r.get("output", "")) if r.get("ok") else None
    except Exception:
        return None


def _is_level(level):
    """True when the open level's name IS `level` (not merely contains it)"""
    return os.path.basename(str(level_name() or "").replace("\\", "/")).lower() == level.lower()


def sandbox_running():
    return "Sandbox.exe" in subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True).stdout


def main():
    level = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CRYENGINE_DEFAULT_LEVEL", "ashvale")
    if not sandbox_running() and not os.path.isfile(SANDBOX):
        print("Sandbox.exe not found at %s: set CRYENGINE_ENGINE to the engine install folder" % SANDBOX)
        return 1
    if not sandbox_running():
        __import__("project_root").write_projects_json(PROJECTS)     # the active project (CRYENGINE_PROJECT / active_project.txt), not always My Project
        subprocess.Popen([SANDBOX], cwd=PROJECT, creationflags=0x8 | 0x200)
        print("started Sandbox", flush=True)
    t0 = time.time()
    while level_name() is None:
        if time.time() - t0 > 300:
            print("the bridge never answered a Python call")
            return 1
        time.sleep(3)
    if _is_level(level):
        print("level '%s' already open" % level)
        return 0
    time.sleep(5)
    r = bridge("bridge.execute_command asset.edit levels/%s/%s.level" % (level, level), 600)
    print("asset.edit: ok=%s %s" % (r.get("ok"), str(r.get("error") or "")[:160]), flush=True)
    t1 = time.time()
    while time.time() - t1 < 600:
        if _is_level(level):
            print("level '%s' open after %.0f s" % (level, time.time() - t0))
            return 0
        time.sleep(5)
    print("level '%s' did not open" % level)
    return 1


if __name__ == "__main__":
    sys.exit(main())
