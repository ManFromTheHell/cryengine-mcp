"""Makes the tool folders importable by their module names.

The modules of this repository are grouped in folders (bridge/, checks/, catalogs/, ...) but import each other by plain module name
(`import bridge_py`). Importing this module puts every group folder on sys.path, so that works for the MCP server and for any module run as a
script (`python checks/material_check.py`). Every module starts with a three-line header that imports it.

    _paths.ROOT           the repository root (data files such as engine_data/, rag/, knowledge_*.json are relative to it)
    _paths.script(name)   absolute path of a module file by its file name ("jobs.py") wherever it lives
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GROUPS = ("bridge", "checks", "catalogs", "assets", "animation", "level", "packaging", "screens", "tests", os.path.join("examples", "ashvale"))
FOLDERS = [os.path.join(ROOT, g) for g in GROUPS]

for _d in [ROOT] + FOLDERS:
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.append(_d)

def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


# WORKSPACE: the folder the tools share for small state files (the deploy lock, the "editor closed on purpose" marker, the pause file, the game's trace log).
# CRYENGINE_WORKSPACE, else CRYENGINE_STATE_DIR or CLAUDE_STATE_DIR (older names), else <repository>/state.
WORKSPACE = _env("CRYENGINE_WORKSPACE", "CRYENGINE_STATE_DIR", "CLAUDE_STATE_DIR") or os.path.join(ROOT, "state")

# ENGINE_SOURCE: a CRYENGINE source checkout (CRYENGINE_SOURCE; the older CRYENGINE_SOURCE_DIR is read too). Only the tools that read engine source or build the plugin
# need it; it is "" when unset and they say so.
ENGINE_SOURCE = _env("CRYENGINE_SOURCE", "CRYENGINE_SOURCE_DIR")

# MOTIONBRICKS_ROOT: where the optional MotionBricks install and its recordings live (clips/, work/, repo/, venv/). Not part of this repository and never downloaded.
MOTIONBRICKS_ROOT = _env("MOTIONBRICKS_ROOT") or os.path.join(ROOT, "anim_work", "motionbricks")


def workspace(name=""):
    """Path of a state file in the shared workspace folder (the folder is not created here)."""
    return os.path.join(WORKSPACE, name) if name else WORKSPACE


_INDEX = None


def script(name):
    """Absolute path of the module file `name` (for example "cvar_probe.py") in whichever group folder holds it; ROOT/name when it is not found."""
    global _INDEX
    if _INDEX is None:
        _INDEX = {}
        for d in [ROOT] + FOLDERS:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith(".py"):
                        _INDEX.setdefault(f, os.path.join(d, f))
    return _INDEX.get(name, os.path.join(ROOT, name))
