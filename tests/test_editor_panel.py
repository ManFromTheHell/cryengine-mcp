#!/usr/bin/env python3
"""Self-test of editor_panel.py (the "Claude Status" pane that runs INSIDE Sandbox's Python 3.7): the data and text on synthetic folders with controls (offline), the source's safety rules (3.7 syntax, nothing
that blocks the editor's main thread), then, with the editor open, the pane registered, opened, read, and closed again through the editor itself.

    test_editor_panel.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ast
import json
import os
import shutil
import sys
import tempfile
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_panel as P

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def put(folder, name, obj):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


def remote(src, timeout=60):
    """run source inside the editor's Python (with this module importable there) -> its printed log"""
    import bridge_py
    pre = "import sys, importlib\nsys.path.insert(0, %r)\nimport editor_panel as P\nimportlib.reload(P)\n" % os.path.dirname(_paths.script("editor_panel.py"))
    r = bridge_py.run_source(pre + src, timeout)
    return (r.get("log") or "").strip()


def main():
    tmp = tempfile.mkdtemp(prefix="zz_mcp_panel_test_")
    try:
        data, jobs = os.path.join(tmp, "data"), os.path.join(tmp, "jobs")
        os.makedirs(data)
        os.makedirs(jobs)
        now = time.time()
        put(jobs, "20261013_100000_tests.json", {"id": "20261013_100000_tests", "kind": "tests", "state": "done", "started": now - 100, "ended": now - 40})
        put(jobs, "20261013_110000_package.json", {"id": "20261013_110000_package", "kind": "package", "state": "running", "started": now - 30})
        put(jobs, "20261013_120000_shoot.json", {"id": "20261013_120000_shoot", "kind": "shoot", "state": "failed", "started": now - 10, "ended": now - 4})
        put(jobs, "notes.json", {"hello": "not a job"})
        put(jobs, "20261013_130000_broken.json", "{ this is not json")
        put(jobs, "20261013_140000_x.json.tmp", {"id": "x", "kind": "x", "state": "running", "started": now})
        js = P.recent_jobs(10, jobs)
        check("recent_jobs: the three job records, newest first; the file that is not a job, the broken JSON and the .tmp are skipped", [j["id"] for j in js] == ["20261013_120000_shoot", "20261013_110000_package", "20261013_100000_tests"], [j["id"] for j in js])
        check("recent_jobs: a finished job's seconds are ended-started (60), a running one's are counted to now (30)", js[2]["seconds"] == 60 and 29 <= js[1]["seconds"] <= 32, [j["seconds"] for j in js])
        check("recent_jobs: the limit applies; a missing folder gives an empty list (not an error)", len(P.recent_jobs(2, jobs)) == 2 and P.recent_jobs(5, os.path.join(tmp, "nope")) == [], "")

        put(data, "audit_baseline.json", {"saved": "2026-10-13 10:55:12", "validators": {"a": {"ran": True, "errors": 0, "warnings": 3}, "b": {"ran": True, "errors": 1, "warnings": 2}, "c": {"ran": False, "errors": 0, "warnings": 0}}})
        put(data, "editor_suite.json", {"when": "2026-10-13 11:02:38", "tests": 50, "failed": [], "scratch_files_left": [], "seconds": 381.0})
        snap = P.snapshot(lambda: {"level": "ashvale", "objects": 307}, data, jobs, os.path.join(tmp, "x.dll"))
        check("snapshot: the audit baseline is summed (3 validators, 2 ran, 1 error, 5 warnings) and the suite read; a missing plugin file is None", snap["audit"] == {"saved": "2026-10-13 10:55:12", "validators": 3, "ran": 2, "errors": 1, "warnings": 5}
              and snap["suite"]["tests"] == 50 and snap["plugin"] is None, snap["audit"])
        txt = P.render_text(snap)
        check("render_text: level and objects, the audit line, the suite line and the jobs all appear", all(k in txt for k in ("level ashvale, 307 objects", "3 validators (2 ran), 1 errors, 5 known warnings", "50 tests, all passed, 381.0 s", "20261013_120000_shoot", "failed")), txt[:40])

        dll = os.path.join(tmp, "p.dll")
        put(tmp, "p.dll", "x" * 2048)
        check("snapshot: a plugin file shows its size", P.snapshot(None, data, jobs, dll)["plugin"]["bytes"] == 2048 and "2,048 bytes" in P.render_text(P.snapshot(None, data, jobs, dll)), "")
        put(data, "editor_suite.json", {"when": "w", "tests": 50, "failed": ["render_tour", "lods_live"], "scratch_files_left": ["zz_mcp_a.txt"], "seconds": 9.0})
        t2 = P.render_text(P.snapshot(None, data, jobs, dll))
        check("control: a failed suite is shown as FAILED with the test names and the file it left behind (not as passed)", "FAILED: render_tour, lods_live" in t2 and "left behind: zz_mcp_a.txt" in t2 and "all passed" not in t2, t2[-120:])

        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        t3 = P.render_text(P.snapshot(None, empty, os.path.join(empty, "j"), dll))
        check("control: with nothing on disk the panel says so (no baseline yet, none yet, none), without raising", "no baseline yet" in t3 and "none yet" in t3 and "Editor   (not asked)" in t3, "")

        def boom():
            raise RuntimeError("the editor is busy")
        t4 = P.render_text(P.snapshot(boom, empty, empty, dll))
        check("control: an editor call that raises becomes a line on the panel, not an exception out of the timer", "unavailable: the editor is busy" in t4, "")

        src = open(_paths.script("editor_panel.py"), encoding="utf-8").read()
        try:
            ast.parse(src, feature_version=(3, 7))
            ok = True
        except SyntaxError as exc:
            ok = False
            print("   ", exc)
        check("the source parses as Python 3.7 (the editor's interpreter is 3.7.3)", ok, "")
        banned = [w for w in ("bridge_py", "subprocess", "socket", "os.system", "Popen", "time.sleep") if w in src.split('"""', 2)[2]]
        check("nothing in the panel can block the editor's main thread: no bridge socket, no process, no sleep", banned == [], banned)

        live = False
        try:
            import bridge_py
            bridge_py.send("bridge.camera.get", 10)
            live = True
        except Exception as exc:                                              # noqa: BLE001
            print("  (the editor does not answer: %s)" % str(exc)[:80])
        if live:
            run_live()
        else:
            print("  (editor not answering: the live checks are skipped)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


def run_live():
    import object_props
    objects = None
    try:
        check("live precondition: the pane is not open", remote("print(len(P.panels()))") == "0", "")
        first = remote("print(P.register())")
        check("live: the pane class is registered with the editor (True now, or False when this session had it already)", first in ("True", "False") and remote("print(P.registered())") == "True", first)
        check("live control: registering again does nothing (False): a second class of the same name is never added", remote("print(P.register())") == "False", "")
        check("live control: closing when nothing is open closes nothing", remote("print(P.close_panel())") == "0", "")
        remote("P.open_panel()")
        time.sleep(1.5)
        n = remote("print(len(P.panels()))")
        check("live: general.open_pane opened one panel", n == "1", n)
        txt = remote("print(json.dumps(P.panels()[0].text.toPlainText()))" if False else "import json\nprint(json.dumps(P.panels()[0].text.toPlainText()))")
        text = json.loads(txt) if txt else ""
        objects = object_props.object_count()
        check("live: it shows the open level and its object count as the editor reports it (%d)" % objects, "level ashvale, %d objects" % objects in text, text.splitlines()[2] if len(text.splitlines()) > 2 else text)
        check("live: and the project's files (audit baseline, suite, plugin, jobs)", all(k in text for k in ("Project audit", "Editor suite", "Bridge plugin", "Jobs (newest first)")), "")
        top = remote("w = P.panels()[0].window()\nprint(type(w).__name__, w.isWindow(), w.windowTitle() == P.PANE_NAME)")
        check("live: the editor put it in its own floating window (a plain QWidget with the pane's title), not into the main frame", top == "QWidget True True", top)
        check("live: its refresh timer runs", remote("print(P.panels()[0].timer.isActive(), P.panels()[0].timer.interval())") == "True 3000", "")
    finally:
        closed = remote("print(P.close_panel())")
        time.sleep(1.0)
        left = remote("print(len(P.panels()))")
        check("live: the pane is closed again through its own window (%s closed) and nothing is left open" % closed, left == "0", left)
    check("live: the editor still answers and holds the same objects", object_props.object_count() == objects, "")

    import editor_ux as U
    try:
        U.panel("explode")
        refused = False
    except U.UxError:
        refused = True
    check("the tool's function refuses an action it does not know", refused, "")
    try:
        s0 = U.panel("status")
        s1 = U.panel("open")
        time.sleep(1.5)
        s2 = U.panel("status")
        check("the tool's function: status (closed), open (one panel), status again (one panel)", s0["open"] == 0 and s1["registered"] and s2["open"] == 1, (s0, s1, s2))
    finally:
        s3 = U.panel("close")
        time.sleep(1.0)
    check("the tool's function: close closes exactly that one window and the editor shows none open", s3.get("closed") == 1 and U.panel("status")["open"] == 0, s3)


if __name__ == "__main__":
    main()
