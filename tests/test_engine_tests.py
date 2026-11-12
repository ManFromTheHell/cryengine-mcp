#!/usr/bin/env python3
"""Self-test of engine_tests.py: the GoogleTest output parser with positive controls (synthetic output with a FAILED test), the whitelist and filter checks, then the engine's own seven unit test executables
(about 3 s; they start no editor and no game).

    test_engine_tests.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import os
import subprocess
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import engine_tests as T

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


RUN = """[==========] Running 4 tests from 2 test suites.
[----------] 2 tests from A
[ RUN      ] A.one
[       OK ] A.one (3 ms)
[ RUN      ] A.two
x.cpp:10: Failure
[  FAILED  ] A.two (1 ms)
[----------] 2 tests from B
[       OK ] B.three (0 ms)
[  SKIPPED ] B.four (0 ms)
[==========] 4 tests from 2 test suites ran. (7 ms total)
[  PASSED  ] 2 tests.
[  FAILED  ] 1 test, listed below:
[  FAILED  ] A.two
"""
LIST = "Running main() from gtest_main.cc\nA.\n  one\n  two\nB.\n  three # GetParam() = 1\n"


def main():
    p = T.parse_run(RUN)
    check("control: the parser reads two passed, one failed and one skipped test from GoogleTest's output, and the program's own total", (p["passed"], p["failed"], p["reported_total"], p["total_ms"]) == (2, 1, 4, 7) and p["tests"]["B.four"]["status"] == "skipped", (p["passed"], p["failed"]))
    check("positive control: the FAILED summary line at the end is not counted as a second failure of A.two", p["failed"] == 1 and list(p["tests"]).count("A.two") == 1, p["failed"])
    check("the --gtest_list_tests output is read into suites and tests (a '# GetParam' note is cut)", T.parse_list(LIST) == {"A": ["one", "two"], "B": ["three"]}, T.parse_list(LIST))
    for bad in ("cmd", "../x", "CryCommonUnitTest.exe", "notepad"):
        try:
            T._path(bad)
            refused = False
        except T.TestsError:
            refused = True
        check("positive control: only the seven names can be run: %r is refused" % bad, refused, "")
    for bad in ("a; del x", "$(x)", "a b", "../../x", "x" * 200):
        try:
            T.run(["CryCommonUnitTest"], bad)
            refused = False
        except T.TestsError:
            refused = True
        check("positive control: a filter that is not a GoogleTest filter is refused (%r)" % bad[:12], refused, "")

    missing = [e for e in T.EXES if not os.path.isfile(os.path.join(T.ENGINE, "bin", "win_x64", e + ".exe"))]
    if missing:
        print("  --   SKIP the engine's own test executables: %d of %d not found under %s (set CRYENGINE_ENGINE to the engine install folder): %s" % (len(missing), len(T.EXES), T.ENGINE, ", ".join(missing)))
        fails = results.count(False)
        print("\n%d checks, %d failed" % (len(results), fails))
        return 1 if fails else 0

    r = T.run()
    ex = r["exes"]
    base = T.baseline()
    # was: passed == 144 (95+17+8+10+8+6+0 for the author's 5.7.1 install); now: no failure, something ran, and the count equals the saved baseline's when one exists
    want = sum(1 for v in base["exes"].values() for t in v["tests"].values() if t["status"] == "ok") if base else None
    check("the engine's own tests: none fails, every exit code 0, and the count of passes equals the baseline's (%s)" % (want if want is not None else "no baseline saved: only > 0 is required"),
          r["ok"] and r["failed"] == 0 and r["passed"] > 0 and (want is None or r["passed"] == want), (r["passed"], r["failed"]))
    check("the count of tests read from the output equals the count the programs report (no line was missed)", all(v["reported_total"] == len(v["tests"]) and "note" not in v for v in ex.values()), [k for k, v in ex.items() if v["reported_total"] != len(v["tests"])])
    check("the listing agrees with the run: every listed test ran", all(sum(len(t) for t in T.list_tests(e).values()) == len(ex[e]["tests"]) for e in T.EXES), "")
    one = T.run(["CryCommonUnitTest"], "CryStringTest.Construction")
    check("control: a filter runs exactly the test it names", one["passed"] == 1 and list(one["exes"]["CryCommonUnitTest"]["tests"]) == ["CryStringTest.Construction"], one["passed"])
    none = T.run(["CryCommonUnitTest"], "NoSuch.Test")
    check("positive control: a filter that matches nothing runs 0 tests (and is not mistaken for a pass of everything)", none["passed"] == 0 and not none["exes"]["CryCommonUnitTest"]["tests"], none["passed"])
    if base is None:
        print("  --   SKIP baseline comparison: no engine_data/engine_tests_baseline.json yet (save one with the engine_tests tool)")
        base = {"exes": {k: {"returncode": v["returncode"], "tests": {n: {"status": t["status"]} for n, t in v["tests"].items()}} for k, v in r["exes"].items()}}   # self-baseline, for the controls below
    else:
        check("the saved baseline matches this run: no difference", T.compare(r, base) == [], T.compare(r, base))
    broken = copy.deepcopy(r)
    k = next(iter(broken["exes"]["CryCommonUnitTest"]["tests"]))
    broken["exes"]["CryCommonUnitTest"]["tests"][k]["status"] = "FAILED"
    del broken["exes"]["CrySystemUnitTest"]["tests"][next(iter(broken["exes"]["CrySystemUnitTest"]["tests"]))]
    broken["exes"]["Cry3DEngineUnitTest"]["tests"]["New.Test"] = {"status": "ok", "ms": 0}
    diffs = T.compare(broken, base)
    check("positive control: a test that fails, one that vanished and a new one are each reported against the baseline", len(diffs) == 3 and any("was ok and is FAILED" in d for d in diffs) and any("did not run" in d for d in diffs) and any("is new" in d for d in diffs), diffs)
    left = [p for p in os.listdir(T.SCRATCH)] if os.path.isdir(T.SCRATCH) else []
    check("the tests leave nothing behind in their scratch folder", not left, left)
    alive = subprocess.run(["tasklist"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.lower()
    check("no unit test process is left running", "unittest.exe" not in alive, "")

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
