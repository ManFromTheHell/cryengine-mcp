#!/usr/bin/env python3
"""The level's SETTINGS (Level Settings panel, environment presets) and its exported copy (ENGINE_LEARNING_PLAN F8), checked against what the engine reads, without running the game.

What the level holds, and who reads it (manual: Level Settings; engine: Cry3DEngine/3dEngineLoad.cpp C3DEngine::LoadEnvironmentSettingsFromXML):
  Assets/levels/<level>/leveldata/environment.xml   Fog, Terrain, EnvState, VolFogShadows, VolumetricCloud, Ocean, OceanAnimation, DynTexSource   (the Level Settings > Settings tab)
  Assets/levels/<level>/leveldata/timeofday.xml     the level's LIST of environment presets and which one is the default  (Level Settings > Environment Presets tab)
  Assets/levels/<level>/level.pak                   the EXPORTED level; the game reads mission_<name>.xml in it (Environment + TimeOfDay + Objects), never the source files.

The game looks presets up BY NAME in the level's list (WorldTools.cpp FindPreset: "ashvale_clear" / "ashvale_rain"); a level whose list lacks them is not an error anywhere: the call logs one trace line and the
sky stays on whatever the list's default is. The loader reads attributes by name with atof/atoi: a misspelt attribute keeps the default, a word where a number goes reads as 0, and some values are clamped.

  V001 a preset the game code looks for is not in the level's SAVED list (leveldata/timeofday.xml): the next export drops it     V006 a value outside what the loader keeps (clamped), or a view distance under 1000
  V002 the open editor's list differs from the saved list (unsaved change, or lost)                                              V007 a texture or material path in the settings that does not exist
  V003 the EXPORTED level (level.pak) lacks a preset the game looks for, or lists others than the source                           V008 the exported entities differ from the source layers (added, removed, changed property)
  V004 the exported Environment differs from environment.xml (the export is older than the settings)                              V009 the level's default preset is the engine's default.env (the editor shows a look the game never uses)
  V005 an element or attribute in environment.xml that the 3D engine does not read (ignored silently)

    python checks/level_check.py [level]        default: the level the open editor holds (needs the editor)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")
ENGINE_DEFAULT = "%engine%/engineassets/shading/default.env"

sys.path.insert(0, HERE)

# The contract of C3DEngine::LoadEnvironmentSettingsFromXML (3dEngineLoad.cpp, 5.7.1): element -> {attribute: (kind, lo, hi)}; kind 'f' float (atof), 'i' integer (atoi / atol), 'b' bool, 's' string,
# 'v' vector "x,y,z"; lo/hi the range the loader clamps to (None: not clamped). An attribute in the file that is not here is not read.
CONTRACT = {
    "Fog": {"ViewDistance": ("i", None, None), "ViewDistanceLowSpec": ("i", None, None), "LDRGlobalDensMult": ("f", 0.0, None)},
    "Terrain": {"DetailLayersViewDistRatio": ("f", None, None), "HeightMapAO": ("b", None, None), "IntegrateObjects": ("b", None, None), "AutoGenerateBaseTexture": ("b", None, None),
                "SupportOfflineProceduralVegetation": ("b", None, None)},
    "EnvState": {"ConsoleMergedMeshesPool": ("i", None, None), "ShowTerrainSurface": ("b", None, None), "SunShadowsMinSpec": ("i", None, None), "SunShadowsAdditionalCascadeMinSpec": ("i", None, None),
                 "SunShadowsClipPlaneRange": ("f", None, None), "SunShadowsClipPlaneRangeShift": ("f", 0.0, 100.0), "SunShadowsFromTerrain": ("b", None, None), "UseLayersActivation": ("b", None, None)},
    "VolFogShadows": {"Enable": ("b", None, None), "EnableForClouds": ("b", None, None)},
    "VolumetricCloud": {"HorizonHeight": ("f", None, None), "GlobalCloudNoiseScale": ("v", None, None), "MaxViewableDistance": ("f", 0.0, 1000000.0), "MaxRayMarchDistance": ("f", 0.0, 200000.0),
                        "MaxRayMarchStep": ("i", 16, 512), "TilingSize": ("v", None, None), "TilingOffset": ("v", None, None), "EdgeTurbulenceNoiseScale": ("v", None, None),
                        "EdgeTurbulenceNoiseErode": ("b", None, None), "EdgeTurbulenceNoiseAbsolute": ("b", None, None), "MaxGlobalCloudDensity": ("f", 0.0, 1.0), "RemapCloudDensityMin": ("f", None, None),
                        "RemapCloudDensityMax": ("f", None, None), "AdditionalNoiseIntensity": ("f", None, None), "ShadingLOD": ("f", 0.0, 1.0), "ShadowTilingSize": ("f", 1.0, None),
                        "CloudVolumeTexture": ("s", None, None), "GlobalCloudNoiseVolumeTexture": ("s", None, None), "EdgeTurbulenceNoiseVolumeTexture": ("s", None, None)},
    "Ocean": {"Material": ("s", None, None), "CausticsMultiplier": ("f", None, None), "CausticsDistanceAtten": ("f", None, None), "CausticsTilling": ("f", None, None), "CausticDepth": ("f", None, None),
              "CausticIntensity": ("f", None, None)},
    "OceanAnimation": {"WindDirection": ("f", None, None), "WindSpeed": ("f", None, None), "WavesAmount": ("f", 0.4, 3.0), "WavesSize": ("f", 0.0, 3.0)},
    "DynTexSource": {"Width": ("i", None, None), "Height": ("i", None, None)},
    # the mission file carries the sun's dawn/dusk times in the same Environment node
    "Lighting": {"DawnTime": ("f", None, None), "DawnDuration": ("f", None, None), "DuskTime": ("f", None, None), "DuskDuration": ("f", None, None)},
}
# attributes the EDITOR writes into the Lighting node of the mission that the 3D engine does not read (the editor's own baking settings): not reported
EDITOR_ONLY = {"Lighting": None}
NUMBER = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?[fF]?\s*$")


class LevelError(RuntimeError):
    pass


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _norm(p):
    return (p or "").replace(chr(92), "/").strip().lower()


def read_presets(node):
    """A <TimeOfDay> element -> ([preset name], default name | None)"""
    names, default = [], None
    if node is None:
        return names, default
    for p in node.iter("Preset"):
        names.append(p.get("Name", ""))
        if p.get("Default") in ("1", "true"):
            default = p.get("Name", "")
    return names, default


# Prefix of the game's environment preset names ("ashvale_clear" ...); set CRYENGINE_PRESET_PREFIX for another game.
PRESET_PREFIX = (os.environ.get("CRYENGINE_PRESET_PREFIX") or "ashvale").lower()


def code_presets(code_dir=CODE):
    """The environment preset stems the game looks for: the string literals "ashvale_..." in the files that look presets up (FindPreset / GetPresetsInfos / SetCurrentPreset)."""
    stems = {}
    if not os.path.isdir(code_dir):
        return stems
    for root, _d, files in os.walk(code_dir):
        for f in files:
            if not f.lower().endswith((".cpp", ".h")):
                continue
            path = os.path.join(root, f)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if not re.search(r"GetPresetsInfos|SetCurrentPreset|FindPreset", text):
                continue
            for m in re.finditer(r'"(%s_[a-z0-9_]+)"' % re.escape(PRESET_PREFIX), text):
                stems.setdefault(m.group(1), os.path.relpath(path, PROJECT).replace(os.sep, "/"))
    return stems


def check_environment(node, where, assets=ASSETS):
    """The <Environment> element (source file or exported mission) against the loader's contract -> [finding]"""
    out = []
    for child in node:
        spec = CONTRACT.get(child.tag)
        if spec is None:
            if child.tag not in ("Lighting",):
                out.append(_finding("warning", "V005", where, "the element <%s> is not one the 3D engine reads from the Environment node: it is ignored" % child.tag))
            continue
        for attr, value in child.attrib.items():
            if attr not in spec:
                if child.tag in EDITOR_ONLY:
                    continue
                near = [a for a in spec if a.lower() == attr.lower() or a.lower().startswith(attr.lower()[:6])]
                out.append(_finding("warning", "V005", where, "<%s %s=...> is not read by the 3D engine (ignored, the default stays)%s" % (child.tag, attr, "; did you mean %s?" % near[0] if near else "")))
                continue
            kind, lo, hi = spec[attr]
            if kind in ("f", "i") and not NUMBER.match(value):
                out.append(_finding("warning", "V006", where, "<%s %s=\"%s\"> is not a number: the loader reads it as 0" % (child.tag, attr, value)))
                continue
            if kind == "v" and len([p for p in value.split(",") if NUMBER.match(p)]) != 3:
                out.append(_finding("warning", "V006", where, "<%s %s=\"%s\"> is not three comma-separated numbers" % (child.tag, attr, value)))
                continue
            if kind in ("f", "i"):
                n = float(re.sub(r"[fF]$", "", value.strip()))
                if (lo is not None and n < lo) or (hi is not None and n > hi):
                    out.append(_finding("warning", "V006", where, "<%s %s=\"%s\"> is outside %s..%s: the loader clamps it" % (child.tag, attr, value, lo, hi)))
        if child.tag == "Fog":
            n = child.get("ViewDistance")
            if n and NUMBER.match(n) and float(n) < 1000:
                out.append(_finding("warning", "V006", where, "ViewDistance %s is under 1000: the manual says it should never be set below 1000 (clipping errors)" % n))
        for attr in ("Material", "CloudVolumeTexture", "GlobalCloudNoiseVolumeTexture", "EdgeTurbulenceNoiseVolumeTexture"):
            v = child.get(attr)
            if child.tag in ("Ocean", "VolumetricCloud") and v:
                path = v.replace("%ENGINE%", "%engine%")
                if "%engine%" in path.lower():
                    continue                                    # an engine asset: the engine resolves it
                base = path.replace(chr(92), "/")
                options = [base] if "." in os.path.basename(base) else [base + ".mtl"]
                if attr != "Material":
                    options = [base]
                if not any(os.path.isfile(os.path.join(assets, o.replace("/", os.sep))) for o in options):
                    out.append(_finding("warning", "V007", where, "<%s %s=\"%s\"> names a file that is not under Assets" % (child.tag, attr, v)))
    return out


