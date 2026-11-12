#!/usr/bin/env python3
"""Materials against the manual's Physically Based Shading rules (ENGINE_LEARNING_PLAN B2), without the editor or the game.

Sources (the offline manual): 'Texture and Material Setup for PBS' (44959244), 'Material Editor' Lighting Settings (35259544), 'Creating Textures for PBS' (54427668), the grass tutorial
(24285888); and the engine's own shader script, which settles what Smoothness means: Illum.cfx / Vegetation.cfx / Terrain.cfx  `attribs.Smoothness = MatSpecColor.w * GetSmoothnessTex(...)`, i.e. the final
smoothness is the material's Shininess / 255 TIMES the alpha of the _ddna map (a material with no map uses the slider alone).

  S001 error    a Diffuse colour channel above 1.0 (albedo above 100% reflects more light than arrives)
  S002 info     a TEXTURED material (Diffuse + normal map) whose Diffuse colour is not 255,255,255: the manual keeps it white and puts the colour in the texture
  S003 warning  a non-metal without a specular map whose Specular colour is outside sRGB 40..60 (error above 80: 'never higher than 80/80/80'; information below 38, water's reflectance)
  S004 warning  a Specular colour that is not white while a specular MAP is used (it multiplies the map and lowers the physical values)
  S005 warning  a metal (surface type mat_metal*, a metal name, or Specular above sRGB 150) whose Specular is below sRGB 180, or whose Diffuse is not black or very dark (above 0.15)
  S006 info     Shininess below 255 with a _ddna normal map: the map's smoothness is scaled to Shininess/255 of itself
  S007 warning  a non-metal whose Specular colour is coloured (channels differ by more than 6): 'never use coloured specular for anything except metals'

    python checks/pbs_check.py [--all] [material.mtl ...]       default: the project's own materials (Assets/materials); --all adds Assets/objects (the GameSDK imports)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")

METAL_NAMES = ("steel", "gold", "iron", "copper", "silver", "brass", "bronze", "aluminum", "aluminium", "chrome")
NON_PBS_SHADERS = ("nodraw", "watervolume", "water", "referenceimage", "ashflat", "ashillum", "terrain", "particles", "distanceclouds", "sky", "skyhdr", "glass", "eye", "hair", "humanskin", "vegetation")


def srgb(v):
    """linear 0..1 -> sRGB 0..255 (the Material Editor shows the colour in sRGB)."""
    v = max(0.0, min(1.0, v))
    return round(255.0 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055))


def _triple(text, default):
    try:
        v = [float(x) for x in (text or default).split(",")]
        return v if len(v) == 3 else [float(x) for x in default.split(",")]
    except ValueError:
        return [float(x) for x in default.split(",")]


def material_facts(el, name):
    tex = {}
    node = el.find("Textures")
    for t in (node.findall("Texture") if node is not None else []):
        if t.get("File"):
            tex[(t.get("Map") or "").lower()] = t.get("File")
    return {"name": name, "shader": (el.get("Shader") or "").split(".")[0], "diffuse": _triple(el.get("Diffuse"), "1,1,1"), "specular": _triple(el.get("Specular"), "0,0,0"),
            "shininess": float(el.get("Shininess") or 0), "surface": (el.get("SurfaceType") or ""), "textures": tex}


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def is_metal(f):
    spec = [srgb(x) for x in f["specular"]]
    return f["surface"].lower().startswith("mat_metal") or any(n in f["name"].lower() for n in METAL_NAMES) or (sum(spec) / 3.0 > 150)


def check_facts(f, where):
    out = []
    if f["shader"].lower() != "illum":
        return out                                           # the rules are the Illum shader's; terrain, vegetation, skin, water, glass have their own conventions
    d, sp = f["diffuse"], [srgb(x) for x in f["specular"]]
    textured = any(k in f["textures"] for k in ("diffuse",)) and ("bumpmap" in f["textures"])
    has_spec_map = "specular" in f["textures"]
    metal = is_metal(f)
    if max(d) > 1.0 + 1e-6:
        out.append(_finding("error", "S001", where, "Diffuse colour %s: a channel above 1.0 is an albedo above 100%% (physically impossible, and it is what makes the surface glow rather than look pale)" % ",".join("%.2f" % x for x in d),
                            "put the brightness into the diffuse texture and leave the colour at 1,1,1"))
    if textured and not metal and any(abs(x - 1.0) > 0.01 for x in d) and max(d) <= 1.0 + 1e-6:
        out.append(_finding("info", "S002", where, "textured material with Diffuse colour %s: the manual keeps it at 255,255,255 and puts the colour in the texture (a tint multiplies the texture and throws away precision)" % ",".join("%.2f" % x for x in d)))
    if has_spec_map:
        if min(sp) < 250:
            out.append(_finding("warning", "S004", where, "a specular map is used and the Specular colour is sRGB %s, not 255,255,255: it multiplies the map and lowers its physical values" % ",".join(str(x) for x in sp), "set the Specular colour to white"))
    elif metal:
        if sum(sp) / 3.0 < 180:
            out.append(_finding("warning", "S005", where, "looks like a metal and its Specular is sRGB %s: metal specular should be above 180 (iron 196, aluminium 245, gold 255,219,145)" % ",".join(str(x) for x in sp)))
        if max(d) > 0.15:
            out.append(_finding("warning", "S005", where, "looks like a metal and its Diffuse is %s: metal has a black or very dark diffuse (its colour is in the specular)" % ",".join("%.2f" % x for x in d)))
    else:
        mean = sum(sp) / 3.0
        if max(sp) > 80:
            out.append(_finding("error", "S003", where, "non-metal Specular sRGB %s is above 80: the manual says never" % ",".join(str(x) for x in sp), "40..60 (typically 55)"))
        elif mean < 38 or mean > 60:
            out.append(_finding("info" if mean < 38 else "warning", "S003", where, "non-metal Specular sRGB %s is outside 40..60 (water is 38, skin 51, plastic 53..61; unknown non-metals 55)" % ",".join(str(x) for x in sp)))
        if max(sp) - min(sp) > 6:
            out.append(_finding("warning", "S007", where, "non-metal with a coloured Specular (sRGB %s): coloured specular is for metals only" % ",".join(str(x) for x in sp)))
    if "bumpmap" in f["textures"] and f["shininess"] < 254:
        out.append(_finding("info", "S006", where, "Shininess %g with a _ddna normal map: the engine multiplies the map's alpha by Shininess/255 (Illum.cfx), so the map's smoothness is scaled to %d%% of itself" % (f["shininess"], round(100 * f["shininess"] / 255.0)),
                            "255 lets the map decide; a lower value is a deliberate global roughening"))
    return out


def check(paths=None, include_imports=False):
    """-> {ok, errors, warnings, findings, checked: n sub-materials}"""
    files = []
    if paths:
        files = [p if os.path.isabs(p) else os.path.join(ASSETS, p.replace("/", os.sep)) for p in paths]
    else:
        roots = [os.path.join(ASSETS, "materials")] + ([os.path.join(ASSETS, "objects")] if include_imports else [])
        for root in roots:
            for r, _, fs in os.walk(root):
                files.extend(os.path.join(r, f) for f in sorted(fs) if f.lower().endswith(".mtl"))
    findings, n = [], 0
    for p in files:
        rel = os.path.relpath(p, ASSETS).replace(chr(92), "/")
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError):
            continue
        sub = root.find("SubMaterials")
        for el in (sub.findall("Material") if sub is not None else [root]):
            name = el.get("Name") or os.path.splitext(os.path.basename(p))[0]
            if sub is None:
                name = os.path.splitext(os.path.basename(p))[0]
            n += 1
            findings.extend(check_facts(material_facts(el, name), "%s%s" % (rel, (" [" + name + "]") if sub is not None else "")))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "checked": n}


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    r = check(args or None, "--all" in sys.argv)
    print("%s: %d error(s), %d warning(s), %d info; %d (sub-)materials" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], len(r["findings"]) - r["errors"] - r["warnings"], r["checked"]))
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:250]))


if __name__ == "__main__":
    main()
