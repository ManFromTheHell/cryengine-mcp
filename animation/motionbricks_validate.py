#!/usr/bin/env python3
"""Compare what the ENGINE computes from a compiled MotionBricks clip with what the clip was built to say. Project Python, numpy only.

    python animation/motionbricks_validate.py <name> [--work <MOTIONBRICKS_ROOT>/work] [--samples <samples.json>]

Needs the two files motionbricks_clip.py and the editor leave behind: <work>/<name>.expected.json (the cycle's own world positions of the key joints at normalized times 0 .25 .5 .75) and the engine's sample
(`bridge.character.samplepose <out.json> | objects/characters/human/sdk_player/sdk_player.cdf | <name> | 0,0.25,0.5,0.75 | 0.0333 | reload`, default <work>/<name>.samples.json). Exit code 1 if any key joint is
more than --tol metres (default 0.03) from where it should be: that is the pipeline's own accuracy, FBX writer and Resource Compiler included. No game is run.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json
import os
import sys

import numpy as np


YAW_TOL_DEG = 1.5


def validate(expected, samples, tol=0.03):
    if not samples.get("found") or not samples.get("loaded"):
        return {"ok": False, "error": "the engine did not find/load the clip %r (found=%s loaded=%s): is the .caf in a folder of the animation set and was the set reloaded?" % (samples.get("anim"), samples.get("found"), samples.get("loaded"))}
    if not expected.get("expected") or len(samples.get("samples", [])) != len(expected["expected"]):
        return {"ok": False, "error": "sample count %d != expected %d: nothing (or not everything) to compare" % (len(samples.get("samples", [])), len(expected.get("expected", [])))}
    ix = {n: i for i, n in enumerate(samples["joints"])}
    worst, worst_yaw, rows = 0.0, 0.0, []
    for exp, got in zip(expected["expected"], samples["samples"]):
        errs = {}
        for name, pos in exp["joints"].items():
            if name not in ix:
                continue
            e = float(np.linalg.norm(np.array(got["pose"][ix[name]][4:7]) - np.array(pos)))
            errs[name] = e
        if not errs:
            return {"ok": False, "error": "no joint of the expected set exists in the engine skeleton at t=%s" % exp["t"]}
        w = max(errs.values())
        worst = max(worst, w)
        top = sorted(errs.items(), key=lambda kv: -kv[1])[:3]
        row = {"t": exp["t"], "max_m": w, "median_m": float(np.median(list(errs.values()))), "worst": [(n, round(v, 4)) for n, v in top]}
        if "root_yaw_deg" in exp and "Bip01" in ix:
            # the root joint IS the locator (the RC re-roots a clip on it): its heading about the vertical, positive to the left, wrapped to +-180
            qw, qx, qy, qz = got["pose"][ix["Bip01"]][:4]
            yaw = float(np.degrees(np.arctan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))))
            row["yaw_err_deg"] = abs((yaw - exp["root_yaw_deg"] + 180.0) % 360.0 - 180.0)
            worst_yaw = max(worst_yaw, row["yaw_err_deg"])
        rows.append(row)
    dur_ok = abs(samples["duration"] - expected["duration"]) < 1.5 / expected["fps"]
    return {"ok": worst <= tol and dur_ok and worst_yaw <= YAW_TOL_DEG, "worst_m": worst, "worst_yaw_deg": worst_yaw, "duration_engine": samples["duration"], "duration_expected": expected["duration"], "duration_ok": dur_ok, "samples": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--work", default=os.environ.get("MOTIONBRICKS_WORK") or os.path.join(_paths.MOTIONBRICKS_ROOT, "work"))       # MOTIONBRICKS_WORK (default <MOTIONBRICKS_ROOT>/work): the folder holding <name>.expected.json
    ap.add_argument("--samples", default=None)
    ap.add_argument("--tol", type=float, default=0.03)
    a = ap.parse_args()
    exp = json.load(open(os.path.join(a.work, a.name + ".expected.json")))
    smp = json.load(open(a.samples or os.path.join(a.work, a.name + ".samples.json")))
    r = validate(exp, smp, a.tol)
    if "error" in r:
        print(r["error"])
        sys.exit(1)
    for row in r["samples"]:
        print("t %.2f: max %.4f m, median %.4f m, worst %s" % (row["t"], row["max_m"], row["median_m"], row["worst"]))
    print("duration engine %.4f s, expected %.4f s" % (r["duration_engine"], r["duration_expected"]))
    print("%s: %s (worst %.4f m, tolerance %.3f m)" % (a.name, "OK" if r["ok"] else "FAIL", r["worst_m"], a.tol))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
