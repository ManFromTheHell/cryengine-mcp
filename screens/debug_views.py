#!/usr/bin/env python3
"""The engine's debug views as a catalog, and a way to LOOK at them in the editor viewport (ENGINE_LEARNING_PLAN G7). The game is not run.

CRYENGINE ships the definitions of its debug views as Python in the editor's own scripts (Editor/Scripts/viewmodes.py, environmentmodes.py, DebugViews/debug_views.py): which console
variable to set to which value to see normals, albedo, wireframe, LOD index, texel density, collision shapes, no fog, no GI ... This module reads those scripts (nothing is re-typed
from memory), checks every variable against the console-variable catalog (cvar_catalog) and its help text, and drives them: set the variables, take an editor viewport picture, put every
variable back and check that it is back.

    python screens/debug_views.py                       list the views
    python screens/debug_views.py <view> [eye x y z] [target x y z]     one picture (to job_runs/)

The editor accepts cheat and const variables (dev mode), so r_DebugGBuffer, e_DefaultMaterial, p_draw_helpers work here; a Release GAME would refuse most of them (cvar_info says).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ast
import os
import re
import subprocess
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_py  # noqa: E402
import cvar_catalog  # noqa: E402

ENGINE = os.environ.get("CRYENGINE_ENGINE") or os.environ.get("CRYENGINE_ENGINE_DIR") or (os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1"))
SCRIPTS = os.path.join(ENGINE, "Editor", "Scripts")
SHOTS = os.path.join(HERE, "job_runs")
# the Perspective pane inside the editor window capture (calibrated on the 1018x428 window; any other window size is returned whole)
VIEWPORT_CROP = {(1018, 428): (286, 137, 774, 421), (1018, 453): (286, 137, 774, 446)}   # the 1018x453 window has the same layout with a 25 px taller pane


class DebugViewError(RuntimeError):
    pass


def _read(*parts):
    path = os.path.join(SCRIPTS, *parts)
    if not os.path.isfile(path):
        raise DebugViewError("the engine's script %s is not there" % path)
    return open(path, encoding="utf-8", errors="replace").read()


def _value(text):
    text = text.strip()
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text.strip("'\"")


def _blocks(text):
    """`elif mode == 'X':` -> body, for the viewmodes/environmentmodes scripts."""
    parts = re.split(r"\n\s*(?:el)?if mode == '(\w+)':", text)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def parse_modes(text, group):
    """viewmodes.py / environmentmodes.py -> views: [{id, group, sets: [(cvar, value)], reset: [(cvar, value)], cycle: [...]}]."""
    out = []
    text = re.sub(r"float\(([-\d.]+)\)", r"\1", text)                       # float(256) -> 256
    for mode, body in _blocks(text):
        t = re.search(r"toggleCvarsValue\('mode_%s' % mode, '(\w+)', ([^,]+), ([^)]+)\)", body)
        c = re.search(r"cycle(?:List\d?)\s*=\s*\[(.*?)\]", body, re.S)
        if t:
            out.append({"id": "%s:%s" % (group, mode), "group": group, "name": mode, "sets": [(t.group(1), _value(t.group(3)))], "reset": [(t.group(1), _value(t.group(2)))], "cycle": []})
        elif c:
            steps = re.findall(r'"([^"]+)"', c.group(1))
            pairs = [tuple(s.split(None, 1)) for s in steps]
            first = pairs[0][0] if pairs else ""
            out.append({"id": "%s:%s" % (group, mode), "group": group, "name": mode, "sets": [], "reset": [(first, _value(pairs[0][1]))] if pairs else [],
                        "cycle": [(a, _value(b)) for a, b in pairs]})
        elif mode == "Fullshading":
            out.append({"id": "%s:%s" % (group, mode), "group": group, "name": mode, "sets": [("r_DebugGBuffer", 0)], "reset": [], "cycle": []})
    return out


def parse_debug_views(text):
    """debug_views.py -> views from its cvar('name', default, 'category', ...).add_value(v, 'label') blocks and the auxiliary variables each function sets."""
    out = []
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#") or l.lstrip().startswith("####"))   # the commented-out soundfx view must not leak into the one before it
    for fn, body in re.findall(r"\ndef (\w+)\(\):\n(.*?)(?=\ndef |\n####|\Z)", text, re.S):
        if fn in ("init_cvars", "reset_cvars", "set_auxiliary_cvar", "main"):
            continue
        m = re.search(r"(\w+)\s*=\s*cvar\('(\w+)',\s*([^,]+),", body)
        if not m:
            continue
        var, name, default = m.group(1), m.group(2), _value(m.group(3))
        aux = [(a, _value(v), _value(n)) for a, v, n in re.findall(r"set_auxiliary_cvar\('(\w+)',\s*(.+?),\s*(.+?)\)\s*$", body, re.M) if "int(general" not in v and "cvar" not in a.lower()[:0]]
        aux = [(a, d, n) for a, d, n in aux]
        for v, label in re.findall(r"%s\.add_value\(([^,]+),\s*'([^']*)'\)" % re.escape(var), body):
            out.append({"id": "debug_views:%s=%s" % (name, _value(v)), "group": "debug_views." + fn, "name": "%s %s" % (name, _value(v)), "label": label,
                        "sets": [(name, _value(v))] + [(a, n) for a, d, n in aux], "reset": [(name, default)] + [(a, d) for a, d, n in aux], "cycle": []})
    return out


def _help_values(help_text):
    """`  3 - Reflectance` lines in a variable's help -> {3: 'Reflectance'}"""
    return {int(m.group(1)): m.group(2).strip() for m in re.finditer(r"^\s*(-?\d+)\s*-\s*(.+)$", help_text or "", re.M)}


