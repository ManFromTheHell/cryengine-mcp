#!/usr/bin/env python3
"""Self-test of render_tour.py: the setting parser and the tour list against the engine's own CVar catalog (offline), then, with the editor open, a short real tour with controls (the game is not
run; every variable is read back at its old value). About 90 s live.

    test_render_tour.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import cvar_catalog as C
import render_tour as R

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def refused(items):
    try:
        R.parse(items)
        return ""
    except R.TourError as exc:
        return str(exc)


def main():
    missing = [n for n, _ in R.DEFAULT_TOUR if not C.get(n)]
    check("every variable of the default tour (%d) exists in the engine's catalog" % len(R.DEFAULT_TOUR), not missing, missing)
    locked = [n for n, _ in R.DEFAULT_TOUR if C.get(n)["settable"]["editor"] != "yes"]
    check("and the editor can set every one of them", not locked, locked)
    check("a setting is name=number: e_Shadows=0 and r_Gamma = 1.4 parse", R.parse(["e_Shadows=0", "r_Gamma = 1.4"]) == [("e_Shadows", "0"), ("r_Gamma", "1.4")], "")
    check("a malformed setting is refused", "name=number" in refused(["e_Shadows"]) and "name=number" in refused(["e_Shadows=off"]), "")
    msg = refused(["e_Shadow=0"])
    check("an unknown variable is refused with the nearest names", "no console variable" in msg and "e_Shadows" in msg, msg[:60])
    check("a variable's label is the engine's own help text (e_Shadows: 'Controls rendering of shadows')", R.label("e_Shadows") == "Controls rendering of shadows", R.label("e_Shadows"))
    check("the numbers 1 and 1.0 and '1' are the same value, 1 and 2 are not", R._same_number("1", 1.0) and R._same_number(1, "1.0") and not R._same_number(1, 2) and not R._same_number("a", 1), "")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except (OSError, ConnectionError):                          # the editor is not open (bridge_py raises ConnectionRefusedError / socket.timeout); other errors are real and propagate
        live = False
        print("  --   SKIPPED: the editor is not open, the live checks did not run")
    if live:
        import debug_views as D
        before = {n: D._cvar_get(n) for n in ("r_Gamma", "e_Sun", "r_Sharpening")}
        res = R.tour([("r_Gamma", before["r_Gamma"]), ("e_Sun", 0), ("r_NoSuchVariableAnywhere", 1)])
        rows = {r["name"]: r for r in res["rows"]}
        check("control: setting a variable to the value it already has changes nothing (%.2f against drift %.2f)" % (rows["r_Gamma"]["mean_abs_diff"], rows["r_Gamma"]["drift"]),
              rows["r_Gamma"]["mean_abs_diff"] < 3 * max(rows["r_Gamma"]["drift"], res["noise"], 0.3), rows["r_Gamma"]["mean_abs_diff"])
        check("switching the sun off is a different picture by far (%.1f levels, %sx the noise or drift)" % (rows["e_Sun"]["mean_abs_diff"], rows["e_Sun"]["ratio"]), rows["e_Sun"]["ratio"] > 10 and rows["e_Sun"]["applied"] == "0", rows["e_Sun"]["ratio"])
        check("a variable this editor does not have is reported, not guessed", rows["r_NoSuchVariableAnywhere"]["status"] == "no such variable in this editor", rows["r_NoSuchVariableAnywhere"]["status"])
        after = {n: D._cvar_get(n) for n in before}
        check("every variable the tour touched holds the value it had: %s" % before, after == before and all(r.get("restored", True) for r in res["rows"]), after)

    bad = results.count(False)
    print("render tour test: %s (%d checks%s)" % ("PASS" if not bad else "%d FAILED" % bad, len(results), "" if live else ", live checks SKIPPED"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
