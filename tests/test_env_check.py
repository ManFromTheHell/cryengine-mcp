#!/usr/bin/env python3
"""Self-test of env_check.py: the engine's variable table read from the source and, with the editor open, compared name by name with the 145 variables the running engine lists; the project's
presets; and deliberately broken copies (positive controls) next to an unchanged copy (control), made in a temporary folder (no editor needed for those).

    test_env_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import env_check as E

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


def main():
    try:
        rows, current = E.table()
    except E.EnvError as exc:                                   # a fresh machine without the engine source: nothing to compare against
        print("SKIPPED: %s" % exc)
        return 0
    check("the engine's table has 145 variables in enum order and the current preset version 4", len(rows) == 145 and current == 4 and rows[0]["id"] == "PARAM_SUN_COLOR" and rows[1]["id"] == "PARAM_SUN_INTENSITY", (len(rows), current))
    check("every variable got its AddVar line (group, name, range): none is left as a placeholder", all(r["group"] for r in rows) and all(r["max"] >= r["min"] for r in rows), [r["id"] for r in rows if not r["group"]][:3])
    sun = next(r for r in rows if r["id"] == "PARAM_SUN_INTENSITY")
    check("PARAM_SUN_INTENSITY reads as float, default 119000, range 0..550000; a colour variable has range 0..1 and three defaults", sun["type"] == "TYPE_FLOAT" and sun["default"] == [119000.0] and (sun["min"], sun["max"]) == (0.0, 550000.0)
          and len(rows[0]["default"]) == 3 and (rows[0]["min"], rows[0]["max"]) == (0.0, 1.0), (sun["default"], sun["min"], sun["max"]))
    check("the search finds variables by group or name", len(E.variables("fog")) > 10 and all("fog" in (r["id"] + r["name"] + r["group"] + r["display"]).lower() for r in E.variables("fog")), len(E.variables("fog")))

    try:
        import bridge_py
        import json
        r = bridge_py.send("bridge.env.list", 20)
        live = json.loads(r["output"])["variables"]
    except Exception:                                           # noqa: BLE001
        live = None
        print("  --   the editor is not open: the live comparison is skipped")
    if live is not None:
        check("the running engine lists the same 145 variables, with the same names, groups and order as the table read from the source", [v["name"] for v in live] == [r["name"] for r in rows] and [v["group"] for v in live] == [r["group"] or "Default" for r in rows], (len(live), len(rows)))
        check("and the colour / float types agree", [v["type"] for v in live] == ["color" if r["type"] == "TYPE_COLOR" else "float" for r in rows], "")

    clear_env = os.path.join(E.ASSETS, "libs", "environmentpresets", "ashvale_clear.env")
    have_ash = os.path.isfile(clear_env) and os.path.isfile(os.path.join(E.ASSETS, "libs", "environmentpresets", "ashvale_rain.env"))
    res = E.check()
    if not have_ash:
        print("  --   SKIPPED: the project has no ashvale_clear.env / ashvale_rain.env (the author's level); the checks that pin those presets and the copies made from them are skipped")
        fails = results.count(False)
        print("\n%d checks, %d failed" % (len(results), fails))
        return 1 if fails else 0
    own = [p for p in res["presets"] if "ashvale" in p["file"]]
    check("the project's three presets (clear, rain, and the Undercroft's own) are version 4 with all 145 variables in the engine's order and a Constants block: no error, no E003", len(own) == 3 and all(p["variables"] == 145 and p["version"] == 4 and p["constants"] for p in own) and res["ok"] and "E003" not in [f["code"] for f in res["findings"] if "ashvale" in f["where"]], own)
    moon = [f for f in res["findings"] if f["code"] == "E004" and "ashvale" in f["where"]]
    check("a real finding: the moon colour multiplier is authored 0.4 in both presets but its range ends at 0.1, which the engine clamps to (E004)", len(moon) == 2 and all("PARAM_NIGHSKY_MOON_COLOR_MULTIPLIER" in f["where"] and "CLAMPS" in f["message"] for f in moon), [f["where"][-40:] for f in moon])
    ex = [f for f in res["findings"] if "example.env" in f["where"]]
    check("the engine's shipped example.env is an older file (version 2): converted by id, reported as information (E002), not as an error", "E002" in codes(ex) and not [f for f in ex if f["severity"] == "error"], codes(ex))

    d = E.diff("libs/environmentpresets/ashvale_clear.env", "libs/environmentpresets/ashvale_rain.env")
    ids = {x["id"] for x in d}
    check("control: a preset against itself differs nowhere", E.diff("libs/environmentpresets/ashvale_clear.env", "libs/environmentpresets/ashvale_clear.env") == [], "")
    check("clear against rain: the weather changes the sun, the fog, the clouds, the saturation and the exposure bias (17 variables: the clear day's +0.2 EV against the overcast's own +1.5)", len(d) == 17 and {"PARAM_SUN_INTENSITY", "PARAM_SUN_COLOR", "PARAM_VOLFOG_GLOBAL_DENSITY", "PARAM_HDR_COLORGRADING_COLOR_SATURATION", "PARAM_HDR_EYEADAPTATION_EV_AUTO_COMPENSATION"} <= ids, (len(d), sorted(ids)[:4]))

    work = tempfile.mkdtemp()
    try:
        src = os.path.join(E.ASSETS, "libs", "environmentpresets", "ashvale_clear.env")
        text = open(src, encoding="utf-8").read()

        def put(tag, t):
            p = os.path.join(work, tag + ".env")
            open(p, "w", encoding="utf-8", newline="").write(t)
            return p

        def run(p):
            return E.check_one(os.path.basename(p), E.read_preset(p), rows, current)
        c = run(put("copy", text))
        check("control: an unchanged copy of the preset has only the real moon finding", codes(c) == ["E004"], codes(c))
        blocks = text.split(" <var id=")
        swapped = blocks[:]
        swapped[1], swapped[2] = swapped[2], swapped[1]
        # the last block carries the closing tags; swap two inner ones only
        check("positive control: two <var> elements swapped is E003 (a version-4 file is read positionally: later variables are misloaded)", "E003" in codes(run(put("swap", " <var id=".join(swapped)))), "")
        dropped = " <var id=".join(blocks[:5] + blocks[6:])
        check("positive control: a <var> removed is E003, with the position of the first difference", any("position 4" in f["message"] for f in run(put("drop", dropped)) if f["code"] == "E003"), "")
        bad_time = text.replace('keys="0:300:0:0:0:0:4:4:0,34800:300', 'keys="0:300:0:0:0:0:4:4:0,-5:300', 1)
        check("positive control: a key time out of order or negative is E005", "E005" in codes(run(put("time", bad_time))), "")
        bad_val = text.replace('keys="0:300:0:0:0:0:4:4:0,34800:300:0:0:0:0:4:4:0', 'keys="0:900000:0:0:0:0:4:4:0,34800:300:0:0:0:0:4:4:0', 1)
        r = run(put("value", bad_val))
        check("positive control: a sun intensity of 900000 (range ends at 550000) is E004 and the message says the engine clamps it", any("PARAM_SUN_INTENSITY" in f["where"] and "CLAMPS" in f["message"] for f in r if f["code"] == "E004"), codes(r))
        i = text.index('<var id="PARAM_SUN_COLOR"')
        j = text.index("<spline1", i)
        k = text.index("/>", j) + 2
        nogreen = text[:j] + '<spline1 keys=""/>' + text[k:]
        check("positive control: a colour with keys in red and none in green is E006", "E006" in codes(run(put("green", nogreen))), "")
        check("positive control: a file with no Constants block is E008", "E008" in codes(run(put("noconst", text.replace("<Constants", "<Constantz").replace("</Constants>", "</Constantz>")))), "")
        rng = text.replace('id="PARAM_SUN_INTENSITY" type="TYPE_FLOAT" minValue="0" maxValue="550000"', 'id="PARAM_SUN_INTENSITY" type="TYPE_FLOAT" minValue="0" maxValue="900000"', 1)
        check("positive control: a file range that differs from the engine's table is E007 (information)", any(f["code"] == "E007" and f["severity"] == "info" for f in run(put("range", rng))), "")
        out = E.check([put("notenv", "<Foo/>")])
        check("positive control: a file that is not an EnvironmentPreset is E001", codes(out["findings"]) == ["E001"] and not out["ok"], codes(out["findings"]))
        old = text.replace('version="4"', 'version="3"', 1)
        r = run(put("old", old))
        check("an older version is E002 (information) and the sequence is then matched by id, so no E003 error", "E002" in codes(r) and not [f for f in r if f["code"] == "E003" and f["severity"] == "error"], codes(r))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