def _children(node):
    return {c.tag: dict(c.attrib) for c in node}


def check(level=None, live_presets=None, with_engine=True, assets=ASSETS, code_dir=CODE):
    """-> {ok, errors, warnings, findings, summary}; level: the folder under Assets/levels, default the level the open editor holds (the first level when no editor answers)"""
    if level is None:
        import open_level
        level = open_level.name()
    folder = os.path.join(assets, "levels", level)
    if not os.path.isdir(folder):
        raise LevelError("no level folder at %s" % folder)
    findings = []
    data = os.path.join(folder, "leveldata")
    needs = code_presets(code_dir)
    summary = {"level": level, "code_presets": sorted(needs)}

    # ---- the saved settings
    tod_path = os.path.join(data, "timeofday.xml")
    saved, saved_default = [], None
    if os.path.isfile(tod_path):
        saved, saved_default = read_presets(ET.parse(tod_path).getroot())
    else:
        findings.append(_finding("warning", "V001", "leveldata/timeofday.xml", "the level has no saved time-of-day file: it lists no environment preset"))
    summary["saved_presets"], summary["saved_default"] = saved, saved_default
    saved_norm = [_norm(p) for p in saved]
    for stem, where in sorted(needs.items()):
        if not any(stem in p for p in saved_norm):
            findings.append(_finding("error", "V001", "leveldata/timeofday.xml", "the game looks for the preset '%s' (%s) and the level's saved list does not have it (it lists: %s): the next export drops it and the game finds nothing "
                                     "to switch to" % (stem, where, ", ".join(saved) or "nothing"), "bridge.env.load libs/environmentpresets/%s.env, bridge.env.default, then save the level" % stem))
        elif not os.path.isfile(os.path.join(assets, "libs", "environmentpresets", stem + ".env")):
            findings.append(_finding("error", "V007", "libs/environmentpresets", "the preset file %s.env does not exist" % stem))
    if saved_default is not None and _norm(saved_default) == ENGINE_DEFAULT and needs:
        findings.append(_finding("warning", "V009", "leveldata/timeofday.xml", "the level's default preset is the engine's default.env: the editor opens with a look the game never uses (the game switches to %s)" % ", ".join(sorted(needs))))

    # ---- the editor's live list
    if live_presets is None and with_engine:
        try:
            import bridge_py
            r = bridge_py.send("bridge.env.presets", 30)
            live_presets = [p["name"] for p in json.loads(r.get("output") or "{}").get("presets", [])] if r.get("ok") else None
        except Exception:                                         # noqa: BLE001 - no editor: the file checks still run
            live_presets = None
    summary["live_presets"] = live_presets
    if live_presets is not None and sorted(_norm(p) for p in live_presets) != sorted(saved_norm):
        findings.append(_finding("warning", "V002", "editor", "the open editor lists %s and the saved level lists %s: an unsaved change, or the list was lost when the editor started (a save now writes the editor's list)" % (live_presets, saved)))

    # ---- the settings file
    env_path = os.path.join(data, "environment.xml")
    src_env = None
    if os.path.isfile(env_path):
        try:
            src_env = ET.parse(env_path).getroot()
            findings.extend(check_environment(src_env, "leveldata/environment.xml", assets))
        except ET.ParseError as exc:
            findings.append(_finding("error", "V005", "leveldata/environment.xml", "not valid XML: %s" % exc))

    # ---- the exported copy
    pak = os.path.join(folder, "level.pak")
    summary["exported"] = os.path.isfile(pak)
    if os.path.isfile(pak):
        try:
            with zipfile.ZipFile(pak) as z:
                missions = [n for n in z.namelist() if re.match(r"^mission_[^/]+\.xml$", n, re.I)]
                if not missions:
                    findings.append(_finding("error", "V003", "level.pak", "the exported level holds no mission_*.xml: the game has no settings, presets or entities for it"))
                for m in missions:
                    root = ET.fromstring(z.read(m))
                    tod = root.find("TimeOfDay")
                    exp, exp_default = read_presets(tod)
                    summary.setdefault("exported_presets", {})[m] = exp
                    for stem in sorted(needs):
                        if not any(stem in _norm(p) for p in exp):
                            findings.append(_finding("error", "V003", "level.pak " + m, "the EXPORTED level lacks the preset '%s' that the game looks for (it lists: %s): the packaged game would not find it" % (stem, ", ".join(exp) or "nothing"),
                                                     "save the level with the presets listed, then export it"))
                    if sorted(_norm(p) for p in exp) != sorted(saved_norm) and not any(f["code"] == "V003" and f["where"].endswith(m) for f in findings):
                        findings.append(_finding("warning", "V003", "level.pak " + m, "the exported level lists %s and the source lists %s: export again before packaging" % (exp, saved)))
                    env = root.find("Environment")
                    if env is not None and src_env is not None:
                        a, b = _children(src_env), _children(env)
                        for tag in sorted(a):
                            if tag in b and a[tag] != {k: v for k, v in b[tag].items() if k in a[tag] or tag not in CONTRACT}:
                                diff = sorted(k for k in set(a[tag]) | set(b[tag]) if a[tag].get(k) != b[tag].get(k) and (k in a[tag]))
                                if diff:
                                    findings.append(_finding("warning", "V004", "level.pak " + m, "the exported <%s> differs from environment.xml in %s (e.g. %s: exported %r, source %r): the export is older than the settings" % (
                                        tag, ", ".join(diff), diff[0], b[tag].get(diff[0]), a[tag].get(diff[0]))))
                            elif tag not in b:
                                findings.append(_finding("warning", "V004", "level.pak " + m, "the exported Environment has no <%s> that environment.xml has" % tag))
                    findings.extend(_entity_drift(root, folder, m))
        except zipfile.BadZipFile as exc:
            findings.append(_finding("error", "V003", "level.pak", "cannot be read as an archive: %s" % exc))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "summary": summary}


