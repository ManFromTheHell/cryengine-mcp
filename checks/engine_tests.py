#!/usr/bin/env python3
"""The engine's OWN unit tests (ENGINE_LEARNING_PLAN A11): Crytek ships seven GoogleTest executables in the installed engine's bin/win_x64, built from the engine's source tree.

    CryCommonUnitTest 95 tests (math, strings, containers, heap allocator, thread-safe containers ...)     CrySystemUnitTest 17 (job system, console, XML ...)
    CryEntitySystemUnitTest 8      CryAISystemUnitTest 10 (MNM navmesh geometry)       CryAnimationUnitTest 8 (incl. AnimListRcMatchingTest: the chrparams animation-list patterns)
    Cry3DEngineUnitTest 6          CryAudioSystemUnitTest 0

They are standalone programs: they do not start the editor or the game, load no level and take a fraction of a second. This module runs them from a scratch folder (the executable's own folder supplies its DLLs),
reads GoogleTest's output and keeps a BASELINE (engine_data/engine_tests_baseline.json) so that a change to the environment (a driver, a Windows update, a rebuilt DLL) that breaks the engine's own tests shows up as a
difference. Only the seven names above can be run; nothing else is executed.

    python checks/engine_tests.py [list]        the tests of every executable
    python checks/engine_tests.py run [exe] [--filter Suite.Test] [--accept]
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
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "engine_tests_scratch")
BASELINE = os.path.join(DATA, "engine_tests_baseline.json")
ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.environ.get("CRYENGINE_ENGINE_DIR") or (os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
EXES = ("CryCommonUnitTest", "CrySystemUnitTest", "CryEntitySystemUnitTest", "CryAISystemUnitTest", "CryAnimationUnitTest", "Cry3DEngineUnitTest", "CryAudioSystemUnitTest")
FILTER = re.compile(r"^[A-Za-z0-9_.*:\-?]{1,120}$")


class TestsError(RuntimeError):
    pass


def _path(exe):
    if exe not in EXES:
        raise TestsError("%r is not one of the engine's unit test executables: %s" % (exe, ", ".join(EXES)))
    p = os.path.join(ENGINE, "bin", "win_x64", exe + ".exe")
    if not os.path.isfile(p):
        raise TestsError("%s is not installed" % p)
    return p


def _run(args, timeout):
    """Run one of the executables with a timeout and kill it (and its children) if it overruns. -> (returncode, stdout, seconds)"""
    os.makedirs(SCRATCH, exist_ok=True)
    t0 = time.time()
    p = subprocess.Popen(args, cwd=SCRATCH, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=0x08000000)       # CREATE_NO_WINDOW
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, stdin=subprocess.DEVNULL)
        out, _ = p.communicate()
        raise TestsError("%s did not finish in %d s and was killed: %s" % (os.path.basename(args[0]), timeout, (out or b"").decode("utf-8", "replace")[-300:]))
    return p.returncode, (out or b"").decode("utf-8", "replace"), round(time.time() - t0, 2)


def parse_list(text):
    """GoogleTest --gtest_list_tests output -> {suite: [test, ...]}"""
    suites, cur = {}, None
    for line in text.splitlines():
        if not line.strip() or line.startswith("Running main") or line.startswith("["):
            continue
        if line.startswith("  "):
            if cur is not None:
                suites[cur].append(line.strip().split(" ")[0])
        else:
            cur = line.strip().split(" ")[0].rstrip(".")
            suites[cur] = []
    return suites


def list_tests(exe):
    code, out, _ = _run([_path(exe), "--gtest_list_tests"], 60)
    if code != 0:
        raise TestsError("%s --gtest_list_tests exited %d: %s" % (exe, code, out[-200:]))
    return parse_list(out)


def parse_run(text):
    """GoogleTest's console output -> {tests: {Suite.Test: {status, ms}}, passed, failed, total_ms}"""
    tests = {}
    for m in re.finditer(r"^\[\s+(OK|FAILED)\s+\]\s+(\S+?)(?:,.*?)?\s+\((\d+) ms\)\s*$", text, re.M):
        tests[m.group(2)] = {"status": "ok" if m.group(1) == "OK" else "FAILED", "ms": int(m.group(3))}
    for m in re.finditer(r"^\[\s+SKIPPED\s+\]\s+(\S+?)\s+\((\d+) ms\)", text, re.M):
        tests[m.group(1)] = {"status": "skipped", "ms": int(m.group(2))}
    ran = re.search(r"\[=+\]\s+(\d+) tests? from (\d+) test suites? ran\. \((\d+) ms total\)", text)
    return {"tests": tests, "passed": sum(1 for t in tests.values() if t["status"] == "ok"), "failed": sum(1 for t in tests.values() if t["status"] == "FAILED"),
            "reported_total": int(ran.group(1)) if ran else None, "total_ms": int(ran.group(3)) if ran else None}


