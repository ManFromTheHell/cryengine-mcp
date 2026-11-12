"""A "Claude Status" panel INSIDE the Sandbox editor, written in Python with PySide2 (ENGINE_LEARNING_PLAN A7), with no C++ and no change to the engine folder.

How the editor takes a Python window (manual: Sandbox Python Plugins; source: Code/Sandbox/Plugins/SandboxPythonBridge, EditorQt/Util/BoostPythonHelpers.cpp):
  * the module `SandboxBridge` (loaded with the editor) has `register_window(window_type, name, category="", needs_menu_item=True, menu_path="", unique=True)`: it registers a QWidget subclass with the
    editor's class factory as a VIEW PANE (the class factory keeps the type); `general.open_pane("<name>")` opens it (IEditor::OpenView). There is no close_pane (its help text mentions one); a pane is closed
    from inside, through Qt. The pane's widget is created when the pane opens, by calling the type with no arguments.
  * Sandbox runs `Editor/Python/plugins/*/startup.py` at start-up, but ONLY under the ENGINE's install folder (PathUtil::GetEnginePath()): a plugin there would be a file added to the engine. This module
    registers at run time instead (register(), once per editor session; the registration is gone when the editor restarts: call it again, or `editor_panel` the MCP tool).
  * The editor's interpreter is Python 3.7.3 (this file must stay 3.7-clean) and runs on the editor's MAIN thread: the panel only READS small files and the editor's own cheap calls. It never calls
    the bridge socket or starts a process: a blocking call from inside the editor's Python deadlocks the editor (the bridge answers on the thread that would be waiting).

Shows: the level and its object count, the bridge plugin's file, the last accepted project-audit baseline, the last editor-suite run and the latest jobs. Refreshes every 3 seconds while open.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import time

HERE = _paths.ROOT
DATA = os.path.join(HERE, "engine_data")
JOBS = os.path.join(HERE, "job_runs")
PANE_NAME = "Claude Status"
REFRESH_MS = 3000
JOBS_SHOWN = 6


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _when(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def recent_jobs(limit=JOBS_SHOWN, jobs_dir=JOBS):
    """the newest job records (id, kind, state, seconds), newest first. A record that is not a job (no state) is skipped."""
    out = []
    try:
        names = sorted(os.listdir(jobs_dir), reverse=True)
    except OSError:
        return out
    for n in names:
        if len(out) >= limit:
            break
        if not n.endswith(".json") or n.endswith(".json.tmp"):
            continue
        rec = _read_json(os.path.join(jobs_dir, n))
        if not isinstance(rec, dict) or "state" not in rec or "kind" not in rec:
            continue
        end = rec.get("ended") or time.time()
        out.append({"id": rec.get("id", n[:-5]), "kind": rec["kind"], "state": rec["state"], "seconds": int(end - rec.get("started", end))})
    return out


def default_plugin_dll():
    """ClaudeBridgePlugin.dll sits beside SandboxPythonBridge.dll in the engine's EditorPlugins folder; the loaded module knows where that is"""
    try:
        import SandboxBridge
        return os.path.join(os.path.dirname(SandboxBridge.bridge_path), "ClaudeBridgePlugin.dll")
    except Exception:                                             # noqa: BLE001 - offline: the launcher's default engine folder
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "CRYENGINE Launcher", "Engines", "crytek", "cryengine-57-lts", "5.7.1", "bin", "win_x64", "EditorPlugins", "ClaudeBridgePlugin.dll")


def snapshot(editor=None, data_dir=DATA, jobs_dir=JOBS, plugin_dll=None):
    """Everything the panel shows, as plain data. editor: a callable returning {"level", "objects"} (None offline)."""
    snap = {"when": time.time(), "editor": None, "plugin": None, "audit": None, "suite": None, "jobs": recent_jobs(JOBS_SHOWN, jobs_dir)}
    if editor is not None:
        try:
            snap["editor"] = editor()
        except Exception as exc:                                  # noqa: BLE001 - a panel must never take the editor down
            snap["editor"] = {"error": str(exc)[:80]}
    try:
        st = os.stat(plugin_dll or default_plugin_dll())
        snap["plugin"] = {"bytes": st.st_size, "modified": st.st_mtime}
    except OSError:
        pass
    base = _read_json(os.path.join(data_dir, "audit_baseline.json"))
    if isinstance(base, dict) and isinstance(base.get("validators"), dict):
        vals = base["validators"]
        snap["audit"] = {"saved": base.get("saved", "?"), "validators": len(vals), "ran": sum(1 for v in vals.values() if v.get("ran")),
                         "errors": sum(v.get("errors", 0) for v in vals.values()), "warnings": sum(v.get("warnings", 0) for v in vals.values())}
    suite = _read_json(os.path.join(data_dir, "editor_suite.json"))
    if isinstance(suite, dict) and "tests" in suite:
        snap["suite"] = {"when": suite.get("when", "?"), "tests": suite["tests"], "failed": suite.get("failed", []), "seconds": suite.get("seconds"), "scratch": suite.get("scratch_files_left", [])}
    return snap


