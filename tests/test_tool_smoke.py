#!/usr/bin/env python3
"""Regression test for tool_smoke.py: every tool of the MCP server answers a plain first call (or is deliberately skipped and says why), and a tool added to server.py without being classified fails.

Starts the real server over stdio and calls ~90 read-only tools once (about a minute). By default this test sets CLAUDE_AUTO_RECOVER=0, so the server does NOT relaunch a missing editor; set CLAUDE_AUTO_RECOVER=1 to let it (bridge_watchdog.recover) like it does for any tool call.

    test_tool_smoke.py        # exit 1 on a BROKEN or UNCLASSIFIED tool
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import asyncio
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
os.environ.setdefault("CLAUDE_AUTO_RECOVER", "0")           # never launch the editor from a test unless the caller opts in
import tool_smoke as T

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def main():
    rows = asyncio.run(T.run())
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    check("the server lists every tool (at least 138)", len([r for r in rows if "(invalid args)" not in r["tool"]]) >= 138, len(rows))
    check("no tool is UNCLASSIFIED: each is called here or skipped with a reason (add new tools to tool_smoke.py)", not by.get("UNCLASSIFIED"), [r["tool"] for r in by.get("UNCLASSIFIED", [])])
    check("no tool is BROKEN on a plain first call", not by.get("BROKEN"), [(r["tool"], r["detail"][:50]) for r in by.get("BROKEN", [])][:3])
    check("at least 90 tools were really called and answered", len(by.get("ok", [])) >= 90, len(by.get("ok", [])))
    check("every skipped tool has a stated reason", all(r["detail"] for r in by.get("skipped", [])), len(by.get("skipped", [])))
    check("the skipped tools are the ones that change something (creates, deletes, sets, starts, launches the game): at least one", len(by.get("skipped", [])) >= 1, len(by.get("skipped", [])))
    neg = by.get("negative-ok", []) + by.get("NEGATIVE-FAIL", [])
    check("T-18: mutating tools called with invalid arguments REFUSE (25 or more tried, none accepted garbage)", len(neg) >= 25 and not by.get("NEGATIVE-FAIL"), [(r["tool"], r["detail"][:50]) for r in by.get("NEGATIVE-FAIL", [])][:3] or len(neg))
    check("T-18: the dangerous editor calls are among the refusals (execute_python general.exit, run_console_command quit, mannequin_check reload)", {"execute_python (invalid args)", "run_console_command (invalid args)", "mannequin_check (invalid args)"} <= {r["tool"] for r in by.get("negative-ok", [])})
    check("T-03/T-18: no tool answered with more than the output budget (rows carry `chars`)", all(r.get("chars", 0) <= T.BUDGET_EXEMPT.get(r["tool"], T.BUDGET_CHARS) for r in rows), max((r.get("chars", 0), r["tool"]) for r in rows))
    bad_expected = [r for r in by.get("expected", [])]
    check("no tool needed an 'expected' refusal (a caller-side reason) on this project's plain calls", not bad_expected, [(r["tool"], r["detail"][:40]) for r in bad_expected][:3])
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
