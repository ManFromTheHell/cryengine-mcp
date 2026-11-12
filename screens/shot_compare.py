#!/usr/bin/env python3
"""Compare two pictures of the game by measurement: did a change show, how much, and where.

    shot_compare.py <a> <b> [--control <c>] [--region x0,y0,x1,y1]

a, b, c: '<job id>:<label>' (a shoot job's picture) or a path under job_runs. `c` is a SECOND capture of the unchanged view that `a` shows: the
game never draws the same frame twice (grass sways, the clouds move, the eye adapts), so the difference between a and c is the NOISE FLOOR, and a change only
counts when a-vs-b stands well above it. Without a control the numbers are given and the verdict says the noise floor is unknown. The comparison of a with
ITSELF is always run and must read 0: the instrument's own control.

Measures (on the 8-bit pictures, optionally within a region given as fractions of the width and height):
  mean_abs_diff      mean over pixels and channels of |a - b|, 0..255
  changed_fraction   share of pixels where any channel differs by more than 24
  grid               the same share in a 3 x 3 grid (rows top to bottom): where it changed
  cell_mean_abs_diff mean |a - b| in a 4 x 4 grid; with a control each cell is judged against ITS OWN repeat difference (the night sky moves ~19 levels between two captures of
                     the same view, grass ~4, a stone wall under 1), so a lamp-out change at the door is found and the sky's twinkle is not mistaken for one
  luminance          mean Rec.709 luminance of each, 0..1
  local_contrast     mean |luminance - the mean of its 4 neighbours|: the measure that showed the plaster's night stipple (22.0 -> 7.5 on a 130 x 160 px wall)

What already existed and is reused: the metrics' definitions come from lighting_probe.py (luminance) and the night-stipple test; ffmpeg decodes (no image library is
installed). New: a generic two-picture comparison with a control, and a difference picture.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib

HERE = _paths.ROOT
RUNS = os.path.join(HERE, "job_runs")
BASELINE = os.path.join(HERE, "visual_baseline")          # visual_check.py's accepted pictures
ROOTS = (RUNS, BASELINE)
THRESHOLD = 24


class CompareError(ValueError):
    pass


def resolve(ref):
    """'<job id>:<label>' or a path under job_runs."""
    if ":" in ref and not os.path.isabs(ref) and not ref.startswith("."):
        job, _, label = ref.partition(":")
        if not (job.replace("_", "").isalnum() and label.replace("_", "").replace("-", "").isalnum()):
            raise CompareError("bad picture reference %r (use '<job id>:<label>')" % ref)
        for name in (label + ".png", label + "_s.jpg"):
            p = os.path.join(RUNS, job + "_shots", name)
            if os.path.isfile(p):
                return p
        raise CompareError("no picture '%s' in job %s (it has: %s)" % (label, job, ", ".join(sorted(f[:-4] for f in os.listdir(os.path.join(RUNS, job + "_shots")) if f.endswith(".png")))
                                                                       if os.path.isdir(os.path.join(RUNS, job + "_shots")) else "no pictures"))
    real = os.path.realpath(ref if os.path.isabs(ref) else os.path.join(RUNS, ref))
    if not any(real.lower().startswith(os.path.realpath(r).lower() + os.sep) for r in ROOTS) or not os.path.isfile(real):
        raise CompareError("%r is not a picture under job_runs or visual_baseline" % ref)
    return real


def _tool(name):
    p = shutil.which(name)
    if not p:
        raise CompareError("%s not found on PATH (installed with ffmpeg)" % name)
    return p


def load(path):
    """RGB uint8 [h, w, 3] via ffmpeg (a PNG decoder would need an image library, and none is installed)."""
    import numpy as np
    r = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    try:
        w, h = [int(v) for v in r.stdout.strip().split(",")[:2]]
    except ValueError:
        raise CompareError("ffprobe could not read %s" % path)
    d = subprocess.run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    if len(d.stdout) != w * h * 3:
        raise CompareError("decoded %d bytes from %s, expected %d" % (len(d.stdout), path, w * h * 3))
    return np.frombuffer(d.stdout, dtype=np.uint8).reshape(h, w, 3)


def _crop(img, region):
    if not region:
        return img
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise CompareError("region is x0,y0,x1,y1 as fractions with x0 < x1 and y0 < y1")
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def luminance(img):
    import numpy as np
    f = img.astype(np.float32) / 255.0
    return 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]


def local_contrast(img):
    import numpy as np
    y = luminance(img)
    if y.shape[0] < 3 or y.shape[1] < 3:
        return 0.0
    mid = y[1:-1, 1:-1]
    around = (y[:-2, 1:-1] + y[2:, 1:-1] + y[1:-1, :-2] + y[1:-1, 2:]) / 4.0
    return float(np.abs(mid - around).mean())


def diff_stats(a, b, region=None):
    import numpy as np
    if a.shape != b.shape:
        raise CompareError("the pictures differ in size: %s vs %s" % (a.shape[1::-1], b.shape[1::-1]))
    a, b = _crop(a, region), _crop(b, region)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    changed = d.max(axis=2) > THRESHOLD
    h, w = changed.shape
    grid = [[round(float(changed[r * h // 3:(r + 1) * h // 3, c * w // 3:(c + 1) * w // 3].mean()), 4) for c in range(3)] for r in range(3)]
    return {"mean_abs_diff": round(float(d.mean()), 3), "changed_fraction": round(float(changed.mean()), 4), "grid": grid}


CELLS = 4


def cell_mads(a, b, region=None, n=CELLS):
    """Mean |a - b| in each cell of an n x n grid (row 0 = top): the noise floor differs enormously between regions (the night sky moved 19 levels between two
    captures of the same view, the grass 3.6, a stone wall 0.5), so a change is judged cell by cell against that cell's own repeat difference."""
    import numpy as np
    a, b = _crop(a, region), _crop(b, region)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).mean(axis=2)
    h, w = d.shape
    return [[float(d[r * h // n:(r + 1) * h // n, c * w // n:(c + 1) * w // n].mean()) for c in range(n)] for r in range(n)]


def write_png(path, img):
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw, 6)) + ch(b"IEND", b""))


def diff_picture(a, b, out, flagged=None):
    """Where they differ, as a picture: b dimmed. Without a control every difference is red (gain x4). With one, RED marks the cells that changed beyond
    their own noise and BLUE the differences that are within it (the sky's twinkle, swaying grass): the first version painted them all red and the
    night sky looked as changed as the lamp-lit door."""
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2).astype(np.float32)
    base = b.astype(np.float32) * 0.35
    pic = base.copy()
    if flagged is None:
        pic[..., 0] = np.clip(base[..., 0] + d * 4.0, 0, 255)
    else:
        h, w = d.shape
        mask = np.zeros((h, w), dtype=bool)
        for r, k in flagged:
            mask[r * h // CELLS:(r + 1) * h // CELLS, k * w // CELLS:(k + 1) * w // CELLS] = True
        pic[..., 0] = np.clip(base[..., 0] + np.where(mask, d * 4.0, 0), 0, 255)
        pic[..., 2] = np.clip(base[..., 2] + np.where(mask, 0, d * 3.0), 0, 255)
    write_png(out, pic.astype(np.uint8))
    return out


def mask(img, boxes):
    """A copy with each box (x0, y0, x1, y1 as fractions) painted black: the same in every picture compared, so it can never differ (the HUD's health bar, a text banner)."""
    if not boxes:
        return img
    out = img.copy()
    h, w = out.shape[:2]
    for x0, y0, x1, y1 in boxes:
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise CompareError("an ignore box is x0,y0,x1,y1 as fractions with x0 < x1 and y0 < y1")
        out[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 0
    return out


def cell_diff(path_a, path_b, ignore=None):
    """The 4x4 grid of mean |a - b| between two stored pictures (HUD boxes blanked): what one launch differs from another by, cell by cell."""
    return cell_mads(mask(load(resolve(path_a)), ignore), mask(load(resolve(path_b)), ignore))


def compare(ref_a, ref_b, control=None, region=None, picture=True, ignore=None, min_levels=3.0, tolerance=None, tol_factor=1.5, tol_margin=2.0):
    """ignore: boxes [[x0,y0,x1,y1], ...] (fractions) blanked in every picture before anything is measured. min_levels: a cell counts as changed only if it differs from the
    other picture by at least this many grey levels MORE than the control does (default 3). tolerance: a 4x4 matrix of how much each cell differs between LAUNCHES of the unchanged game
    (visual_check learns it): a cell is only flagged if it also exceeds tol_factor x that + tol_margin, because the in-run repeat cannot see launch-to-launch variation."""
    pa, pb = resolve(ref_a), resolve(ref_b)
    a, b = mask(load(pa), ignore), mask(load(pb), ignore)
    out = {"a": ref_a, "b": ref_b, "region": region}
    # the instrument's own control: a picture against itself must read exactly 0
    own = diff_stats(a, a, region)
    out["self_check"] = own["mean_abs_diff"]
    if own["mean_abs_diff"] != 0 or own["changed_fraction"] != 0:
        raise CompareError("the comparison of a picture with itself is not 0 (%s): the instrument is broken" % own)
    ab = diff_stats(a, b, region)
    out.update(ab)
    ya, yb = luminance(_crop(a, region)), luminance(_crop(b, region))
    out["luminance"] = {"a": round(float(ya.mean()), 4), "b": round(float(yb.mean()), 4), "change": round(float(yb.mean() - ya.mean()), 4)}
    out["local_contrast"] = {"a": round(local_contrast(_crop(a, region)), 4), "b": round(local_contrast(_crop(b, region)), 4)}
    cells = cell_mads(a, b, region)
    cells_flagged = set()
    out["cell_mean_abs_diff"] = [[round(v, 1) for v in row] for row in cells]
    if control:
        c = mask(load(resolve(control)), ignore)
        noise = diff_stats(a, c, region)
        nc = cell_mads(a, c, region)
        out["noise_floor"] = {"control": control, "mean_abs_diff": noise["mean_abs_diff"], "changed_fraction": noise["changed_fraction"],
                              "cell_mean_abs_diff": [[round(v, 1) for v in row] for row in nc]}
        flagged = []
        for r in range(CELLS):
            for k in range(CELLS):
                # clearly above this cell's own repeat difference: 3x it and at least `min_levels` more
                if cells[r][k] >= 3.0 * nc[r][k] and cells[r][k] - nc[r][k] >= min_levels and (tolerance is None or cells[r][k] > tol_factor * tolerance[r][k] + tol_margin):
                    flagged.append({"cell": "r%dc%d" % (r, k), "where": "rows %d-%d%% from the top, columns %d-%d%% from the left" % (
                        100 * r // CELLS, 100 * (r + 1) // CELLS, 100 * k // CELLS, 100 * (k + 1) // CELLS),
                        "a_vs_b": round(cells[r][k], 1), "noise": round(nc[r][k], 1), "times_noise": round(cells[r][k] / max(nc[r][k], 0.1), 1)})
        out["changed_cells"] = flagged
        cells_flagged = {(int(f["cell"][1]), int(f["cell"][3])) for f in flagged}
        noisy = sum(1 for row in nc for v in row if v > 8.0)
        if flagged:
            best = max(flagged, key=lambda f: f["a_vs_b"] - f["noise"])
            out["verdict"] = "CHANGED in %d of %d cells, most in %s (%.1f vs a noise floor of %.1f there)" % (len(flagged), CELLS * CELLS, best["where"], best["a_vs_b"], best["noise"])
        else:
            out["verdict"] = "NO MEASURABLE CHANGE beyond the noise floor in any of the %d cells" % (CELLS * CELLS)
        if noisy:
            out["verdict"] += ". Note: %d cell(s) are noisy between two captures of the same view (sky, grass, water), so a change there would be hidden" % noisy
    else:
        out["verdict"] = ("no control given, so the noise floor is unknown (two captures of the SAME view differ too: the night sky by ~19 levels, grass by ~4): "
                          "%.1f%% of pixels differ by more than %d. Take a second picture of the unchanged view and pass it as control." % (100 * ab["changed_fraction"], THRESHOLD))
    if picture and not region:
        # picture=True: job_runs/compare/diff_<time>.png; picture='<path>': there (the caller names it, e.g. one per view)
        target = picture if isinstance(picture, str) else os.path.join(RUNS, "compare", "diff_%s.png" % time.strftime("%H%M%S"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        out["difference_picture"] = diff_picture(a, b, target, cells_flagged if control else None)
    return out


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    control = sys.argv[sys.argv.index("--control") + 1] if "--control" in sys.argv else None
    region = [float(v) for v in sys.argv[sys.argv.index("--region") + 1].split(",")] if "--region" in sys.argv else None
    args = [x for x in args if x != control and (not region or x != sys.argv[sys.argv.index("--region") + 1])]
    if len(args) != 2:
        print(__doc__)
        return 2
    print(json.dumps(compare(args[0], args[1], control, region), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