def run(exes=None, gtest_filter=None, timeout=120):
    """-> {exes: {name: {returncode, passed, failed, reported_total, seconds, tests, tail}}, passed, failed, ok}"""
    if gtest_filter is not None and not FILTER.match(gtest_filter):
        raise TestsError("the filter %r is not a GoogleTest filter (letters, digits, _ . * : - ?)" % gtest_filter)
    res, passed, failed = {}, 0, 0
    for exe in (exes or EXES):
        args = [_path(exe)] + (["--gtest_filter=" + gtest_filter] if gtest_filter else [])
        code, out, secs = _run(args, timeout)
        r = parse_run(out)
        r.update(returncode=code, seconds=secs, tail=out[-400:] if (code != 0 or r["failed"]) else "")
        if r["reported_total"] is not None and r["reported_total"] != len(r["tests"]):
            r["note"] = "the program reported %d tests and %d were read from its output" % (r["reported_total"], len(r["tests"]))
        res[exe] = r
        passed += r["passed"]
        failed += r["failed"]
    return {"exes": res, "passed": passed, "failed": failed, "ok": failed == 0 and all(r["returncode"] == 0 for r in res.values())}


def compare(result, baseline):
    """Differences from a baseline: tests that failed or vanished or are new, per executable. -> [str]"""
    out = []
    for exe, r in result["exes"].items():
        b = (baseline.get("exes") or {}).get(exe)
        if b is None:
            out.append("%s: not in the baseline" % exe)
            continue
        now, was = {k: v["status"] for k, v in r["tests"].items()}, {k: v["status"] for k, v in b["tests"].items()}
        for k in sorted(set(was) - set(now)):
            out.append("%s: %s was in the baseline and did not run" % (exe, k))
        for k in sorted(set(now) - set(was)):
            out.append("%s: %s is new (%s)" % (exe, k, now[k]))
        for k in sorted(set(now) & set(was)):
            if now[k] != was[k]:
                out.append("%s: %s was %s and is %s" % (exe, k, was[k], now[k]))
    return out


def baseline(result=None, accept=False):
    """The saved baseline; with accept=True the given (or a fresh) result becomes the baseline. -> dict or None"""
    if accept:
        result = result or run()
        slim = {"exes": {k: {"returncode": v["returncode"], "tests": {n: {"status": t["status"]} for n, t in v["tests"].items()}} for k, v in result["exes"].items()},
                "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "engine": ENGINE}
        os.makedirs(DATA, exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=1)
        return slim
    if os.path.isfile(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    return None


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] == "list":
        for exe in EXES:
            s = list_tests(exe)
            print("%-26s %3d tests in %2d suites" % (exe, sum(len(v) for v in s.values()), len(s)))
        return
    flt = argv[argv.index("--filter") + 1] if "--filter" in argv else None
    which = [a for a in argv[1:] if a in EXES]
    r = run(which or None, flt)
    for exe, v in r["exes"].items():
        print("%-26s %3d passed, %d failed, exit %s, %.1fs" % (exe, v["passed"], v["failed"], v["returncode"], v["seconds"]))
    print("OK" if r["ok"] else "PROBLEMS")
    b = baseline()
    if b and not which and not flt:
        for d in compare(r, b):
            print("  differs from baseline:", d)
    if "--accept" in argv and not which and not flt:
        baseline(r, accept=True)
        print("baseline saved")


if __name__ == "__main__":
    main()