def catalog():
    views = parse_modes(_read("viewmodes.py"), "render") + parse_modes(_read("environmentmodes.py"), "environment") + parse_debug_views(_read("DebugViews", "debug_views.py"))
    try:
        cat = cvar_catalog.load()
    except RuntimeError:
        cat = None
    by_name = {e["name"].lower(): e for e in cat["entries"]} if cat else {}
    for v in views:
        notes = []
        for name, value in v["sets"] + v["reset"]:
            e = by_name.get(name.lower())
            if cat and not e:
                notes.append("%s is not a variable of this editor" % name)
            elif e:
                labels = _help_values(e["help"])
                if isinstance(value, int) and value in labels and not v.get("label"):
                    v["label"] = labels[value]
                if isinstance(value, int) and len(labels) >= 3 and value not in labels and value != 0:
                    notes.append("%s=%s is not among the values its own help lists (%s)" % (name, value, sorted(labels)))
        if v.get("label") and v["group"].startswith("debug_views") and v["sets"]:
            e = by_name.get(v["sets"][0][0].lower())
            labels = _help_values(e["help"]) if e else {}
            val = v["sets"][0][1]
            if labels and isinstance(val, int) and val in labels and labels[val].lower() != v["label"].lower().rstrip("."):
                notes.append("the script calls %s=%s %r but the variable's help says %r (the script is stale: trust the help)" % (v["sets"][0][0], val, v["label"], labels[val]))
        v["notes"] = list(dict.fromkeys(notes))
        v.setdefault("label", "")
    return views


def get(view_id):
    for v in catalog():
        if v["id"].lower() == view_id.lower() or v["name"].lower() == view_id.lower():
            return v
    raise DebugViewError("no debug view %r: list them with debug_views()" % view_id)


# ---- looking at one --------------------------------------------------------------------------------------------------------------------

def _cvar_get(name):
    out = bridge_py.send("bridge.cvar_get " + name, 20)
    if not out.get("ok"):
        raise DebugViewError("no variable %s" % name)
    import json
    return json.loads(out["output"])["value"]


def _cvar_set(name, value):
    bridge_py.send("bridge.cvar_set %s %s" % (name, value), 20)


