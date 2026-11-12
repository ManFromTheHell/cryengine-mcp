"""Which level the open editor holds: the checkers that compare the editor with a level's files (object_check, entity_check, level_check, project_audit) default to it, so a second level
is checked against its OWN files. With a fixed default level every one of the first level's objects read as missing while another level was open (318 false errors in project_audit).

When the editor does not answer or holds no level, the answer is CRYENGINE_DEFAULT_LEVEL if set, else the first level folder under the active project's Assets/levels, else '' (the
checkers then say there is no such level folder). The reason the editor could not be asked is kept in LAST_ERROR."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os

DEFAULT = os.environ.get("CRYENGINE_DEFAULT_LEVEL", "")
LAST_ERROR = ""                                          # the text of the exception that made the last name() call fall back ("" when the editor answered)


def first_level():
    """the first (alphabetical) level folder under the active project's Assets/levels, or ''"""
    try:
        import project_root
        levels = os.path.join(project_root.ASSETS, "levels")
        for n in sorted(os.listdir(levels)):
            if os.path.isdir(os.path.join(levels, n)):
                return n
    except Exception:                                    # noqa: BLE001
        pass
    return ""


def name(default=DEFAULT):
    """The folder name of the level the editor has open (general.get_current_level_name), or `default` (see the module docstring) when the editor does not answer or holds no level."""
    global LAST_ERROR
    try:
        import world_build
        got = world_build.current_level()["name"]
        LAST_ERROR = ""
        if got:
            return got
    except Exception as exc:                             # noqa: BLE001  (no editor: fall back to a level read from the project's files)
        LAST_ERROR = "%s: %s" % (type(exc).__name__, exc)
    return default or first_level()
