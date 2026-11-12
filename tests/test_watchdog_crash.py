#!/usr/bin/env python3
"""Self-test of the watchdog's crash detection (bridge_watchdog.crash_report_after): an error.log written AFTER the running Sandbox started is a crash (kill at once); an older one, no file, or an unknown
start time is not. Editor-free: it never starts or kills anything.

    test_watchdog_crash.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_watchdog as W

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    tmp = tempfile.mkdtemp(prefix="zz_mcp_wd_test_")
    try:
        log = os.path.join(tmp, "error.log")
        now = time.time()
        with open(log, "w") as f:
            f.write("Logged at ...")
        os.utime(log, (now - 600, now - 600))
        check("control: an error.log written 10 minutes BEFORE the editor started (started 5 minutes ago) is an old crash, not this one", W.crash_report_after(now - 300, log) is None, "")
        os.utime(log, (now - 100, now - 100))
        r = W.crash_report_after(now - 300, log)
        check("an error.log written 100 s ago, after the editor started 300 s ago: a crash of THIS editor (the time is returned)", r is not None and abs(r - (now - 100)) < 2, r)
        os.utime(log, (now - 298, now - 298))
        check("control: an error.log within 5 s of the start is the start-up, not a crash", W.crash_report_after(now - 300, log) is None, "")
        check("control: without an error.log there is nothing to find", W.crash_report_after(now - 300, os.path.join(tmp, "none.log")) is None, "")
        check("control: when the start time is unknown (the process list could not say) nothing is concluded", W.crash_report_after(None, log) is None, "")
        started = W.sandbox_started()
        check("sandbox_started: a number when Sandbox runs (seconds since the epoch, in the past) or None when it does not", started is None or 1.0e9 < started < time.time(), started)
        check("the default path is the project's error.log (where the engine writes it)", os.path.normcase(W.ERROR_LOG) == os.path.normcase(os.path.join(W.PROJECT, "error.log")), W.ERROR_LOG)
        src = open(_paths.script("bridge_watchdog.py"), encoding="utf-8").read()
        check("the strike logic still requires the bridge to be silent before it kills (a crash report alone never kills a healthy editor)", src.index("if bridge_healthy():") < src.index("crash_report_after(sandbox_started())"), "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
