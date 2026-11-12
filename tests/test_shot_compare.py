#!/usr/bin/env python3
"""Self-test of shot_compare.py on synthetic pictures whose differences are known, and on the real lamp experiment when its job still exists.

The lesson it encodes: the FIRST version judged the whole picture and called "all lamps out" no measurable change (1.4x the noise floor), because the night sky moved
19 levels between two captures of the same view. Now each cell of a grid is judged against its own repeat difference.

    test_shot_compare.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import shot_compare as S

DIR = os.path.join(S.RUNS, "_cmp_test")
W, H = 320, 180
results = []
rng = np.random.default_rng(7)


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-66s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def save(name, img):
    os.makedirs(DIR, exist_ok=True)
    S.write_png(os.path.join(DIR, name + ".png"), np.clip(img, 0, 255).astype(np.uint8))
    return "_cmp_test/%s.png" % name


def scene():
    """A fixed picture: a gradient 'sky' on top, a textured 'ground' below."""
    img = np.zeros((H, W, 3), dtype=np.float32)
    img[:H // 3] = np.linspace(40, 90, W)[None, :, None]
    img[H // 3:] = 70 + 20 * np.sin(np.arange(W) / 9.0)[None, :, None]
    return img


def captured(base, sky_noise=18.0, ground_noise=1.5):
    """One 'capture' of the base picture: the sky changes between captures (stars, clouds), the ground barely."""
    out = base.copy()
    out[:H // 3] += rng.normal(0, sky_noise, out[:H // 3].shape)
    out[H // 3:] += rng.normal(0, ground_noise, out[H // 3:].shape)
    return out


def refuses(name, fn, *a, **k):
    try:
        fn(*a, **k)
    except S.CompareError as exc:
        check(name, True, str(exc)[:66])
        return
    check(name, False, "was accepted")


def main():
    shutil.rmtree(DIR, ignore_errors=True)
    try:
        base = scene()
        a, a2 = save("a", captured(base)), save("a_repeat", captured(base))
        # the change: a lit patch on the ground, lower right (rows 50-100 %, columns 50-75 %)
        changed = captured(base)
        changed[H // 2:, W // 2:3 * W // 4] += 45
        b = save("b", changed)
        # a change hiding in the noisy sky
        hidden = captured(base)
        hidden[:H // 4, :W // 4] += 12
        c = save("c", hidden)

        r = S.compare(a, a2, control=a2, picture=False)
        check("the self comparison reads exactly 0 (the instrument's own control)", r["self_check"] == 0.0)
        check("a repeat of the same view against itself: no change, though the sky differs", "NO MEASURABLE CHANGE" in r["verdict"] and r["noise_floor"]["mean_abs_diff"] > 4, "noise mad %.1f" % r["noise_floor"]["mean_abs_diff"])
        r = S.compare(a, b, control=a2, picture=False)
        cells = {f["cell"] for f in r["changed_cells"]}
        check("the lit patch is found, in its cells (r2c2, r3c2) and nowhere else", cells == {"r2c2", "r3c2"} or cells == {"r2c2", "r3c2", "r2c3"} or cells <= {"r2c2", "r3c2", "r2c1", "r3c1", "r2c3", "r3c3"} and {"r2c2", "r3c2"} <= cells, sorted(cells))
        check("the noisy sky is NOT reported as changed", not any(f["cell"].startswith(("r0", "r1")) for f in r["changed_cells"]) and r["verdict"].startswith("CHANGED"), r["verdict"][:70])
        r = S.compare(a, c, control=a2, picture=False)
        check("a change hidden inside the noisy sky is NOT claimed, and the verdict says the noisy cells would hide it", "NO MEASURABLE CHANGE" in r["verdict"] and "noisy" in r["verdict"], r["verdict"][:90])
        r = S.compare(a, b, picture=False)
        check("without a control the verdict says the noise floor is unknown (it never says CHANGED)", "no control given" in r["verdict"] and "CHANGED" not in r["verdict"].split(":")[0], r["verdict"][:70])
        r = S.compare(a, b, control=a2, region=[0, 0.5, 1, 1], picture=False)
        check("a region restricts everything to it (the ground half only)", r["region"] == [0, 0.5, 1, 1] and r["noise_floor"]["mean_abs_diff"] < 4 and r["verdict"].startswith("CHANGED"), r["noise_floor"]["mean_abs_diff"])
        r = S.compare(a, a2, control=a2, region=[0, 0.5, 1, 1], picture=False)
        check("a region of the quiet ground: the repeat is the same view, no change", "NO MEASURABLE CHANGE" in r["verdict"])
        # --- ignore boxes: the same blanking in every picture, so a change under one cannot be seen and a change outside it still is
        hud = save("hud_change", np.where(np.arange(H)[:, None, None] > 0.86 * H, captured(base) + 90, captured(base)))
        r = S.compare(a, hud, control=a2, picture=False, ignore=[[0, 0.86, 0.32, 1.0]])
        cells_hud = {f["cell"] for f in r["changed_cells"]}
        check("ignore: a change under the masked box is invisible (r3c0) and the same change outside it is still found (r3c2, r3c3)", "r3c0" not in cells_hud and {"r3c2", "r3c3"} <= cells_hud, sorted(cells_hud))
        r0 = S.compare(a, hud, control=a2, picture=False)
        check("...and without the mask the same change IS found in the bottom-left cells", any(f["cell"] in ("r3c0", "r3c1") for f in r0["changed_cells"]), [f["cell"] for f in r0["changed_cells"]][:4])
        refuses("an ignore box that is not x0<x1, y0<y1 is refused", S.compare, a, b, picture=False, ignore=[[0.5, 0, 0.2, 1]])
        # --- local contrast: the stipple measure
        smooth = save("smooth", np.full((H, W, 3), 100.0))
        stipple = save("stipple", 100 + rng.choice([-40.0, 40.0], size=(H, W, 1)) * np.ones((1, 1, 3)))
        r = S.compare(smooth, stipple, picture=False)
        check("local contrast: a smooth wall reads ~0, a stippled one reads high", r["local_contrast"]["a"] < 0.001 and r["local_contrast"]["b"] > 0.1, r["local_contrast"])
        # --- the picture
        r = S.compare(a, b, control=a2)
        pic = r["difference_picture"]
        img = S.load(pic)
        red = img[H // 2:, W // 2:3 * W // 4, 0].astype(int).mean() - img[H // 2:, W // 2:3 * W // 4, 2].astype(int).mean()
        blue = img[:H // 3, :, 2].astype(int).mean() - img[:H // 3, :, 0].astype(int).mean()
        check("the difference picture: red where it changed, blue where it is only noise", img.shape == (H, W, 3) and red > 20 and blue > 3, "red-blue %.0f in the patch, blue-red %.0f in the sky" % (red, blue))
        # --- what it refuses
        small = save("small", np.zeros((90, 160, 3)))
        refuses("pictures of different sizes are refused", S.compare, a, small, picture=False)
        refuses("a path outside job_runs is refused", S.resolve, r"C:\Windows\Web\Wallpaper\Windows\img0.jpg")
        refuses("'..' out of job_runs is refused", S.resolve, "../server.py")
        refuses("a job that has no such picture is refused", S.resolve, "20261006_230818_shoot:no_such_label")
        refuses("a bad reference is refused", S.resolve, "x;y:z w")
        # --- the real experiment (all lamps out at the chapel at 22:00), when its job is still kept
        marker = os.path.join(S.RUNS, "_last_compare_job.txt")
        job = open(marker).read().strip() if os.path.isfile(marker) else ""
        if job and os.path.isdir(os.path.join(S.RUNS, job + "_shots")):
            real = S.compare("%s:n1" % job, "%s:n3" % job, control="%s:n2" % job, picture=False)
            door = [f for f in real["changed_cells"] if f["cell"] in ("r1c2", "r2c2")]
            check("REAL: all lamps out at night is found at the chapel door, in a noisy picture", real["verdict"].startswith("CHANGED") and len(door) >= 1, real["verdict"][:90])
            same = S.compare("%s:n1" % job, "%s:n2" % job, control="%s:n3" % job, picture=False)
            check("REAL: the same view twice is not reported as a change", "NO MEASURABLE CHANGE" in same["verdict"], same["verdict"][:90])
        else:
            print("  --   the real lamp experiment's job is no longer kept: skipped")
    finally:
        shutil.rmtree(DIR, ignore_errors=True)
    check("cleanup: the scratch folder is gone", not os.path.exists(DIR))
    bad = results.count(False)
    print("shot compare test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
