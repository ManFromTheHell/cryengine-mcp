#!/usr/bin/env python3
"""Self-test of editor_suite.py with FAKE tests in a temporary folder: a passing one, a failing one, one that times out, one that leaves a level object behind and one that leaves a zz_mcp_ file behind
(the last two only with the editor open). Each fault must be reported; the leftovers are removed here.

    test_editor_suite.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_suite as E

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


PASS = 'print("3 checks, 0 failed")\n'
FAIL = 'print("  ok   one")\nprint("  FAIL the thing that is wrong")\nprint("2 checks, 1 failed")\nraise SystemExit(1)\n'
SLOW = 'import time\ntime.sleep(20)\n'
CUBE = "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf"
LEAK_OBJECT = ('import sys\nsys.path.insert(0, __HERE__)\nimport bridge_py\nbridge_py.send("bridge.object.create Brush 2 2 2 zz_mcp_suite_leak ' + CUBE + '", 30)\nprint("1 checks, 0 failed")\n').replace("__HERE__", repr(HERE))
LEAK_FILE = 'import os\nopen(os.path.join(__DATA__, "zz_mcp_suite_left.txt"), "w").write("x")\nprint("1 checks, 0 failed")\n'.replace("__DATA__", repr(os.path.join(HERE, "engine_data")))


def main():
    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                             # noqa: BLE001
        live = False
        print("  --   the editor is not open: the leak checks are skipped")
    root = tempfile.mkdtemp(prefix="zz_mcp_suite_")
    out = os.path.join(root, "result.json")
    try:
        def write(name, text):
            open(os.path.join(root, "test_%s.py" % name), "w", encoding="utf-8").write(text)
        write("aa_pass", PASS)
        write("bb_fail", FAIL)
        r = E.run(folder=root, out_path=out, log=lambda *_: None)
        by = {x["test"]: x for x in r["results"]}
        check("control: a passing test is exit 0 with its own summary line", by["aa_pass"]["exit"] == 0 and by["aa_pass"]["summary"] == "3 checks, 0 failed", by["aa_pass"]["summary"])
        check("positive control: a failing test is a failure of the suite, with its FAIL line kept", by["bb_fail"]["exit"] == 1 and r["failed"] == ["bb_fail"] and any("the thing that is wrong" in f for f in by["bb_fail"]["failed_checks"]), r["failed"])
        saved = json.load(open(out, encoding="utf-8"))
        check("the result is written to the file asked for (tests, failed, seconds, per-test rows)", saved["tests"] == 2 and saved["failed"] == ["bb_fail"] and len(saved["results"]) == 2, saved["tests"])
        only = E.run(only=["aa_pass"], folder=root, out_path=out, log=lambda *_: None)
        check("a named subset runs only those tests", only["tests"] == 1 and only["failed"] == [], only["tests"])
        try:
            E.run(only=["no_such_test"], folder=root, out_path=out, log=lambda *_: None)
            check("positive control: an unknown test name is refused, naming the tests there are", False, "")
        except ValueError as exc:
            check("positive control: an unknown test name is refused, naming the tests there are", "aa_pass" in str(exc), str(exc)[:50])
        os.remove(os.path.join(root, "test_bb_fail.py"))
        os.remove(os.path.join(root, "test_aa_pass.py"))
        write("cc_slow", SLOW)
        t = E.run(folder=root, out_path=out, timeout=2, log=lambda *_: None)
        check("positive control: a test that does not finish in time is killed and fails (exit -1, 'TIMED OUT')", t["results"][0]["exit"] == -1 and "TIMED OUT" in t["results"][0]["summary"], t["results"][0]["summary"])
        os.remove(os.path.join(root, "test_cc_slow.py"))
        if live:
            write("dd_leak_object", LEAK_OBJECT)
            before = E.editor_state()
            try:
                lk = E.run(folder=root, out_path=out, log=lambda *_: None)
                row = lk["results"][0]
                check("positive control: a test that exits 0 but leaves a level object behind FAILS the suite (exit 2, the counts named)", row["exit"] == 2 and "objects" in row.get("leak", "") and lk["failed"] == ["dd_leak_object"], row.get("leak"))
            finally:
                try:
                    bridge_py.send("bridge.object.delete zz_mcp_suite_leak", 30)
                except Exception:                                 # noqa: BLE001
                    pass
            check("the editor is back to the object count it had (the scratch object was deleted here)", E.editor_state() == before, E.editor_state())
            os.remove(os.path.join(root, "test_dd_leak_object.py"))
            write("ee_leak_file", LEAK_FILE)
            try:
                lf = E.run(folder=root, out_path=out, log=lambda *_: None)
                check("positive control: a zz_mcp_ file left under the project is reported by the suite", any("zz_mcp_suite_left" in f for f in lf["scratch_files_left"]), lf["scratch_files_left"])
            finally:
                p = os.path.join(HERE, "engine_data", "zz_mcp_suite_left.txt")
                if os.path.exists(p):
                    os.remove(p)
            check("and the project has no zz_mcp_ file left now", E.scratch_files() == [], E.scratch_files())
    finally:
        shutil.rmtree(root, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
