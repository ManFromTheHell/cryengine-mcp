#!/usr/bin/env python3
"""Visual regression for the packaged game: does the game still LOOK the way it was accepted?

    visual_check.py --out <dir> [--accept] [--inject-fault]
    visual_check.py --out <dir> --reevaluate <run dir>      judge a stored run again, without the game

Every test the project has measures numbers (CI, perf, lamps, menus). Nine houses rendered flat grey for a day under 100 % passing tests, because nothing looked. This takes a
fixed set of VIEWS (landscapes and the houses by day, six places at night), each TWICE in a row, and compares each against the accepted baseline picture:

  - the two captures of one view are the NOISE FLOOR (the sky, swaying grass, a villager walking by): shot_compare judges each cell of a 4x4 grid against that cell's own repeat
    difference, so a view only counts as changed where it differs from the baseline by clearly more than it differs from itself;
  - the check runs with the merged-mesh grass off, plain SMAA (no temporal jitter) and no volumetric clouds, and the HUD masked: a frame is then a function of the pose (see DETERMINISTIC);
  - --accept stores this run's first captures as the baseline (visual_baseline/, with the package's age and the view list);
  - --inject-fault darkens the picture by 30 % (ash_brightness 0.7): the day views MUST then be flagged, which is how the harness is shown able to fail. It fails (exit 1) if fewer
    than 10 of the 14 day views are flagged. (Lamps-out was tried first and could not serve: see FAULT_LINE.)

It launches the packaged game (view_shots.py), so it closes the editor: run it as the `visual` job / the `visual_check` MCP tool. Pictures: <out>/<view>.png and <view>__r.png,
difference pictures <out>/diff_<view>.png for the views that changed (red = changed beyond noise, blue = within it).

What already existed and is reused: view_shots.py (the capture, `house:<Name>:S:12` placing), shot_compare.py (the comparison with a control), health.package_freshness. New: the view
set, the baseline, and the verdict per view.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import subprocess
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import shot_compare as SC

BASELINE = os.path.join(HERE, "visual_baseline")
HOUSES = ["Reeve", "Tavern", "Chapel", "CottageA", "CottageB", "CottageC", "TomCabin", "Smithy", "Barn"]
# (view name, shot line after the label): x y yaw, or house:<Name>:<side>:<metres>. yaw 0 looks north (+y). The village runs y 330..400, the camp is at y ~770.
DAY = [("green", "500 330 0"), ("village", "500 300 0"), ("road_south", "506 300 180"), ("forest_road", "510 450 0"), ("camp", "515 745 0")] + \
      [("day_" + h.lower(), "house:%s:S:12" % h) for h in HOUSES]
NIGHT = [("night_chapel", "house:Chapel:S:12"), ("night_tavern", "house:Tavern:S:12"), ("night_cottagea", "house:CottageA:S:12"), ("night_smithy", "house:Smithy:S:12"),
         ("night_green", "500 330 0"), ("night_camp", "515 745 0")]
NIGHT_HOUR = 22
# A cell must differ from the baseline by this many grey levels MORE than the view differs from its own repeat. The in-run repeat is almost noise-free here (0.0-0.4), but an
# UNCHANGED game still differs between launches by up to ~6 levels in a cell: the first-person sword's idle sway (the bottom cells), lamp flicker at night. Measured over
# deterministic runs: cross-launch maxima 5.6 and 3.8 levels; the lamps-out fault gives 10-26 in each night view. 8 sits between them. Subtler changes than ~8 levels in a cell
# (a few percent of brightness) are NOT seen: this finds visible regressions (a flat grey house, a missing texture, lighting that moved), not tuning.
MIN_LEVELS = 8.0
# The fault that proves the harness can fail: the picture 30 % darker (the game's own brightness option, an ordinary variable). The lamps-out fault used first could NOT serve: the
# unchanged game's night views differ between launches at the lamp-lit cells by about as much as lamps-out changes them (learned tolerance 12-21 levels against a fault of 10-25),
# probably the lamps' animated light style, which has no fixed phase. Night frames are dark, so a 30 % change there is only a few grey levels: the fault is judged on the DAY views.
FAULT_LINE = "?ash_brightness 0.7"
FAULT_MIN_DAY_FLAGGED = 10


def views():
    return [v for v, _ in DAY] + [v for v, _ in NIGHT]


# Settings that make a frame a function of the pose alone. Measured: with the grass off the in-run noise fell to 0.03-0.05 grey levels on the house views, and what still
# differed between launches was the slate roof's moire (the engine default r_AntialiasingMode is 2, SMAA with TEMPORAL filtering: projection jitter and history make a frame depend
# on the frame count), the cloud layer, and the HUD. So: plain SMAA (1), no volumetric clouds, and the HUD masked (HUD_BOXES).
# Each setting is VERIFIED (`?`: set, read back from the engine, abort on a mismatch): without the read-back a run can come out with the temporal AA still on, silently, and every house view then differs.
DETERMINISTIC = ["?e_MergedMeshes 0", "?r_AntialiasingMode 1", "?r_VolumetricClouds 0", "!ash_status"]      # the last line is the 3 s pause that lets the renderer settle
HUD_BOXES = [[0.0, 0.0, 0.46, 0.085], [0.0, 0.86, 0.32, 1.0]]       # the objective banner (top left) and health / coins / bread (bottom left)


def shot_lines(inject_fault=False):
    """The grass is switched off first (and the rest of DETERMINISTIC): its layout differs from one launch to the next (measured: the nine house views, which stand in it, differed from the baseline by
    a mean of 24-28 grey levels with the same mean brightness and no camera shift; the views on roads and the green, without grass in front of the camera, by 0.3-5.6). Within one
    launch the two captures agree, so the in-run repeat cannot see this noise. What is compared is what the project changes: houses, materials, textures, terrain, trees, lighting."""
    lines = list(DETERMINISTIC)
    if inject_fault:
        lines.append(FAULT_LINE)
    for name, where in DAY:
        lines += ["%s %s" % (name, where), "%s__r %s" % (name, where)]
    lines.append("?r_AntialiasingMode 1")                                                     # asserted again: it must still hold at night
    lines.append("!claude_tod %d 0" % NIGHT_HOUR)
    for name, where in NIGHT:
        lines += ["%s %s" % (name, where), "%s__r %s" % (name, where)]
    return lines


TOLERANCE_FILE = "tolerance.json"


def load_tolerance(baseline_dir):
    try:
        return json.load(open(os.path.join(baseline_dir, TOLERANCE_FILE), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def calibrate(run_dirs, baseline_dir):
    """Learn how much each cell of each view differs between LAUNCHES of the UNCHANGED game: the elementwise maximum over these runs of (run's picture vs the baseline picture), added to
    what was learned before. The runs must be of the same unchanged package as the baseline: a run that contains a real change would teach the check to ignore it."""
    cur = load_tolerance(baseline_dir) or {"runs": 0, "tolerance": {}, "calibrated_from": []}
    for run in run_dirs:
        for name in views():
            cm = SC.cell_diff(os.path.join(run, name + ".png"), os.path.join(baseline_dir, name + ".png"), HUD_BOXES)
            old = cur["tolerance"].get(name)
            cur["tolerance"][name] = [[round(max(cm[r][k], old[r][k] if old else 0.0), 2) for k in range(4)] for r in range(4)]
        cur["runs"] += 1
        cur["calibrated_from"].append(os.path.basename(run))
    cur["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(cur, open(os.path.join(baseline_dir, TOLERANCE_FILE), "w", encoding="utf-8"), indent=0)
    return cur


def evaluate(run_dir, baseline_dir, names=None):
    """Compare each view of a run with the baseline, using its own repeat as the control. Returns {views: [...], changed: [...], missing: [...]}."""
    names = names or views()
    tol = (load_tolerance(baseline_dir) or {}).get("tolerance")
    out, changed, missing = [], [], []
    for name in names:
        cur, rep, base = (os.path.join(run_dir, name + ".png"), os.path.join(run_dir, name + "__r.png"), os.path.join(baseline_dir, name + ".png"))
        absent = [os.path.basename(p) for p in (cur, rep, base) if not os.path.isfile(p)]
        if absent:
            missing.append({"view": name, "missing": absent})
            continue
        pic = os.path.join(run_dir, "diff_%s.png" % name)
        r = SC.compare(cur, base, control=rep, picture=pic, ignore=HUD_BOXES, min_levels=MIN_LEVELS, tolerance=(tol or {}).get(name))
        flagged = r["verdict"].startswith("CHANGED")
        noisy = sum(1 for row in r["noise_floor"]["cell_mean_abs_diff"] for v in row if v > 8.0)
        item = {"view": name, "status": "changed" if flagged else "same", "noise_mad": r["noise_floor"]["mean_abs_diff"], "noisy_cells": noisy,
                "changed_cells": r["changed_cells"], "verdict": r["verdict"]}
        if flagged:
            item["difference_picture"] = pic
            changed.append(name)
        else:
            try:
                os.remove(pic)
            except OSError:
                pass
        out.append(item)
    return {"views": out, "changed": changed, "missing": missing, "compared": len(out)}


def accept(run_dir, baseline_dir, meta):
    if os.path.isdir(baseline_dir) and os.listdir(baseline_dir):          # keep the baseline being replaced
        shutil.copytree(baseline_dir, baseline_dir + "_prev_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(baseline_dir, exist_ok=True)
    stored = []
    for name in views():
        src = os.path.join(run_dir, name + ".png")
        if not os.path.isfile(src):
            raise SystemExit("cannot accept: %s is missing from the run" % src)
        shutil.copyfile(src, os.path.join(baseline_dir, name + ".png"))
        stored.append(name)
    try:
        os.remove(os.path.join(baseline_dir, TOLERANCE_FILE))              # a new baseline has no learned tolerance yet
    except OSError:
        pass
    meta = dict(meta, views=stored, accepted=time.strftime("%Y-%m-%d %H:%M:%S"))
    json.dump(meta, open(os.path.join(baseline_dir, "manifest.json"), "w", encoding="utf-8"), indent=1)
    return stored


def capture(out_dir, build, inject_fault):
    os.makedirs(out_dir, exist_ok=True)
    shots = os.path.join(out_dir, "shots.txt")
    open(shots, "w", encoding="utf-8").write("\n".join(shot_lines(inject_fault)) + "\n")
    r = subprocess.run([sys.executable, "-u", _paths.script("view_shots.py"), build, out_dir, shots, "--tod", "12"], stdin=subprocess.DEVNULL, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit("view_shots.py failed (exit %d)" % r.returncode)
    missing = [n for n in views() for suffix in ("", "__r") if not os.path.isfile(os.path.join(out_dir, n + suffix + ".png"))]
    if missing:
        raise SystemExit("the capture is incomplete: %s" % missing[:8])


def main():
    a = sys.argv[1:]
    if "--out" not in a:
        print(__doc__)
        return 2
    out_dir = os.path.abspath(a[a.index("--out") + 1])
    do_accept, fault = "--accept" in a, "--inject-fault" in a
    if "--calibrate-from" in a:                                 # seed the tolerance from stored runs of the unchanged game (no game launched)
        runs = [os.path.abspath(x) for x in a[a.index("--calibrate-from") + 1:] if not x.startswith("--")]
        t = calibrate(runs, BASELINE)
        print("RESULT visual: calibrated on %d run(s) in all (%s)" % (t["runs"], ", ".join(t["calibrated_from"])))
        return 0
    if "--reevaluate" in a:                                     # judge a stored run again (no game): used to calibrate MIN_LEVELS on pictures already taken
        run = os.path.abspath(a[a.index("--reevaluate") + 1])
        rep = evaluate(run, BASELINE)
        print("RESULT visual (re-evaluated %s): %d views, %d changed%s" % (os.path.basename(run), rep["compared"], len(rep["changed"]), (" (%s)" % ", ".join(rep["changed"])) if rep["changed"] else ""))
        return 0 if not rep["changed"] else 1
    import jobs as J
    build = J.BUILDS["release"]
    fresh = None
    try:
        import health
        fresh = health.package_freshness("release")
        if fresh["stale"]:
            print("NOTE: the release package is %s: %s" % ("stale" if not fresh.get("only_level_resaved") else "only level-re-saved", "; ".join(fresh["reasons"])))
    except Exception as exc:                                    # noqa: BLE001 - the freshness note is a courtesy
        print("NOTE: could not read the package's freshness: %s" % exc)
    if not do_accept and not os.path.isfile(os.path.join(BASELINE, "manifest.json")):
        print("RESULT visual: no baseline yet: run once with accept=true on a package you have looked at and approve of")
        return 2
    if do_accept and fault:
        print("refused: --accept with --inject-fault would store the fault as the baseline")
        return 2
    if "--calibrate" in a and fresh is None:
        print("RESULT visual: refused to calibrate: the package's freshness is unknown (see the NOTE above), so it cannot be shown to be the one the baseline was taken from")
        return 2
    if "--calibrate" in a and fresh and base_manifest_package(fresh) is not None and fresh["built"] != base_manifest_package(fresh):
        print("RESULT visual: refused to calibrate: the package (%s) is not the one the baseline was taken from (%s): a run of a CHANGED game would teach the check to ignore the change" % (
            fresh["built"], base_manifest_package(fresh)))
        return 2
    capture(out_dir, build, fault)
    if "--calibrate" in a:
        t = calibrate([out_dir], BASELINE)
        print("RESULT visual: calibration run added: the tolerance now rests on %d launch(es) of the unchanged game" % t["runs"])
        return 0
    if do_accept:
        stored = accept(out_dir, BASELINE, {"package_built": fresh["built"] if fresh else None, "package_stale_note": fresh["reasons"] if fresh else None, "build": "release",
                                            "night_hour": NIGHT_HOUR})
        print("RESULT visual: baseline accepted: %d views stored in %s" % (len(stored), BASELINE))
        return 0
    rep = evaluate(out_dir, BASELINE)
    base_meta = json.load(open(os.path.join(BASELINE, "manifest.json"), encoding="utf-8"))
    for v in rep["views"]:
        where = ""
        if v["changed_cells"]:
            best = max(v["changed_cells"], key=lambda f: f["a_vs_b"] - f["noise"])
            where = "  %d cell(s), most in %s (%.1f vs noise %.1f)" % (len(v["changed_cells"]), best["where"], best["a_vs_b"], best["noise"])
        print("  %-8s %-16s noise %5.1f%s" % (v["status"].upper() if v["status"] == "changed" else v["status"], v["view"], v["noise_mad"], where))
    for m in rep["missing"]:
        print("  MISSING  %-16s %s" % (m["view"], m["missing"]))
    json.dump(dict(rep, baseline=base_meta, fault_injected=fault), open(os.path.join(out_dir, "visual_report.json"), "w"), indent=1)
    night = NIGHT_NAMES()
    if fault:
        days = [n for n, _ in DAY]
        hit_day = [n for n in rep["changed"] if n in days]
        hit_night = [n for n in rep["changed"] if n in night]
        ok = len(hit_day) >= FAULT_MIN_DAY_FLAGGED and not rep["missing"]
        print("RESULT visual (fault test): picture 30%% darker: %d of %d day views flagged, %d of %d night views flagged (night frames are dark: a 30%% change there is a few grey levels) -> the harness %s" % (
            len(hit_day), len(days), len(hit_night), len(night), "CAN fail: PASS" if ok else "did NOT see it: FAIL"))
        return 0 if ok else 1
    print("RESULT visual: %d views compared against the baseline of %s, %d changed%s%s" % (
        rep["compared"], base_meta.get("accepted", "?"), len(rep["changed"]), (" (%s)" % ", ".join(rep["changed"])) if rep["changed"] else "",
        ("; %d missing" % len(rep["missing"])) if rep["missing"] else ""))
    return 0 if not rep["changed"] and not rep["missing"] else 1


def base_manifest_package(_fresh):
    try:
        return json.load(open(os.path.join(BASELINE, "manifest.json"), encoding="utf-8")).get("package_built")
    except (OSError, ValueError):
        return None


def NIGHT_NAMES():
    return [n for n, _ in NIGHT]


if __name__ == "__main__":
    sys.exit(main())