def crop_viewport(png_path):
    """Crop the editor window capture to its Perspective pane when the window has the known size; else leave it whole. -> path"""
    try:
        import struct
        import zlib
    except ImportError:
        return png_path
    data = open(png_path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return png_path
    w, h = struct.unpack(">II", data[16:24])
    box = VIEWPORT_CROP.get((w, h))
    if not box:
        # Any other window size (a restarted editor comes back at its saved size, and its panel layout fixes a minimum, e.g. 1018 x 519): ask the editor where its 3D surface is (editor_window.py)
        try:
            import editor_window
            if editor_window.size() == (w, h):
                box = editor_window.viewport_box()
        except Exception as e:                                  # noqa: BLE001
            print("debug_views: viewport box unavailable (%s: %s)" % (type(e).__name__, e), file=sys.stderr)
            box = None
        if box:
            box = (max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h))
    if not box:
        print("debug_views: the %dx%d window capture was NOT cropped to the viewport (the whole window is returned)" % (w, h), file=sys.stderr)
        return png_path
    # decode (8-bit RGB/RGBA, no interlace), crop, re-encode
    pos, idat, bpp = 8, b"", 3
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            bit, color = body[8], body[9]
            bpp = {2: 3, 6: 4}.get(color, 0)
            if bit != 8 or not bpp:
                return png_path
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        rows.append(line)
        prev = line
    x0, y0, x1, y1 = box
    out = b"".join(b"\x00" + bytes(r[x0 * bpp:x1 * bpp]) for r in rows[y0:y1])

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    cropped = png_path[:-4] + "_view.png"
    with open(cropped, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", x1 - x0, y1 - y0, 8, 2 if bpp == 3 else 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(out, 6)) + chunk(b"IEND", b""))
    return cropped


MONOCHROME_SATURATION = 2.0      # a wireframe (white lines on dark) measures about 3.8; an undrawn viewport is flat grey, near 0 AND without structure (checked below)
MIN_STRUCTURE = 10.0             # luminance standard deviation of a picture that really shows something; flat grey has ~0


def shoot(path_stem, eye=None, target=None, min_saturation=None):
    """One editor viewport picture -> a PNG path (the whole editor window, or its Perspective pane when the window has the known size)."""
    os.makedirs(SHOTS, exist_ok=True)
    bmp = os.path.join(SHOTS, path_stem + ".bmp")
    if eye is None:
        cam = bridge_py.send("bridge.camera.get", 20)
        import json
        c = json.loads(cam["output"])
        eye = c["pos"]
        fwd = c["forward"]
        target = [eye[0] + fwd[0] * 30, eye[1] + fwd[1] * 30, eye[2] + fwd[2] * 30]
    spec = os.path.join(SHOTS, path_stem + ".txt")
    with open(spec, "w", encoding="utf-8") as f:
        f.write("%s|%s|%s|%s\n" % (bmp.replace(chr(92), "/"), " ".join("%g" % c for c in eye), " ".join("%g" % c for c in target), "" if min_saturation is None else "%g" % min_saturation))
    r = subprocess.run([sys.executable, _paths.script("bridge_shoot.py"), spec], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=240)
    if "never settled" in r.stdout and "0 shot(s) never settled" not in r.stdout:
        raise DebugViewError("the viewport never settled: %s" % r.stdout[-200:])
    png = bmp[:-4] + ".png"
    subprocess.run([sys.executable, _paths.script("bmp_to_png.py"), bmp, png], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    if not os.path.isfile(png):
        raise DebugViewError("no picture was written: %s" % r.stdout[-200:])
    cropped = crop_viewport(png)
    if min_saturation is not None:
        # the lower saturation bar lets a monochrome view through, so the picture must show STRUCTURE: an undrawn flat-grey viewport has near-zero saturation too
        import numpy as np
        import shot_compare
        a = shot_compare.load(cropped).astype(float)
        if float(a.mean(axis=2).std()) < MIN_STRUCTURE:
            raise DebugViewError("the viewport drew a flat picture (luminance spread %.1f under %.0f): not a live frame" % (float(a.mean(axis=2).std()), MIN_STRUCTURE))
    return cropped


def look(view_id, eye=None, target=None, step=0):
    """Apply a debug view, take one picture, put every variable back and VERIFY it is back. -> {view, picture, set, restored}"""
    v = get(view_id)
    sets = v["sets"] if not v["cycle"] else [v["cycle"][step % len(v["cycle"])]]
    resets = {name: value for name, value in v["reset"]}
    for name, _ in sets:
        resets.setdefault(name, None)
    before = {name: _cvar_get(name) for name in resets}
    applied = {}
    try:
        for name, value in sets:
            _cvar_set(name, value)
            applied[name] = _cvar_get(name)
        mono = "wireframe" in v["id"].lower()               # white lines on dark: saturation about 3.8, below the colour bar of 8
        picture = shoot("dv_" + re.sub(r"\W+", "_", v["id"]) + ("_%d" % step if v["cycle"] else ""), eye, target, MONOCHROME_SATURATION if mono else None)
    finally:
        for name, old in before.items():
            try:
                _cvar_set(name, old)
            except Exception:                                     # noqa: BLE001 - reported below by the read-back
                pass
    restored = {name: _cvar_get(name) for name in before}
    not_back = {n: (before[n], restored[n]) for n in before if str(before[n]) != str(restored[n])}
    return {"view": v["id"], "label": v["label"], "picture": picture, "set": applied, "was": before, "restored": not not_back, "not_restored": not_back}


def main():
    args = sys.argv[1:]
    if not args:
        for v in catalog():
            print("%-34s %-24s %s %s" % (v["id"], v["label"][:24], v["sets"][:2], ("  ! " + "; ".join(v["notes"])) if v["notes"] else ""))
        return
    nums = [float(a) for a in args[1:]]
    print(look(args[0], nums[:3] or None, nums[3:6] or None))


if __name__ == "__main__":
    main()
