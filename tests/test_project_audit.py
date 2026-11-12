#!/usr/bin/env python3
"""Self-test of project_audit.py: the baseline comparison and the failure isolation on synthetic validators (editor-free), then one real run over the project (about 5 s with the editor open).

    test_project_audit.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import os
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import project_audit as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def finding(sev, code="X1", where="a", msg="m"):
    return {"severity": sev, "code": code, "where": where, "message": msg}


def main():
    r = A._result("v", [finding("error"), finding("warning"), finding("info")], False)
    check("a validator's counts come from its findings: 1 error, 1 warning, 1 info", (r["errors"], r["warnings"], r["info"]) == (1, 1, 1), (r["errors"], r["warnings"], r["info"]))
    r = A._result("PBS material rules", [finding("error"), finding("warning")], False)
    check("an advisory validator (the PBS guidelines) cannot raise an error: its error is reported as a warning", (r["errors"], r["warnings"]) == (0, 2), (r["errors"], r["warnings"]))

    saved = list(A.VALIDATORS)
    try:
        A.VALIDATORS[:] = [("good", lambda: ([finding("warning", "W1", "x")], {"n": 1}), False), ("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")), False), ("needs editor", lambda: ([], {}), True),
                           ("clean", lambda: ([], {}), False)]
        out = A.run(skip_editor=True)
        by = {v["name"]: v for v in out["validators"]}
        check("control: a validator that works is reported with its findings", by["good"]["ran"] and by["good"]["warnings"] == 1 and by["clean"]["ran"], by["good"]["warnings"])
        check("positive control: a validator that raises is reported as NOT RUN with its reason, and the ones after it still ran", not by["broken"]["ran"] and "boom" in by["broken"]["reason"] and by["clean"]["ran"], by["broken"].get("reason"))
        check("positive control: with the editor skipped, a validator that needs it is NOT RUN, and the others are", not by["needs editor"]["ran"] and out["ran"] == 2 and len(out["not_run"]) == 2, out["not_run"])
        check("the overall verdict ignores validators that did not run and needs at least one that did", out["ok"] and A.run(only=["nothing"], skip_editor=True)["ok"] is False, "")
        base_path = os.path.join(tempfile.mkdtemp(), "audit_baseline.json")
        old = A.BASELINE
        A.BASELINE = base_path
        try:
            A.accept(out)
            base = A.baseline()
            check("the baseline round-trips: comparing a run with the baseline it was saved from shows nothing new", A.compare(out, base) == {"new": [], "gone": [], "notes": []}, A.compare(out, base))
            worse = copy.deepcopy(out)
            for v in worse["validators"]:
                if v["name"] == "clean":
                    v["findings"] = [finding("error", "E9", "y", "new problem")]
                if v["name"] == "good":
                    v["findings"] = []
            d = A.compare(worse, base)
            check("positive control: a finding that appeared is NEW and one that disappeared is GONE", len(d["new"]) == 1 and d["new"][0]["code"] == "E9" and len(d["gone"]) == 1 and "W1" in d["gone"][0]["finding"], d)
            worse2 = copy.deepcopy(out)
            for v in worse2["validators"]:
                if v["name"] == "good":
                    v["ran"] = False
                    v["reason"] = "x"
            check("positive control: a validator that ran in the baseline and did not now is noted, not silently dropped", any("did not run now" in n for n in A.compare(worse2, base)["notes"]), A.compare(worse2, base)["notes"])
            info_only = copy.deepcopy(out)
            for v in info_only["validators"]:
                if v["name"] == "clean":
                    v["findings"] = [finding("info", "I1", "z", "only information")]
            check("information findings never count as new (the baseline holds errors and warnings)", A.compare(info_only, base)["new"] == [], "")
            a, b = finding("warning", "L011", "C:/ShaderPak/Engine/ShaderCache.pak", "is older than Assets/textures/a.dds: x"), finding("warning", "L011", "C:/ShaderPak/Engine/ShaderCache.pak", "is older than Assets/textures/b.dds: x")
            check("the stale shader cache (L011) is ONE finding whichever file is the newest (2026-10-15: each new texture made it look NEW), other codes still key on their message",
                  A.key(a) == A.key(b) and A.key(finding("warning", "W9", "w", "one thing")) != A.key(finding("warning", "W9", "w", "another thing")), "")
        finally:
            A.BASELINE = old
    finally:
        A.VALIDATORS[:] = saved

    check("the real list holds 22 validators: 7 editor-free and 15 that read the open editor", len(A.VALIDATORS) == 22 and sum(1 for v in A.VALIDATORS if not v[2]) == 7, (len(A.VALIDATORS), sum(1 for v in A.VALIDATORS if not v[2])))
    free = A.run(skip_editor=True)
    check("an editor-free run over the real project runs the 7 editor-free validators, finds no error, and says which 15 did not run", free["ran"] == 7 and free["errors"] == 0 and len(free["not_run"]) == 15, (free["ran"], free["errors"], len(free["not_run"])))
    live = A.editor_open()
    if live:
        full = A.run()
        check("the full run over the real project: 22 validators ran, none failed to run, no error, in under 60 s", full["ran"] == 22 and not full["not_run"] and full["errors"] == 0 and sum(v.get("seconds", 0) for v in full["validators"]) < 60, (full["ran"], full["not_run"], full["errors"]))
        check("the engine's own unit tests are among them and pass (144)", next(v for v in full["validators"] if v["name"] == "engine unit tests")["detail"]["passed"] == 144, "")
        base = A.baseline()
        check("the saved baseline is read and the project shows nothing NEW against it (or the baseline is yet to be accepted)", base is None or A.compare(full, base)["new"] == [], A.compare(full, base)["new"][:2] if base else "no baseline")
    else:
        print("  --   the editor is not open: the full run is skipped")

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
