#!/usr/bin/env python3
"""Offline self-test of visual_check.py's logic: the verdict per view, the baseline, the shot list, the job's wiring. The game itself is exercised by the live
runs (accept, plain, --inject-fault); this proves the part that decides, on synthetic pictures whose differences are known.

    test_visual_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import shutil
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import jobs as J
import shot_compare as SC
import visual_check as V

DIR = os.path.join(SC.RUNS, "_vis_test")
W, H = 320, 180
results = []
rng = np.random.default_rng(11)


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-68s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def scene(seed):
    r = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), dtype=np.float32)
    img[:H // 3] = np.linspace(40, 90, W)[None, :, None]
    img[H // 3:] = 70 + 25 * np.sin(np.arange(W) / (6.0 + seed))[None, :, None]
    return img + r.normal(0, 0.5, img.shape)


def capture(base):
    out = base.copy()
    out[:H // 3] += rng.normal(0, 18.0, out[:H // 3].shape)       # the sky differs between any two captures
    out[H // 3:] += rng.normal(0, 1.5, out[H // 3:].shape)
    return out


def save(folder, name, img):
    os.makedirs(folder, exist_ok=True)
    SC.write_png(os.path.join(folder, name + ".png"), np.clip(img, 0, 255).astype(np.uint8))


def main():
    shutil.rmtree(DIR, ignore_errors=True)
    base_dir, run_dir = os.path.join(DIR, "baseline"), os.path.join(DIR, "run")
    try:
        names = ["same_a", "lit_patch_b", "hidden_in_sky_c", "no_baseline_d"]
        scenes = {n: scene(i) for i, n in enumerate(names)}
        def changed_scene(n):
            """The scene as the CURRENT game shows it: the repeat is captured right after the first capture, so it shows the change too."""
            img = scenes[n].copy()
            if n == "lit_patch_b":
                img[H // 2:, W // 2:3 * W // 4] += 50                  # a lit patch on the ground
            if n == "hidden_in_sky_c":
                img[:H // 4, :W // 4] += 12                            # a change inside the noisy sky
            return img
        for n in names:
            if n != "no_baseline_d":
                save(base_dir, n, capture(scenes[n]))                  # the baseline: the scene before the change
            save(run_dir, n, capture(changed_scene(n)))
            save(run_dir, n + "__r", capture(changed_scene(n)))
        rep = V.evaluate(run_dir, base_dir, names)
        status = {v["view"]: v["status"] for v in rep["views"]}
        check("the lit patch is flagged and the untouched view is not", status.get("lit_patch_b") == "changed" and status.get("same_a") == "same", status)
        check("a change hidden inside the noisy sky is NOT claimed (and the view is marked noisy)", status.get("hidden_in_sky_c") == "same" and next(v for v in rep["views"] if v["view"] == "hidden_in_sky_c")["noisy_cells"] >= 1, status.get("hidden_in_sky_c"))
        check("a view with no baseline picture is reported missing, not passed", [m["view"] for m in rep["missing"]] == ["no_baseline_d"] and rep["compared"] == 3, rep["missing"])
        check("only the changed view keeps a difference picture", os.path.isfile(os.path.join(run_dir, "diff_lit_patch_b.png")) and not os.path.exists(os.path.join(run_dir, "diff_same_a.png")))
        changed = next(v for v in rep["views"] if v["view"] == "lit_patch_b")
        check("it says WHERE: the cells of the patch (rows 50-100 %, columns 50-75 %)", {f["cell"] for f in changed["changed_cells"]} >= {"r2c2", "r3c2"}, [f["cell"] for f in changed["changed_cells"]])
        # --- accepting
        full_run = os.path.join(DIR, "full")
        for n in V.views():
            save(full_run, n, scene(3))
            save(full_run, n + "__r", scene(4))
        new_base = os.path.join(DIR, "newbase")
        stored = V.accept(full_run, new_base, {"build": "release", "package_built": "t"})
        manifest = json.load(open(os.path.join(new_base, "manifest.json")))
        pics = sorted(f for f in os.listdir(new_base) if f.endswith(".png"))
        check("accept stores each view's FIRST capture (not the repeats) and a manifest", len(stored) == len(V.views()) and not any("__r" in f for f in pics) and len(pics) == len(V.views()) and manifest["views"] == stored and "accepted" in manifest, len(pics))
        check("the stored pictures are byte for byte the run's", open(os.path.join(new_base, "green.png"), "rb").read() == open(os.path.join(full_run, "green.png"), "rb").read())
        # --- the shot list
        plain, fault = V.shot_lines(False), V.shot_lines(True)
        n0 = len(V.DETERMINISTIC)
        check("the render is made deterministic before the first view (grass off, SMAA without temporal filtering, no clouds)", plain[:n0] == V.DETERMINISTIC and fault[:n0] == plain[:n0]
              and "?e_MergedMeshes 0" in V.DETERMINISTIC and "?r_AntialiasingMode 1" in V.DETERMINISTIC and "?r_VolumetricClouds 0" in V.DETERMINISTIC)
        check("every setting is a VERIFIED one (a `?` line: set, read back, abort on a mismatch)", all(l.startswith("?") for l in V.DETERMINISTIC if "cvar" in l or l.startswith(("?", "~"))) and not any(l.startswith("~") for l in V.DETERMINISTIC))
        check("every view appears twice in a row (the capture and its repeat)", all(plain[i].split()[1:] == plain[i + 1].split()[1:] and plain[i + 1].split()[0] == plain[i].split()[0] + "__r" for i in range(n0, n0 + 2 * len(V.DAY), 2)))
        check("the fault adds exactly the brightness line, right after the deterministic settings and before the first view", [l for l in fault if l not in plain] == [V.FAULT_LINE]
              and fault.index(V.FAULT_LINE) == n0 and fault.index(V.FAULT_LINE) < fault.index("green 500 330 0") and V.FAULT_LINE == "?ash_brightness 0.7", [l for l in fault if l not in plain])
        check("the night starts with the clock command (once)", sum(l.startswith("!claude_tod") for l in plain) == 1 and not any("ash_lamps" in l for l in fault))
        check("the day views come before the night views", plain.index("!claude_tod 22 0") == n0 + 2 * len(V.DAY) + 1 and plain[plain.index("!claude_tod 22 0") - 1] == "?r_AntialiasingMode 1")
        ok = all(any(re.match(p, l) for p in J.SHOT_PATTERNS) for l in plain + fault)
        check("every line is inside the grammar the shoot job validates (labels, numbers, console commands)", ok, [l for l in plain + fault if not any(re.match(p, l) for p in J.SHOT_PATTERNS)][:2])
        check("view names are unique and short enough for a label with the __r suffix", len(set(V.views())) == len(V.views()) and all(len(n) + 3 <= 40 for n in V.views()))
        # --- the job's wiring
        argv = J.build_argv("visual", {"accept": True}, "20200101_000000_visual")
        check("the visual job passes --out under job_runs and --accept", argv[0] == "--out" and "20200101_000000_visual_shots" in argv[1] and "--accept" in argv and "--inject-fault" not in argv, argv)
        try:
            J.build_argv("visual", {"accept": True, "inject_fault": True}, "x")
            check("accept together with inject_fault is refused (it would store the fault as the baseline)", False)
        except J.JobError:
            check("accept together with inject_fault is refused (it would store the fault as the baseline)", True)
        check("the kind closes the editor and needs the machine to itself", J.KINDS["visual"][1] and J.KINDS["visual"][2] and J.KINDS["visual"][3])
        # --- the verdict the job reports, read from its output
        jid = "20200101_000000_visual"
        os.makedirs(J.JOBS, exist_ok=True)
        for text, want in (("RESULT visual: 20 views compared against the baseline of x, 0 changed\n", "pass"), ("RESULT visual: 20 views compared against the baseline of x, 2 changed (a, b)\n", "FAIL"),
                           ("RESULT visual: baseline accepted: 20 views stored in x\n", "pass"),
                           ("RESULT visual (fault test): picture 30% darker: 12 of 14 day views flagged, 2 of 6 night views flagged -> the harness CAN fail: PASS\n", "pass"),
                           ("RESULT visual (fault test): picture 30% darker: 1 of 14 day views flagged, 0 of 6 night views flagged -> the harness did NOT see it: FAIL\n", "FAIL"),
                           ("RESULT visual: no baseline yet: run once with accept=true\n", "FAIL")):
            open(os.path.join(J.JOBS, jid + ".log"), "w").write("noise\n" + text)
            got = J._result({"id": jid, "kind": "visual", "state": "done"})["verdict"]
            check("job_status verdict from %r" % text[:46], got == want, got)
        os.remove(os.path.join(J.JOBS, jid + ".log"))
    finally:
        shutil.rmtree(DIR, ignore_errors=True)
        try:
            os.remove(os.path.join(J.JOBS, "20200101_000000_visual.log"))
        except OSError:
            pass
    check("cleanup: the scratch folder is gone", not os.path.exists(DIR))
    bad = results.count(False)
    print("visual check test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