def render_text(snap):
    lines = ["Claude status   (refreshed %s)" % _when(snap["when"]), ""]
    ed = snap.get("editor")
    if ed is None:
        lines.append("Editor   (not asked)")
    elif "error" in ed:
        lines.append("Editor   unavailable: %s" % ed["error"])
    else:
        lines.append("Editor   level %s, %s objects" % (ed.get("level", "?"), ed.get("objects", "?")))
    pl = snap.get("plugin")
    lines.append("Bridge plugin   %s" % ("%s bytes, built %s" % (format(pl["bytes"], ","), _when(pl["modified"])) if pl else "ClaudeBridgePlugin.dll not found"))
    a = snap.get("audit")
    lines += ["", "Project audit (accepted baseline)", "   " + ("%s: %d validators (%d ran), %d errors, %d known warnings" % (a["saved"], a["validators"], a["ran"], a["errors"], a["warnings"]) if a else "no baseline yet")]
    s = snap.get("suite")
    if s:
        verdict = "all passed" if not s["failed"] else "FAILED: " + ", ".join(s["failed"][:5])
        lines += ["", "Editor suite (last run)", "   %s: %s tests, %s, %s s%s" % (s["when"], s["tests"], verdict, s["seconds"], (", left behind: " + ", ".join(s["scratch"][:3])) if s["scratch"] else "")]
    else:
        lines += ["", "Editor suite (last run)", "   none yet"]
    lines += ["", "Jobs (newest first)"]
    if snap["jobs"]:
        lines += ["   %-28s %-12s %-8s %d s" % (j["id"], j["kind"], j["state"], j["seconds"]) for j in snap["jobs"]]
    else:
        lines.append("   none")
    return "\n".join(lines)


def editor_info():
    """The editor's own cheap read-only calls (main thread only)."""
    import sandbox.general as general
    return {"level": general.get_current_level_name(), "objects": general.get_objects_count()}


def make_panel_class():
    """The QWidget subclass the editor will instantiate with no arguments. Built on demand so the module imports without Qt."""
    from PySide2 import QtCore, QtGui, QtWidgets

    class ClaudeStatusPanel(QtWidgets.QWidget):
        def __init__(self):
            super(ClaudeStatusPanel, self).__init__()
            self.setWindowTitle(PANE_NAME)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(6, 6, 6, 6)
            self.text = QtWidgets.QPlainTextEdit(self)
            self.text.setReadOnly(True)
            self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self.text.setFont(font)
            lay.addWidget(self.text)
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.refresh)
            self.timer.start(REFRESH_MS)
            self.refresh()

        def refresh(self):
            try:
                self.text.setPlainText(render_text(snapshot(editor_info)))
            except Exception as exc:                              # noqa: BLE001 - never raise out of a timer inside the editor
                self.text.setPlainText("Claude status panel: %s" % exc)

    return ClaudeStatusPanel


def registered():
    """True when this editor session already holds the pane class (registering twice would add a second class of the same name)."""
    import sandbox.general as general
    return PANE_NAME in list(general.get_pane_class_names())


def register():
    """Register the pane with the editor once per session -> True when it registered now, False when it was already there"""
    if registered():
        return False
    import SandboxBridge
    SandboxBridge.register_window(make_panel_class(), PANE_NAME, category="Claude", needs_menu_item=True, menu_path="Claude", unique=True)
    return True


def open_panel():
    import sandbox.general as general
    register()
    general.open_pane(PANE_NAME)


def pane_chain():
    """[(class name, window title, is a window)] from the panel widget up to the top level: how the editor wraps a Python pane (to close it safely)"""
    from PySide2 import QtWidgets
    for w in QtWidgets.QApplication.allWidgets():
        if w.windowTitle() == PANE_NAME and type(w).__name__ == "ClaudeStatusPanel":
            chain, cur = [], w
            while cur is not None:
                chain.append((type(cur).__name__, cur.windowTitle(), cur.isWindow()))
                cur = cur.parentWidget()
            return chain
    return []


def panels():
    """the open panel widgets of this class"""
    from PySide2 import QtWidgets
    return [w for w in QtWidgets.QApplication.allWidgets() if type(w).__name__ == "ClaudeStatusPanel" and w.windowTitle() == PANE_NAME]


def close_panel():
    """Close the pane through its own top-level window -> number closed. The editor opens a Python pane as a floating plain QWidget titled like the pane (observed); only such a window is closed:
    a pane docked into the main frame has the main frame as its top-level window, which is NEVER closed from here (that would close the editor)."""
    closed = 0
    for w in panels():
        top = w.window()
        if type(top).__name__ == "QWidget" and top.windowTitle() == PANE_NAME and top is not w:
            top.close()
            closed += 1
    return closed
