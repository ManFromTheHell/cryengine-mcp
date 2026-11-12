#!/usr/bin/env python3
"""The editor-only regression suite: every test_*.py of this folder, one after the other, with the editor's state checked around the run (it never runs the game).

All of these tests are editor-free or use the OPEN editor (its viewport, its loaders, scratch objects named zz_mcp_* that they delete again); none launches the game, packages, or closes the editor. Run it after any
change to a tool or to the plugin; it is also a job kind (`start_job('editor_suite')`), because it takes minutes.

    editor_suite.py                  # all tests
    editor_suite.py ui_check level_check      # these tests only (names without test_ and .py)
    editor_suite.py --list

Besides each test's own exit code the suite checks what a test must leave alone: the number of level objects, the open dialogs, the rendering and debug CVars the viewport tests switch (a fingerprint of about 60 variables: the render tour's, the debug views', a few more; a changed one is reported AND put back, so one test cannot unsettle the next one's picture), and (afterwards) that no zz_mcp_* file is left under the project. A test that
passes but leaves the level changed fails the suite. The result goes to engine_data/editor_suite.json (per test: exit code, seconds, its own summary line).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import subprocess
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
DATA = os.path.join(HERE, "engine_data")
OUT = os.path.join(DATA, "editor_suite.json")
PY = sys.executable
TIMEOUT = 900
SUMMARY = (re.compile(r"(\d+) checks?, (\d+) failed"), re.compile(r"PASS \((\d+) checks\)"), re.compile(r"(\d+) passed, (\d+) failed"), re.compile(r"(\d+) of (\d+) (?:checks )?passed"))

sys.path.insert(0, HERE)


TESTS_DIR = os.path.join(HERE, "tests")


def tests(folder=TESTS_DIR):
    return sorted(f[5:-3] for f in os.listdir(folder) if f.startswith("test_") and f.endswith(".py"))


def editor_state():
    """-> {"objects": n, "dialogs": n} from the open editor, or None when it does not answer"""
    try:
        import bridge_py
        objs = json.loads(bridge_py.send("bridge.object.list", 60).get("output") or "[]")
        dlg = json.loads(bridge_py.send("bridge.dialog.list", 20).get("output") or "{}")
        state = {"objects": len(objs), "dialogs": int(dlg.get("count", 0))}
        try:                                                       # the time of day and the current preset decide whether the sun lights the view at all (e_Sun off = no change at night)
            state["time"] = round(float(json.loads(bridge_py.send("bridge.env.time", 20).get("output") or "{}").get("time")), 2)
            state["preset"] = json.loads(bridge_py.send("bridge.env.presets", 20).get("output") or "{}").get("current")
        except Exception:                                         # noqa: BLE001
            pass
        return state
    except Exception:                                             # noqa: BLE001
        return None


EXTRA_CVARS = ("e_LodMin", "e_DebugDraw", "r_DebugGBuffer", "r_wireframe", "r_Gamma", "r_Sharpening", "e_Sun", "r_DisplayInfo", "sys_spec", "e_TimeOfDaySpeed", "e_Vegetation", "e_Shadows", "r_VolumetricClouds",
               "r_HDREyeAdaptationMode", "e_Clouds", "e_Wind", "e_Decals", "r_Rain", "p_draw_helpers", "ai_DebugDraw")


def watched_cvars():
    """The variables the editor-only tests are allowed to switch and must put back."""
    names = set(EXTRA_CVARS)
    try:
        import render_tour
        names |= {n for n, _ in render_tour.DEFAULT_TOUR}
    except Exception:                                             # noqa: BLE001
        pass
    try:
        import debug_views
        for v in debug_views.catalog():
            names |= {k for k, _ in v.get("sets", [])} | {k for k, _ in v.get("cycle", [])}
    except Exception:                                             # noqa: BLE001
        pass
    return sorted(names)


def cvar_state(names):
    """{name: value as text, or None when this editor does not have it}"""
    import debug_views
    out = {}
    for n in names:
        try:
            out[n] = str(debug_views._cvar_get(n))
        except Exception:                                         # noqa: BLE001
            out[n] = None
    return out


def put_back(changed):
    import debug_views
    for n, (was, _now) in changed.items():
        if was is not None:
            try:
                debug_views._cvar_set(n, was)
            except Exception:                                     # noqa: BLE001
                pass


def scratch_files():
    found = []
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in ("_snapshots", ".git", "node_modules", "bin", "solutions", "user")]
        for n in files + dirs:
            if n.lower().startswith("zz_mcp"):
                found.append(os.path.join(root, n))
    return found


def run_one(name, timeout=TIMEOUT, folder=TESTS_DIR):
    t0 = time.time()
    try:
        p = subprocess.run([PY, os.path.join(folder, "test_%s.py" % name)], cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace")
        code, text = p.returncode, p.stdout
    except subprocess.TimeoutExpired as exc:
        code, text = -1, "TIMED OUT after %d s\n%s" % (timeout, (exc.stdout or "") if isinstance(exc.stdout, str) else "")
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    summary = ""
    for l in reversed(lines):
        if any(r.search(l) for r in SUMMARY):
            summary = l.strip()
            break
    fails = [l.strip()[:140] for l in lines if re.match(r"\s*FAIL\b", l)]
    return {"test": name, "exit": code, "seconds": round(time.time() - t0, 1), "summary": summary or (lines[-1].strip()[:120] if lines else "(no output)"), "failed_checks": fails[:6]}


# the tests that read the editor's PICTURE: the editor idles when nobody looks at it (m_bKeepEditorActive stays false: user.cfg cannot set it, only a live ed_keepEditorActive 0 -> 1 does), and an idle editor
# hands such a test stale frames. The editor is kept active for these alone: holding the editor active for a whole run has ended in an AMD driver crash (error.log: AMDXX64.DLL access violation).
VIEWPORT_TESTS = ("debug_views", "decal_lab", "lods_live", "pfx_author", "render_tour")
# the bridge_watchdog.py scheduled task (every 5 minutes, two strikes) brings Sandbox back; seconds to wait for it (env CRYENGINE_RESTART_WAIT overrides).
# Without that task registered nothing restarts the editor and the run stops after this wait.
try:
    RESTART_WAIT = int(os.environ.get("CRYENGINE_RESTART_WAIT", "600"))
except ValueError:
    RESTART_WAIT = 600


def wait_for_editor_back(log):
    """After a crash: wait for the watchdog to restart Sandbox and reopen the level -> state | None"""
    t0 = time.time()
    while time.time() - t0 < RESTART_WAIT:
        time.sleep(15)
        st = editor_state()
        if st and st.get("objects", 0) > 0:
            log("         the editor is back after %d s (%d objects)" % (time.time() - t0, st["objects"]))
            time.sleep(20)                                        # let the level settle
            return editor_state()
    return None


def run(only=None, skip=(), timeout=TIMEOUT, log=print, folder=TESTS_DIR, out_path=OUT):
    names = [n for n in tests(folder) if (not only or n in only) and n not in skip]
    unknown = [n for n in (only or []) if n not in tests(folder)]
    if unknown:
        raise ValueError("no such test: %s (tests: %s)" % (", ".join(unknown), ", ".join(tests(folder))))
    before = editor_state()
    log("editor: %s" % (("%d objects, %d dialogs" % (before["objects"], before["dialogs"])) if before else "not answering (editor tests will skip their live parts)"))
    results = []
    watch = watched_cvars() if (before and folder == TESTS_DIR) else []
    cv_before = cvar_state(watch) if watch else {}
    keep = None
    if before and folder == TESTS_DIR:
        try:
            import editor_state as keep
        except Exception:                                         # noqa: BLE001
            keep = None
    for n in names:
        held = False
        if keep is not None and n in VIEWPORT_TESTS:
            try:
                keep.hold_active()
                held = True
            except Exception:                                     # noqa: BLE001
                pass
        r = run_one(n, timeout, folder)
        mid = editor_state() if before else None
        if before and mid is None:                                # the editor stopped answering during or after the test: a crash; wait for the watchdog
            log("         the editor does not answer after %s: waiting for the watchdog to restart it" % n)
            r["exit"] = r["exit"] or 5
            r["leak"] = "the editor crashed or hung during this test (editor error.log tells why)"
            back = wait_for_editor_back(log)
            results.append(r)
            log("  FAIL %-26s %6.1fs  %s  !! %s" % (n, r["seconds"], r["summary"][:60], r["leak"]))
            before = back
            if not back:
                log("         the editor did not come back in %d s: the run stops" % RESTART_WAIT)
                break
            cv_before = cvar_state(watch) if watch else {}
            continue
        if held:
            try:
                keep.release_active()
            except Exception:                                     # noqa: BLE001
                pass
        if watch:
            cv_now = cvar_state(watch)
            changed = {k: (cv_before[k], cv_now[k]) for k in watch if cv_before.get(k) != cv_now.get(k)}
            if changed:
                r["exit"] = r["exit"] or 3
                r["cvar_leak"] = {k: {"was": a, "now": b} for k, (a, b) in changed.items()}
                put_back(changed)
                time.sleep(2.0)                                   # the next picture must start from the restored state
                cv_now = cvar_state(watch)
            cv_before = cv_now
        leak = None
        if before and mid and (mid["objects"] != before["objects"] or mid["dialogs"] != before["dialogs"]):
            leak = "left the editor with %d objects and %d dialogs (it had %d and %d)" % (mid["objects"], mid["dialogs"], before["objects"], before["dialogs"])
            r["exit"] = r["exit"] or 2
            r["leak"] = leak
            before = mid                                          # report it once, then compare from here
        elif before and mid and (mid.get("time") != before.get("time") or mid.get("preset") != before.get("preset")) and before.get("time") is not None:
            leak = "left the time of day at %s with preset %s (it was %s, %s): put back" % (mid.get("time"), mid.get("preset"), before.get("time"), before.get("preset"))
            r["exit"] = r["exit"] or 4
            r["leak"] = leak
            try:
                import bridge_py
                if mid.get("preset") != before.get("preset") and before.get("preset"):
                    bridge_py.send("bridge.env.current " + before["preset"], 30)
                bridge_py.send("bridge.env.time %s" % before["time"], 30)
                time.sleep(2.0)
            except Exception:                                     # noqa: BLE001
                pass
        results.append(r)
        cvl = ("  !! left CVars changed (put back): " + ", ".join("%s %s->%s" % (k, v["was"], v["now"]) for k, v in list(r["cvar_leak"].items())[:4])) if r.get("cvar_leak") else ""
        log("  %-4s %-26s %6.1fs  %s%s%s" % ("ok" if r["exit"] == 0 else "FAIL", n, r["seconds"], r["summary"][:70], ("  !! " + leak) if leak else "", cvl))
        for f in r["failed_checks"]:
            log("         %s" % f)
    files = scratch_files()
    if files:
        log("  FAIL scratch files left under the project: %s" % ", ".join(os.path.relpath(f, PROJECT) for f in files[:5]))
    failed = [r for r in results if r["exit"] != 0]
    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "editor": before is not None, "tests": len(results), "failed": [r["test"] for r in failed], "scratch_files_left": [os.path.relpath(f, PROJECT) for f in files],
           "seconds": round(sum(r["seconds"] for r in results), 1), "results": results}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    log("%d tests, %d failed%s, %.0f s" % (len(results), len(failed), (" (" + ", ".join(r["test"] for r in failed) + ")") if failed else "", out["seconds"]))
    return out


def main():
    args = sys.argv[1:]
    if args[:1] == ["--list"]:
        print("\n".join(tests()))
        return 0
    only = [a for a in args if not a.startswith("-")]
    out = run(only or None)
    return 1 if out["failed"] or out["scratch_files_left"] else 0


if __name__ == "__main__":
    sys.exit(main())