def _entity_drift(mission_root, folder, mission_name):
    """The entities in the exported mission against the Entity objects of the source layers -> [finding]"""
    out = []
    layers = os.path.join(folder, "layers")
    if not os.path.isdir(layers):
        return out
    src = {}
    for f in sorted(os.listdir(layers)):
        if f.lower().endswith(".lyr"):
            try:
                root = ET.parse(os.path.join(layers, f)).getroot()
            except ET.ParseError:
                continue
            for o in root.iter("Object"):
                if o.get("Type") == "Entity":
                    props = {}
                    for c in o.findall("Components/Component"):
                        p = c.find("properties")
                        props[c.get("TypeName", "")] = dict(p.attrib) if p is not None else {}
                    src[o.get("Name", "")] = (o.get("EntityClass", ""), o.get("Pos", ""), props)
                elif o.get("Type") == "EnvironmentProbe":
                    src[o.get("Name", "")] = ("*", o.get("Pos", ""), {})      # exported as an entity of the engine's own probe class
    exp = {}
    for e in mission_root.iter("Entity"):
        props = {}
        for c in e.findall("Components/Component"):
            p = c.find("properties")
            props[c.get("TypeName", "")] = dict(p.attrib) if p is not None else {}
        exp[e.get("Name", "")] = (e.get("EntityClass", ""), e.get("Pos", ""), props)
    added, removed = sorted(set(src) - set(exp)), sorted(set(exp) - set(src))
    changed = sorted(n for n in set(src) & set(exp) if (src[n][0] != "*" and src[n][0] != exp[n][0]) or (src[n][0] != "*" and src[n][2] != exp[n][2]))
    moved = sorted(n for n in set(src) & set(exp) if src[n][1] != exp[n][1] and n not in changed)
    where = "level.pak " + mission_name
    if added:
        out.append(_finding("warning", "V008", where, "%d entit%s in the source layers are not in the exported level: %s%s" % (len(added), "y" if len(added) == 1 else "ies", ", ".join(added[:6]), " ..." if len(added) > 6 else ""), "export the level again"))
    if removed:
        out.append(_finding("warning", "V008", where, "%d entit%s in the exported level are not in the source layers: %s%s" % (len(removed), "y" if len(removed) == 1 else "ies", ", ".join(removed[:6]), " ..." if len(removed) > 6 else "")))
    if changed:
        out.append(_finding("warning", "V008", where, "%d entit%s differ in class or component properties between the export and the source: %s%s" % (len(changed), "y" if len(changed) == 1 else "ies", ", ".join(changed[:6]), " ..." if len(changed) > 6 else "")))
    if moved:
        out.append(_finding("info", "V008", where, "%d entit%s moved since the export: %s" % (len(moved), "y" if len(moved) == 1 else "ies", ", ".join(moved[:6]))))
    return out


def main():
    r = check(sys.argv[1] if len(sys.argv) > 1 else None)       # no argument: the level the open editor holds
    print("%s: %d error(s), %d warning(s)" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"]))
    print("  ", json.dumps(r["summary"])[:600])
    for f in r["findings"]:
        print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:320]))


if __name__ == "__main__":
    main()
