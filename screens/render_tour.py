#!/usr/bin/env python3
"""Render feature tour (ENGINE_LEARNING_PLAN G1): switch one rendering variable at a time, take a picture of the EDITOR viewport against a control, put the variable back and read it back.

The question it answers is "which of the engine's lighting, shadow, post-processing and detail switches visibly matter in THIS scene, and by how much", with numbers (mean grey-level difference
against the noise between two pictures of the same view, the share of the picture that changed, and where) and the pictures. The game is not run: the editor viewport is the engine's own renderer
(a Release game refuses most cheat variables; `cvar_info` says which). Each variable's label is the engine's own help text from the CVar catalog.

    python screens/render_tour.py                          the default tour on the village view
    python screens/render_tour.py e_Shadows=0 r_ssdo=0     chosen settings
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_py  # noqa: E402,F401
import cvar_catalog as C  # noqa: E402
import debug_views as D  # noqa: E402
import shot_compare as S  # noqa: E402

DEFAULT_EYE = [487, 360, 42]
DEFAULT_TARGET = [487, 400, 36]

# One switch each: the value that turns the feature OFF (or, for the few marked, ON). Names come from the engine's own catalog (the test checks every one exists).
DEFAULT_TOUR = [
    ("e_Shadows", 0), ("r_ssdo", 0), ("r_HeightMapAO", 0), ("r_HDRBloom", 0), ("r_DepthOfField", 0), ("r_MotionBlur", 0), ("r_ColorGrading", 0), ("r_Flares", 0), ("r_SunShafts", 0),
    ("r_FogShadows", 0), ("r_VolumetricClouds", 0), ("e_Clouds", 0), ("e_Vegetation", 0), ("e_MergedMeshes", 0), ("e_Decals", 0), ("e_TerrainDetailMaterials", 0), ("r_Reflections", 0),
    ("r_SSReflections", 0), ("r_AntialiasingMode", 0), ("r_Sharpening", 1.0), ("r_ChromaticAberration", 1.0), ("e_Wind", 0), ("e_Sun", 0), ("r_PostProcessEffects", 0),
]


class TourError(RuntimeError):
    pass


def parse(items):
    """['e_Shadows=0', 'r_Gamma=1.4'] -> [('e_Shadows', '0'), ('r_Gamma', '1.4')]; a name the catalog does not have is refused with the nearest ones."""
    out = []
    for it in items:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([-+0-9.eE]+)\s*$", it)
        if not m:
            raise TourError("a setting is name=number (got %r)" % it)
        if not C.get(m.group(1)):
            near = [v["name"] for v in C.search(m.group(1))[0][:5]]
            raise TourError("no console variable %r%s" % (m.group(1), " (did you mean: %s)" % ", ".join(near) if near else ""))
        out.append((m.group(1), m.group(2)))
    return out


def label(name):
    v = C.get(name)
    help_ = (v or {}).get("help") or ""
    return help_.strip().split("\n")[0][:110]


def tour(settings=None, eye=None, target=None, settle_s=1.5):
    """-> {view, noise, rows: [{name, value, was, applied, label, drift, mean_abs_diff, ratio (the difference over the larger of the noise and the drift), changed_fraction, grid, picture, restored}]}. Every variable is put back and read back."""
    import time
    settings = [(n, str(v)) for n, v in (settings if settings is not None else DEFAULT_TOUR)]
    eye = eye or DEFAULT_EYE
    target = target or DEFAULT_TARGET
    prev = S.load(D.shoot("rt_ctrl0", eye, target))
    ctrl = S.load(D.shoot("rt_ctrl1", eye, target))
    noise = S.diff_stats(prev, ctrl)["mean_abs_diff"]
    rows = []
    for name, value in settings:
        row = {"name": name, "value": value, "label": label(name)}
        try:
            was = D._cvar_get(name)
        except D.DebugViewError:
            row.update(status="no such variable in this editor")
            rows.append(row)
            continue
        row["was"] = was
        # a fresh control right before each one: the scene DRIFTS (the first tour showed a constant 5-level offset after one switch, from the shadow cache or the eye adaptation, that a single
        # control taken at the start could not see); `drift` is how far the unchanged view moved since the previous control
        ctrl2 = S.load(D.shoot("rt_ctrl_" + re.sub(r"\W+", "_", name), eye, target))
        row["drift"] = S.diff_stats(ctrl, ctrl2)["mean_abs_diff"]
        ctrl = ctrl2
        try:
            D._cvar_set(name, value)
            row["applied"] = D._cvar_get(name)
            if str(row["applied"]) != str(value) and not _same_number(row["applied"], value):
                row["status"] = "refused: the variable holds %s after the set" % row["applied"]
            else:
                time.sleep(settle_s)
                pic = D.shoot("rt_" + re.sub(r"\W+", "_", name), eye, target)
                st = S.diff_stats(ctrl, S.load(pic))
                row.update(picture=pic, mean_abs_diff=st["mean_abs_diff"], changed_fraction=st["changed_fraction"], grid=st["grid"], ratio=round(st["mean_abs_diff"] / max(noise, row["drift"], 0.05), 1), status="shot")
        finally:
            try:
                D._cvar_set(name, was)
                time.sleep(settle_s)                               # the real look (clouds, HDR, merged meshes) is heavy: a write such as r_Gamma's, even to the same value, makes the next frames stale or re-adapts the exposure (the next switch can then read about 0.2 levels off)
            except Exception:                                      # noqa: BLE001 - reported by the read-back below
                pass
        row["restored"] = _same_number(D._cvar_get(name), was) or str(D._cvar_get(name)) == str(was)
        rows.append(row)
    return {"view": {"eye": eye, "target": target}, "noise": noise, "rows": rows}


def _same_number(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def main():
    args = sys.argv[1:]
    res = tour(parse(args) if args else None)
    print("noise between two pictures of the view: %.2f" % res["noise"])
    for r in sorted(res["rows"], key=lambda r: -(r.get("mean_abs_diff") or 0)):
        print("%-28s %-6s %-9s diff %-6s drift %-6s x%-6s %s" % (r["name"], r["value"], r.get("status", "")[:9], r.get("mean_abs_diff"), r.get("drift"), r.get("ratio"), r["label"][:50]))


if __name__ == "__main__":
    main()
