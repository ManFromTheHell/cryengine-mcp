#!/usr/bin/env python3
"""Explain HOW a finished CRYENGINE level is built, one system at a time.

Reads the loose layer files (layers/*.lyr), leveldata/, <name>.level and level.pak
(through crypak.py), so it works with Sandbox closed and on any project. Written while
studying GameSDK's woodland; see LEVEL_CRAFT_GUIDE.md for what the output means.

    level_study.py <level dir> [section ...] [--json]

Sections (default: all):
    overview     level size, missions, layer tree with DefaultLoaded/Exportable flags
    streaming    how layers are switched on/off (AreaShape -> AreaTrigger -> Engine:LayerSwitch)
    audio        ambience areas, trigger spots, audio area entities, switches/RTPCs, audio flowgraph
    borders      invisible collision walls, kill zones, occluder meshes, playable extent
    terrain      heightmap, surface types (terrain materials), roads, water, vegetation groups
    lighting     time of day (sun, preset), environment probes (root/child), lights, env settings
    cinematics   TrackView sequences, cameras, how they are started and what they switch
    logic        every flowgraph: node classes and the key nodes (triggers, objectives, timers)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import collections
import glob
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crypak  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TICKS_PER_SECOND = 6000.0   # SAnimTime::numTicksPerSecond
TOD_UNITS_PER_HOUR = 120.0  # environment preset spline keys: 0..2880 over 24 h

# ERF_* render-node flags (Cry3DEngine/IRenderNode.h) that matter when reading RndFlags.
ERF = {0: "GOOD_OCCLUDER", 3: "CASTSHADOWMAPS", 8: "HIDDEN", 10: "GI_MODE_BIT0", 11: "OUTDOORONLY",
       17: "NO_PHYSICS", 21: "RECVWIND", 22: "COLLISION_PROXY", 27: "RAYCAST_PROXY",
       29: "RAIN_OCCLUDER", 30: "HAS_CASTSHADOWMAPS"}


def rndflags(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return []
    return [name for bit, name in ERF.items() if n >> bit & 1]


def vec(s, n=3):
    try:
        v = [float(x) for x in (s or "").split(",")]
    except ValueError:
        v = []
    return (v + [0.0] * n)[:n]


def r(v, d=1):
    return [round(x, d) for x in v]


class Level:
    def __init__(self, level_dir):
        self.dir = os.path.abspath(level_dir)
        self.name = os.path.basename(self.dir)
        self.objects = []          # (layer_rel, layer_attrib, Element)
        self.by_id = {}
        self.layers = collections.OrderedDict()
        for path in sorted(glob.glob(os.path.join(self.dir, "layers", "**", "*.lyr"), recursive=True)):
            rel = os.path.relpath(path, os.path.join(self.dir, "layers")).replace("\\", "/")
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            for layer in root.iter("Layer"):
                self.layers[layer.get("Name")] = dict(layer.attrib, file=rel)
                for o in layer.iter("Object"):
                    self.objects.append((rel, layer.attrib, o))
                    if o.get("Id"):
                        self.by_id[o.get("Id")] = o
        self.pak = os.path.join(self.dir, "level.pak")
        self._pak_entries = crypak.entries(self.pak) if os.path.isfile(self.pak) else []

    def pak_xml(self, name):
        for e in self._pak_entries:
            if e["name"].lower() == name.lower():
                try:
                    return ET.fromstring(crypak.read(e))
                except ET.ParseError:
                    return None
        return None

    def loose_xml(self, *parts):
        p = os.path.join(self.dir, *parts)
        if os.path.isfile(p):
            try:
                return ET.parse(p).getroot()
            except ET.ParseError:
                return None
        return None

    def find(self, type_=None, cls=None, layer=None):
        for rel, la, o in self.objects:
            if type_ and not re.search(type_, o.get("Type", ""), re.I):
                continue
            if cls and not re.search(cls, o.get("EntityClass", "") or "", re.I):
                continue
            if layer and not re.search(layer, rel, re.I):
                continue
            yield rel, la, o

    def name_of(self, guid):
        o = self.by_id.get(guid)
        return o.get("Name") if o is not None else guid

    def shape_targets(self, o):
        """Entities an AreaShape/AreaBox/AreaSphere is linked to (its <Entities> list)."""
        return [self.name_of(e.get("Id")) for e in o.findall("Entities/Entity")]

    def flowgraphs(self):
        for rel, la, o in self.objects:
            fg = o.find("FlowGraph")
            if fg is not None:
                yield rel, o, fg

    def asset_root(self):
        d = self.dir
        while True:
            parent = os.path.dirname(d)
            if os.path.basename(d).lower() == "levels":
                return parent
            if parent == d:
                return None
            d = parent


# ----------------------------------------------------------------------------------------
def s_overview(L):
    out = {}
    lvl = L.loose_xml(L.name + ".level")
    ld = L.pak_xml("leveldata.xml")
    if ld is not None:
        li = ld.find("LevelInfo")
        if li is not None:
            out["level_info"] = dict(li.attrib)
    if lvl is not None:
        m = lvl.find("Missions/Mission")
        if m is not None:
            out["mission"] = {k: m.get(k) for k in ("Name", "MissionTime")}
            light = m.find("Lighting")
            if light is not None:
                out["mission_lighting"] = dict(light.attrib)
            mm = m.find("MiniMap")
            if mm is not None:
                out["minimap"] = dict(mm.attrib)
        out["archetype_libs"] = [e.get("Name") for e in lvl.findall("EntityPrototypesLibs/*")]
        out["particle_libs"] = [e.get("Name") for e in lvl.findall("ParticleLibs/*")]
    if ld is not None:
        tree = {}
        for la in ld.findall("Layers/Layer"):
            tree.setdefault(la.get("Parent") or "", []).append(la.get("Name"))
        out["layer_tree"] = tree
    counts = collections.Counter(la.get("Name") for _, la, _ in L.objects)
    out["layers"] = [{"name": n, "file": a.get("file"), "objects": counts.get(n, 0),
                      "DefaultLoaded": a.get("DefaultLoaded"), "Exportable": a.get("Exportable"),
                      "ExportLayerPak": a.get("ExportLayerPak"), "Hidden": a.get("Hidden")}
                     for n, a in L.layers.items()]
    out["baked_files"] = {e["name"]: e["usize"] for e in L._pak_entries
                          if not e["name"].startswith(("terrain/merged_meshes", "brush/"))}
    return out


def s_streaming(L):
    """AreaShape (tall prism) -> linked AreaTrigger -> flowgraph Engine:LayerSwitch nodes."""
    triggers = {}
    for rel, la, o in L.find(cls="^(AreaTrigger|ProximityTrigger)$"):
        triggers[o.get("Id")] = o.get("Name")
    shapes = []
    for rel, la, o in L.find(type_="^(Shape|AreaBox|AreaSphere)$"):
        linked = [e.get("Id") for e in o.findall("Entities/Entity")]
        hit = [triggers[g] for g in linked if g in triggers]
        if hit:
            shapes.append({"shape": o.get("Name"), "layer": rel, "height": o.get("Height"),
                           "pos": r(vec(o.get("Pos"))), "points": len(o.findall("Points/Point")),
                           "triggers": hit})
    switches = []
    for rel, o, fg in L.flowgraphs():
        nodes = {n.get("Id"): n for n in fg.iter("Node")}
        edges = [(e.get("nodeOut"), e.get("portOut"), e.get("nodeIn"), e.get("portIn")) for e in fg.iter("Edge")]
        for nid, n in nodes.items():
            if n.get("Class") in ("entity:AreaTrigger", "entity:ProximityTrigger"):
                ent = L.name_of(n.get("EntityGUID"))
                # follow up to 3 hops to the LayerSwitch nodes this trigger drives
                frontier, seen, reached = [(nid, None)], set(), []
                for _ in range(4):
                    nxt = []
                    for cur, port in frontier:
                        for a, pa, b, pb in edges:
                            if a == cur and (b, pa) not in seen:
                                seen.add((b, pa))
                                bn = nodes.get(b)
                                if bn is None:
                                    continue
                                if bn.get("Class") == "Engine:LayerSwitch":
                                    inp = bn.find("Inputs")
                                    reached.append("%s.%s->%s(%s)" % (ent, port or pa, inp.get("Layer") if inp is not None else "?", pb))
                                else:
                                    nxt.append((b, port or pa))
                    frontier = nxt
                if reached:
                    switches.append({"flowgraph": o.get("Name"), "trigger": ent, "switches": sorted(set(reached))})
    return {"trigger_shapes": shapes, "layer_switch_wiring": switches,
            "note": "Layers with DefaultLoaded=0 are loaded by these switches (Hide/Unhide ports) "
                    "when the player enters/leaves the linked area."}


def s_audio(L):
    amb, spots, aae, shapes = [], [], [], []
    for rel, la, o in L.find(cls="^AudioAreaAmbience$"):
        p = o.find("Properties")
        amb.append({"name": o.get("Name"), "layer": rel, "pos": r(vec(o.get("Pos"))),
                    **{k: p.get(k) for k in ("audioTriggerPlayTrigger", "audioTriggerStopTrigger", "audioRTPCRtpc",
                                             "fRtpcDistance", "audioEnvironmentEnvironment", "fEnvironmentDistance",
                                             "bEnabled") if p is not None and p.get(k)}})
    for rel, la, o in L.find(cls="^AudioTriggerSpot$"):
        p = o.find("Properties")
        pm = p.find("PlayMode") if p is not None else None
        spots.append({"name": o.get("Name"), "pos": r(vec(o.get("Pos"))),
                      "play": p.get("audioTriggerPlayTriggerName") if p is not None else None,
                      "enabled": p.get("bEnabled") if p is not None else None,
                      "behaviour": pm.get("eiBehaviour") if pm is not None else None,
                      "delay": [pm.get("fMinDelay"), pm.get("fMaxDelay")] if pm is not None else None,
                      "randomArea": pm.get("vectorRandomizationArea") if pm is not None else None})
    for rel, la, o in L.find(cls="^AudioAreaEntity$"):
        p = o.find("Properties")
        aae.append({"name": o.get("Name"), **({k: p.get(k) for k in ("audioEnvironmentEnvironment", "fEnvironmentDistance", "fFadeDistance")} if p is not None else {})})
    audio_names = {x["name"] for x in amb + aae}
    for rel, la, o in L.find(type_="^(Shape|AreaBox|AreaSphere)$"):
        t = L.shape_targets(o)
        if any(n in audio_names for n in t):
            shapes.append({"shape": o.get("Name"), "type": o.get("Type"), "height": o.get("Height"),
                           "radius": o.get("Radius"), "size": [o.get("Width"), o.get("Length")] if o.get("Type") == "AreaBox" else None,
                           "priority": o.get("Priority"), "group": o.get("GroupId"), "points": len(o.findall("Points/Point")),
                           "obstruct_sides": o.find("SoundData").attrib if o.find("SoundData") is not None else None,
                           "targets": t})
    fg_audio = []
    for rel, o, fg in L.flowgraphs():
        for n in fg.iter("Node"):
            c = n.get("Class", "")
            if c.startswith("Audio:"):
                inp = n.find("Inputs")
                fg_audio.append({"flowgraph": o.get("Name"), "node": c,
                                 "entity": L.name_of(n.get("EntityGUID")) if n.get("EntityGUID") else None,
                                 "inputs": {k: v for k, v in (inp.attrib.items() if inp is not None else []) if v not in ("", "0")}})
    ar = L.asset_root()
    middleware = sorted({e["name"].split("/")[1] for e in crypak.find(ar, r"^audio/[^/]+/") if e["name"].count("/") > 1}) if ar else []
    return {"ambience_entities": amb, "area_shapes_linked_to_audio": shapes, "trigger_spots": spots,
            "audio_area_entities": aae, "flowgraph_audio_nodes": fg_audio, "middleware_folders_in_paks": middleware}


def s_borders(L):
    walls = collections.defaultdict(lambda: {"count": 0, "xs": [], "ys": [], "zs": [], "flags": set(), "types": set()})
    for rel, la, o in L.objects:
        mat = (o.get("Material") or "").lower()
        fl = rndflags(o.get("RndFlags"))
        if "COLLISION_PROXY" in fl or "RAYCAST_PROXY" in fl or "collision_proxy" in mat or "nodraw" in mat \
                or (o.get("Type") == "Designer" and "GOOD_OCCLUDER" in fl):
            key = (rel, mat)
            w = walls[key]
            p = vec(o.get("Pos"))
            w["count"] += 1
            w["xs"].append(p[0]); w["ys"].append(p[1]); w["zs"].append(p[2])
            w["flags"].update(fl); w["types"].add(o.get("Type"))
    wall_rows = []
    for (rel, mat), w in walls.items():
        role = "collision wall (player/vehicle blocking, invisible in game)" if "COLLISION_PROXY" in w["flags"] \
            else "occluder mesh (culling only)" if "GOOD_OCCLUDER" in w["flags"] else "proxy"
        wall_rows.append({"layer": rel, "material": mat, "count": w["count"], "types": sorted(w["types"]),
                          "rndflags": sorted(w["flags"]), "role": role,
                          "x": [round(min(w["xs"])), round(max(w["xs"]))], "y": [round(min(w["ys"])), round(max(w["ys"]))],
                          "z": [round(min(w["zs"])), round(max(w["zs"]))],
                          "layer_flags": {k: L.layers.get(L_layer_name(L, rel), {}).get(k) for k in ("DefaultLoaded", "Exportable")}})
    kill = []
    for rel, o, fg in L.flowgraphs():
        nodes = {n.get("Id"): n for n in fg.iter("Node")}
        killers = {nid for nid, n in nodes.items() if n.get("Class") in ("Actor:KillPlayer", "Entity:Damage", "Game:KillPlayer")}
        if not killers:
            continue
        for e in fg.iter("Edge"):
            if e.get("nodeIn") in killers:
                src = nodes.get(e.get("nodeOut"))
                if src is not None and src.get("EntityGUID"):
                    trig = L.name_of(src.get("EntityGUID"))
                    shapes = [so.get("Name") for _, _, so in L.find(type_="^(Shape|AreaBox|AreaSphere)$")
                              if trig in L.shape_targets(so)]
                    kill.append({"flowgraph": o.get("Name"), "trigger": trig, "port": e.get("portOut"),
                                 "action": nodes[e.get("nodeIn")].get("Class"), "shapes": shapes})
    lvl = L.loose_xml(L.name + ".level")
    mm = lvl.find("Missions/Mission/MiniMap") if lvl is not None else None
    bg = [o for _, _, o in L.find(layer="background")]
    return {"walls_and_occluders": wall_rows, "kill_zones": kill,
            "playable_extent_minimap": dict(mm.attrib) if mm is not None else None,
            "background_layer": {"objects": len(bg), "examples": sorted({(o.get("Prefab") or o.get("Geometry") or "") for o in bg})[:8]},
            "border_vegetation_hint": "vegetation groups named *border* ring the playable area (see terrain section)"}


def L_layer_name(L, rel):
    for n, a in L.layers.items():
        if a.get("file") == rel:
            return n
    return None


def s_terrain(L):
    out = {}
    ld = L.pak_xml("leveldata.xml")
    if ld is not None:
        li = ld.find("LevelInfo")
        out["level_info"] = dict(li.attrib) if li is not None else None
        out["surface_types"] = [dict(s.attrib) for s in ld.findall("SurfaceTypes/SurfaceType")]
        groups = collections.OrderedDict()
        for v in ld.findall("Vegetation/Object"):
            a = v.attrib
            groups.setdefault(a.get("Category") or a.get("GroupName") or "?", []).append(
                {k: a.get(k) for k in ("FileName", "Size", "SizeVar", "Bending", "Density", "AlignToTerrainNormals",
                                        "UseTerrainColor", "AutoMerged", "CastShadowMaps", "ViewDistRatio", "Hideable") if a.get(k) is not None})
        out["vegetation_objects"] = groups
    tt = L.loose_xml("leveldata", "terraintexture.xml")
    if tt is not None:
        out["terrain_texture"] = dict(tt.attrib)
    env = L.loose_xml("leveldata", "environment.xml")
    if env is not None:
        t = env.find("Terrain")
        out["environment_terrain"] = dict(t.attrib) if t is not None else None
    hm = os.path.join(L.dir, "leveldata", "heightmap.dat")
    out["heightmap_dat_bytes"] = os.path.getsize(hm) if os.path.isfile(hm) else None
    ar = L.asset_root()
    mats = []
    for s in out.get("surface_types", []):
        m = s.get("DetailMaterial")
        if m and ar:
            data = crypak.read_asset(ar, m + ".mtl")
            if data:
                x = ET.fromstring(data)
                mats.append({"material": m, "shader": x.get("Shader"), "surface": x.get("SurfaceType"),
                             "textures": {t.get("Map"): t.get("File") for t in x.findall("Textures/Texture")},
                             "tile": [t.find("TexMod").get("TileU") for t in x.findall("Textures/Texture") if t.find("TexMod") is not None][:1],
                             "params": x.find("PublicParams").attrib if x.find("PublicParams") is not None else None})
    out["terrain_materials"] = mats
    out["roads"] = [{"name": o.get("Name"), "material": o.get("Material"), "width": o.get("Width"),
                     "borderWidth": o.get("BorderWidth"), "eraseVegWidth": o.get("EraseVegWidth"),
                     "points": len(o.findall("Points/Point"))} for _, _, o in L.find(type_="^Road$")]
    out["water_volumes"] = [{"name": o.get("Name"), "material": o.get("Material"), "z": round(vec(o.get("Pos"))[2], 2),
                             "depth": o.get("VolumeDepth"), "fogDensity": o.get("FogDensity"), "points": len(o.findall("Points/Point"))}
                            for _, _, o in L.find(type_="^WaterVolume$")]
    out["decals"] = collections.Counter(o.get("Material") for _, _, o in L.find(type_="^Decal$"))
    return out


def s_lighting(L):
    out = {}
    tod = L.loose_xml("leveldata", "timeofday.xml")
    if tod is not None:
        out["time_of_day"] = dict(tod.attrib)
        out["tod_presets"] = [dict(p.attrib) for p in tod.findall("Presets/Preset")]
    ar = L.asset_root()
    for p in out.get("tod_presets", []):
        data = crypak.read_asset(ar, p.get("Name")) if ar else None
        if not data:
            continue
        root = ET.fromstring(data)
        t = float(out["time_of_day"].get("Time", 12)) * TOD_UNITS_PER_HOUR
        vals, animated = {}, []
        for var in root.findall("var"):
            vid = var.find("id").get("value")
            comps = []
            for i in range(3):
                s = var.find("spline%d/keys" % i)
                if s is None:
                    continue
                pts = sorted((float(k.split(":")[0]), float(k.split(":")[1])) for k in s.get("value").split(",") if k.count(":") >= 1 and k.split(":")[0])
                if len({v for _, v in pts}) > 1 and vid not in animated:
                    animated.append(vid)
                v = pts[-1][1] if pts else None
                for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                    if t0 <= t <= t1:
                        v = v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1e-9)
                        break
                if pts and t < pts[0][0]:
                    v = pts[0][1]
                if v is not None:
                    comps.append(round(v, 3))
            if var.find("type").get("value") != "TYPE_COLOR":
                comps = comps[:1]   # float vars carry unused spline1/2 copies
            vals[vid.replace("PARAM_", "")] = comps if len(comps) > 1 else (comps[0] if comps else None)
        key = ("SUN_COLOR", "SUN_COLOR_MULTIPLIER", "SUN_INTENSITY", "FOG_COLOR", "VOLFOG_GLOBAL_DENSITY", "VOLFOG_HEIGHT",
               "VOLFOG2_GLOBAL_DENSITY", "SKYLIGHT_SUN_INTENSITY_MULTIPLIER", "SKYBOX_MULTIPLIER", "HDR_EYEADAPTATION_EV_MIN",
               "HDR_EYEADAPTATION_EV_MAX", "HDR_EYEADAPTATION_EV_AUTO_COMPENSATION", "HDR_BLOOM_AMOUNT", "SUN_SHAFTS_VISIBILITY",
               "COLORGRADING_FILTERS_GRAIN", "TERRAIN_OCCL_MULTIPLIER", "GI_MULTIPLIER")
        out["preset_values_at_level_time"] = {k: vals.get(k) for k in key}
        out["preset_variable_count"] = len(vals)
        out["preset_animated_variables"] = len(animated)
    lvl = L.loose_xml(L.name + ".level")
    if lvl is not None:
        light = lvl.find("Missions/Mission/Lighting")
        out["sun_mission_settings"] = {k: light.get(k) for k in ("SunRotation", "SunHeight", "Longitude", "SunVector")} if light is not None else None
    env = L.loose_xml("leveldata", "environment.xml")
    if env is not None:
        out["environment_xml"] = {c.tag: dict(c.attrib) for c in env}
    probes = []
    for rel, la, o in L.find(type_="^EnvironmentProbe$"):
        p = o.find("Properties")
        if p is None:
            continue
        c = p.find("Color"); op = p.find("Options"); adv = p.find("OptionsAdvanced")
        c_get = c.get if c is not None else (lambda k, d=None: d)
        op_get = op.get if op is not None else (lambda k, d=None: d)
        probes.append({"name": o.get("Name"), "layer": rel, "active": p.get("bActive"),
                       "box": [p.get("BoxSizeX"), p.get("BoxSizeY"), p.get("BoxSizeZ")],
                       "cubemap_res": o.get("cubemap_resolution"), "diffuse": c_get("fDiffuseMultiplier"),
                       "specular": c_get("fSpecularMultiplier"), "priority": op_get("SortPriority"),
                       "falloff": op_get("fAttenuationFalloffMax"), "cubemap": adv.get("texture_deferred_cubemap") if adv is not None else None})
    out["probe_summary"] = {
        "count": len(probes),
        "roots_with_own_cubemap": sum(1 for p in probes if p["cubemap_res"] not in ("0", None)),
        "children_reusing_cubemap": sum(1 for p in probes if p["cubemap_res"] == "0"),
        "priority_range": [min((int(p["priority"]) for p in probes if p["priority"] is not None), default=None), max((int(p["priority"]) for p in probes if p["priority"] is not None), default=None)],
        "diffuse_values": collections.Counter(p["diffuse"] for p in probes),
    }
    out["probes"] = probes
    out["lights"] = [{"name": o.get("Name"), "layer": rel, "radius": o.find("Properties").get("Radius"),
                      "diffuse": o.find("Properties/Color").get("fDiffuseMultiplier") if o.find("Properties/Color") is not None else None,
                      "color": o.find("Properties/Color").get("clrDiffuse") if o.find("Properties/Color") is not None else None,
                      "shadows": o.find("Properties/Shadows").get("eiCastShadows") if o.find("Properties/Shadows") is not None else None}
                     for rel, la, o in L.find(cls="^Light$") if o.find("Properties") is not None]
    helpers = [o for _, _, o in L.find(layer="lighting_helpers")]
    out["lighting_helpers"] = {"count": len(helpers), "geometry": sorted({o.get("Geometry") for o in helpers}),
                               "materials": sorted({o.get("Material") for o in helpers}),
                               "hidden_in_game": sorted({o.get("HiddenInGame") for o in helpers})}
    fg_light = []
    for rel, o, fg in L.flowgraphs():
        for n in fg.iter("Node"):
            if n.get("Class", "").startswith(("Environment:", "Time:TimeOfDay")):
                inp = n.find("Inputs")
                fg_light.append({"flowgraph": o.get("Name"), "node": n.get("Class"), "inputs": dict(inp.attrib) if inp is not None else {}})
    out["flowgraph_lighting_nodes"] = fg_light
    return out


def s_cinematics(L):
    out = {"sequences": []}
    seq_elems = [s for _, _, o in L.find(type_="^SequenceObject$") for s in o.findall("Sequence")]
    md = L.pak_xml("moviedata.xml")
    if md is not None and not seq_elems:
        seq_elems = list(md.iter("Sequence"))
    for seq in seq_elems:
        nodes = seq.findall("Nodes/Node")
        info = {"name": seq.get("Name"), "flags": seq.get("Flags"),
                "length_s": round(int(seq.get("endTimeTicks", "0")) / TICKS_PER_SECOND, 1),
                "node_types": dict(collections.Counter(n.get("Type") for n in nodes)),
                "events": [e.get("Name") for e in seq.findall("Events/Event")], "director": {}, "animated": []}
        for n in nodes:
            if n.get("Type") == "Director":
                for tr in n.findall("Track"):
                    info["director"][tr.get("paramType")] = [
                        (round(int(k.get("timeTicks", "0")) / TICKS_PER_SECOND, 1),
                         k.get("node") or k.get("value") or k.get("event")) for k in tr.findall("Key")][:20]
            elif n.get("Type") in ("Entity", "Camera", "GeomCache", "Light", "Audio"):
                info["animated"].append({"type": n.get("Type"), "name": n.get("Name"),
                                         "tracks": [t.get("paramType") for t in n.findall("Track")]})
            elif n.get("Type") in ("ScreenFader", "Event", "DepthOfField"):
                keys = [(round(int(k.get("timeTicks", "0")) / TICKS_PER_SECOND, 1), k.get("fadeType") or k.get("event") or k.get("value"))
                        for t in n.findall("Track") for k in t.findall("Key")]
                info["animated"].append({"type": n.get("Type"), "name": n.get("Name"), "keys": keys[:16]})
        out["sequences"].append(info)
    out["cameras"] = [{"name": o.get("Name"), "layer": rel, "fov_rad": o.get("FOV"), "pos": r(vec(o.get("Pos")))}
                      for rel, la, o in L.find(type_="^Camera$")]
    starts = []
    for rel, o, fg in L.flowgraphs():
        nodes = {n.get("Id"): n for n in fg.iter("Node")}
        for nid, n in nodes.items():
            c = n.get("Class", "")
            if c in ("Animations:PlaySequence", "TrackEvent", "Image:ScreenFader", "Animations:StopSequence"):
                inp = n.find("Inputs")
                feeders = [(nodes[e.get("nodeOut")].get("Class"), L.name_of(nodes[e.get("nodeOut")].get("EntityGUID")) if nodes[e.get("nodeOut")].get("EntityGUID") else None, e.get("portOut"), e.get("portIn"))
                           for e in fg.iter("Edge") if e.get("nodeIn") == nid and e.get("nodeOut") in nodes]
                starts.append({"flowgraph": o.get("Name"), "node": c, "inputs": dict(inp.attrib) if inp is not None else {}, "fed_by": feeders})
    out["flowgraph_sequence_nodes"] = starts
    for rel, la, o in L.find(cls="^(AreaTrigger|ProximityTrigger)$"):
        p = o.find("Properties")
        if p is not None and p.get("PlaySequence"):
            starts.append({"trigger": o.get("Name"), "PlaySequence": p.get("PlaySequence")})
    vis = [{"name": o.get("Name"), "layer": rel, "height": o.get("Height")} for rel, la, o in L.find(type_="^VisArea$")]
    out["vis_areas"] = vis
    return out


def s_logic(L):
    rows = []
    for rel, o, fg in L.flowgraphs():
        nodes = list(fg.iter("Node"))
        cls = collections.Counter(n.get("Class") for n in nodes if not n.get("Class", "").startswith("_comment"))
        comments = [n.find("Inputs").get("Text") or n.get("Name") for n in nodes
                    if n.get("Class", "").startswith("_comment") and n.find("Inputs") is not None][:8]
        comments += [n.get("Name") for n in nodes if n.get("Class") == "_commentbox" and n.get("Name")][:8]
        ents = collections.Counter(L.name_of(n.get("EntityGUID")) for n in nodes if n.get("EntityGUID"))
        rows.append({"flowgraph": o.get("Name"), "layer": rel, "group": fg.get("Group"), "nodes": len(nodes),
                     "edges": len(list(fg.iter("Edge"))), "classes": dict(cls.most_common()),
                     "entities": dict(ents), "comments": [c for c in comments if c]})
    return {"flowgraphs": rows}


SECTIONS = collections.OrderedDict([
    ("overview", s_overview), ("streaming", s_streaming), ("audio", s_audio), ("borders", s_borders),
    ("terrain", s_terrain), ("lighting", s_lighting), ("cinematics", s_cinematics), ("logic", s_logic)])


def study(level_dir, sections=None):
    L = Level(level_dir)
    return collections.OrderedDict((s, SECTIONS[s](L)) for s in (sections or SECTIONS))


def to_text(data, indent=0, width=160):
    pad = "  " * indent
    lines = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v and len(json.dumps(v, default=list)) > width - len(pad) - len(str(k)):
                lines.append("%s%s:" % (pad, k))
                lines.append(to_text(v, indent + 1, width))
            else:
                lines.append("%s%s: %s" % (pad, k, json.dumps(v, default=list)))
    elif isinstance(data, list):
        for v in data:
            if isinstance(v, (dict, list)) and len(json.dumps(v, default=list)) > width - len(pad):
                lines.append("%s-" % pad)
                lines.append(to_text(v, indent + 1, width))
            else:
                lines.append("%s- %s" % (pad, json.dumps(v, default=list)))
    else:
        lines.append(pad + json.dumps(data, default=list))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level_dir")
    ap.add_argument("sections", nargs="*", choices=list(SECTIONS) + [[]], default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    data = study(a.level_dir, a.sections or None)
    if a.json:
        print(json.dumps(data, indent=1, default=list))
    else:
        for name, sec in data.items():
            print("\n########## %s ##########" % name.upper())
            print(to_text(sec))


if __name__ == "__main__":
    main()
