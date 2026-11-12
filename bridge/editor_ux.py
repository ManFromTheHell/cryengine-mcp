#!/usr/bin/env python3
"""The editor's own undo and Notification Center, for the tools (ENGINE_LEARNING_PLAN A8; manual: Sandbox Programming > Undo system, Notifications and Error Reporting).

Observed in the open editor (verbs bridge.undo.* and bridge.notify, added to the plugin):
  - The editor's Python functions (general.new_object, which place_object uses) ALREADY record an undo step ("Create new object (name)"): Ctrl+Z removes what place_object placed.
  - The bridge's own object verbs (bridge.object.create / transform / material / delete) recorded NOTHING, because an undo step exists only inside a CUndo scope. They record one ("Claude: create Brush (name)",
    "Claude: move name") only while `bridge.undo.mode on`, which is off by default: the test tools make and delete scratch objects and Ctrl+Z should not bring those back. `create_object` switches it on
    around its call, because that object is a deliberate creation by the caller.
  - Undoing a creation whose object was later deleted without an undo step is safe (the engine's CUndoBaseObjectNew finds no object, drops the redo and does nothing).
  - bridge.notify shows a non-modal message in the Notification Center (INotificationCenter::ShowInfo / ShowWarning / ShowCritical), which the manual names as the preferred way to notify the operator.

`undo(action, steps, expect)` refuses to undo unless every one of the newest steps contains `expect`: the stack also holds the operator's own edits, and an undo that took back the wrong one would be worse
than none.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)

LEVELS = ("info", "warning", "critical")


class UxError(ValueError):
    pass


def _send(cmd, timeout=30):
    import bridge_py
    try:
        r = bridge_py.send(cmd, timeout)
    except OSError as exc:
        raise UxError("the editor's bridge does not answer (%s): this needs Sandbox open (health_check says what is wrong)" % exc) from exc
    if not r.get("ok"):
        raise UxError(str(r.get("error") or r)[:300])
    try:
        return json.loads(r.get("output") or "{}")
    except ValueError:
        return {"raw": r.get("output")}


def notify(level, title, message=""):
    """A non-modal message in the editor's Notification Center."""
    level = (level or "info").lower()
    if level not in LEVELS:
        raise UxError("level is one of %s" % ", ".join(LEVELS))
    title, message = " ".join((title or "").split()), " ".join((message or "").split())
    if not title or len(title) > 80 or len(message) > 400 or "|" in title:
        raise UxError("title: 1-80 characters without '|'; message: up to 400")
    return _send("bridge.notify %s %s | %s" % (level, title, message))


def info():
    """-> {mode, undo_steps, redo_steps, latest: [newest first]}"""
    return _send("bridge.undo.info")


def set_mode(on):
    return _send("bridge.undo.mode " + ("on" if on else "off"))


def undo(action="info", steps=1, expect=""):
    """action 'info' | 'undo' | 'redo'. undo needs `expect`: every one of the newest `steps` undo steps must contain it (info lists them); redo takes back what an undo took (at most the redo stack)."""
    if action == "info":
        return info()
    if action not in ("undo", "redo"):
        raise UxError("action is info, undo or redo")
    steps = max(1, min(int(steps), 20))
    state = info()
    if action == "undo":
        if not state.get("undo_steps"):
            raise UxError("nothing to undo")
        top = state.get("latest", [])[:steps]
        if not (expect or "").strip():
            raise UxError("undo needs `expect`: part of the description of the step(s) to take back, because the stack also holds the user's own edits. Newest now: %s" % (top[:3] or "-"))
        bad = [s for s in top if expect.lower() not in s.lower()]
        if bad or len(top) < steps:
            raise UxError("the newest %d undo step(s) are %s, not all containing %r: nothing was undone" % (steps, top, expect))
    else:
        if steps > state.get("redo_steps", 0):
            raise UxError("the redo stack holds %d step(s)" % state.get("redo_steps", 0))
    out = _send("bridge.undo.step %s %d" % (action, steps))
    out["took"] = steps
    return out


def panel(action="status"):
    """The "Claude Status" pane inside the editor (editor_panel.py, Python + PySide2 in the editor's own interpreter). action 'status' | 'open' | 'close'. 'open' registers the pane class once per editor
    session (SandboxBridge.register_window) and opens it as a floating window; 'close' closes only that window. -> {"registered", "open"}"""
    if action not in ("status", "open", "close"):
        raise UxError("action is status, open or close")
    import bridge_py
    pre = "import sys, importlib\nsys.path.insert(0, %r)\nimport editor_panel as P\nimportlib.reload(P)\n" % os.path.dirname(_paths.script("editor_panel.py"))
    body = {"status": "", "open": "P.open_panel()\n", "close": "closed = P.close_panel()\n"}[action]
    tail = "import json\nprint(json.dumps({'registered': P.registered(), 'open': len(P.panels())%s}))" % (", 'closed': closed" if action == "close" else "")
    try:
        r = bridge_py.run_source(pre + body + tail, 60)
    except OSError as exc:
        raise UxError("the editor's bridge does not answer (%s): this needs Sandbox open" % exc) from exc
    log = (r.get("log") or "").strip()
    try:
        return json.loads(log.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise UxError("the editor did not answer as expected: %s" % (log or r.get("error") or r)[:300]) from exc


def main():
    a = sys.argv[1:]
    if a[:1] == ["notify"]:
        print(notify(a[1] if len(a) > 1 else "info", a[2] if len(a) > 2 else "Claude", " ".join(a[3:])))
    else:
        print(json.dumps(info(), indent=1))


if __name__ == "__main__":
    main()
