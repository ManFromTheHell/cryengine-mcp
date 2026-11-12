#!/usr/bin/env python3
"""The GameSDK sample project's SDL-mixer audio as a catalog (ENGINE_LEARNING_PLAN H1), read-only from its audio.pak; nothing is extracted or copied.

The sample ships three sets of Audio Controls Editor libraries (audio/sdlmixer, audio/wwise, audio/fmod); this project plays through SDL mixer, so audio/sdlmixer/ace/*.xml are the libraries that work here:
  c_player, c_npc            footsteps and foley by surface (c_player_fts_<surface>_walk ...)
  p_col_mat / p_col_obj / p_col_veg / p_pro / p_brk / p_pfx     impacts, collisions, projectiles, breakage, particle sounds
  w_*                        weapons (w_melee, w_pistol ...)        v_*   vehicles        ui_*   UI          l_*   levels (ambience)
Many events point at `do_nothing.ogg`, a silent placeholder: such a trigger exists but plays nothing (the WWISE/FMOD sets carry the real content). Only events whose sample is a real file count here.

    python checks/gamesdk_audio.py [text ...]       search triggers by name, library or sample (default: summary)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
DATA = os.path.join(HERE, "engine_data")
CATALOG = os.path.join(DATA, "gamesdk_audio.json")
PAK = os.environ.get("GAMESDK_AUDIO_PAK", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk\audio.pak"))
ACE = "audio/sdlmixer/ace/"
ASSET_DIR = "audio/sdlmixer/assets/"
PLACEHOLDER = "do_nothing.ogg"


class AudioCatalogError(RuntimeError):
    pass


def _read(z, name):
    """A pak entry's bytes. The sample's paks store backslash names; zipfile checks them against the central directory, so the name is set to the stored spelling first."""
    zi = z.getinfo(name)
    zi.orig_filename = zi.orig_filename.replace("/", chr(92))
    return z.read(zi)


def ogg_seconds(data):
    """Duration of an Ogg Vorbis file from its first page (sample rate) and last page (granule position); None when it does not parse."""
    try:
        i = data.index(b"vorbis")
        rate = struct.unpack_from("<I", data, i + 6 + 4 + 1)[0]          # 'vorbis' (6) + version (4) + channels (1) -> sample rate
        j = data.rindex(b"OggS")
        granule = struct.unpack_from("<q", data, j + 6)[0]
        return round(granule / float(rate), 3) if rate and granule > 0 else None
    except (ValueError, struct.error):
        return None


def build(pak=PAK):
    """Read the pak and write engine_data/gamesdk_audio.json -> the catalog dict."""
    if not os.path.isfile(pak):
        raise AudioCatalogError("the GameSDK sample's audio.pak is not at %s" % pak)
    z = zipfile.ZipFile(pak)
    names = {n.replace(chr(92), "/"): n for n in z.namelist()}
    assets = {}
    for low, n in names.items():
        if low.lower().startswith(ASSET_DIR) and low.lower().endswith(".ogg"):
            data = _read(z, n)
            assets[os.path.basename(low).lower()] = {"file": os.path.basename(low), "bytes": len(data), "seconds": ogg_seconds(data)}
    triggers, libs = {}, []
    for low, n in sorted(names.items()):
        if not (low.lower().startswith(ACE) and low.lower().endswith(".xml")):
            continue
        lib = os.path.splitext(os.path.basename(low))[0]
        try:
            root = ET.fromstring(_read(z, n).decode("utf-8", "replace"))
        except ET.ParseError as exc:
            libs.append({"library": lib, "error": str(exc)})
            continue
        libs.append({"library": lib, "file": low[len(ACE):], "triggers": len(root.findall("./Triggers/Trigger"))})
        for t in root.findall("./Triggers/Trigger"):
            events = []
            for e in t.findall("Event"):
                sample = (e.get("name") or "")
                a = assets.get(sample.lower())
                events.append({"sample": sample, "type": e.get("type"), "volume": e.get("volume"), "loop_count": e.get("loop_count"), "panning": e.get("enable_panning"),
                               "attenuation": [e.get("attenuation_dist_min"), e.get("attenuation_dist_max")] if e.get("enable_dist_attenuation") == "true" else None,
                               "placeholder": sample.lower() == PLACEHOLDER, "in_pak": a is not None or sample.lower() == PLACEHOLDER, "seconds": a["seconds"] if a else None, "bytes": a["bytes"] if a else None})
            triggers[t.get("name")] = {"library": lib, "path": t.get("path") or "", "events": events,
                                       "real": any(not ev["placeholder"] and ev["in_pak"] for ev in events if ev["type"] == "start")}
    used = {ev["sample"].lower() for t in triggers.values() for ev in t["events"]}
    data = {"pak": os.path.basename(pak),"libraries": libs, "triggers": triggers, "assets": assets,
            "summary": {"triggers": len(triggers), "real": sum(1 for t in triggers.values() if t["real"]), "placeholder_only": sum(1 for t in triggers.values() if not t["real"]),
                        "assets": len(assets), "assets_unused": sorted(a for a in assets if a not in used)[:20], "missing_samples": sorted({ev["sample"] for t in triggers.values() for ev in t["events"] if not ev["in_pak"]})[:20]}}
    os.makedirs(DATA, exist_ok=True)
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def load(refresh=False):
    if refresh or not os.path.isfile(CATALOG):
        return build()
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f)


def project_state():
    """(trigger names the project's own libraries define, sample file names under the project's audio/sdlmixer/assets), lower case."""
    trig, files = set(), set()
    try:
        import ace_catalog
        trig = {t.lower() for t in ace_catalog.load()["triggers"]}
    except Exception:                                             # noqa: BLE001
        pass
    base = os.path.join(ASSETS, "audio", "sdlmixer", "assets")
    if os.path.isdir(base):
        files = {f.lower() for f in os.listdir(base)}
    return trig, files


def search(text="", real_only=True, limit=40, refresh=False):
    """Triggers whose name, library, folder or sample contains every word -> (rows, total). Each row: name, library, path, samples [{sample, seconds, bytes, in_project}], already_defined."""
    d = load(refresh)
    trig, files = project_state()
    words = [{"footstep": "fts", "footsteps": "fts"}.get(w, w) for w in re.split(r"\s+", text.lower().strip()) if w]          # the sample's own word for footsteps
    rows = []
    for name, t in sorted(d["triggers"].items()):
        if real_only and not t["real"]:
            continue
        hay = (name + " " + t["library"] + " " + t["path"] + " " + " ".join(e["sample"] for e in t["events"])).lower()
        if all(w in hay for w in words):
            rows.append({"trigger": name, "library": t["library"], "path": t["path"], "already_defined": name.lower() in trig,
                         "events": [{"sample": e["sample"], "type": e["type"], "seconds": e["seconds"], "bytes": e["bytes"], "volume": e["volume"], "loop": e["loop_count"], "in_project": e["sample"].lower() in files} for e in t["events"] if not e["placeholder"]]})
    return rows[:limit], len(rows)


def main():
    d = load()
    s = d["summary"]
    if len(sys.argv) <= 1:
        print("GameSDK SDL-mixer audio: %(triggers)d triggers, %(real)d with a real sample, %(placeholder_only)d placeholder-only (do_nothing.ogg); %(assets)d sample files" % s)
        for lib in d["libraries"]:
            real = sum(1 for t in d["triggers"].values() if t["library"] == lib["library"] and t["real"])
            print("  %-22s %3s triggers, %3d real" % (lib["library"], lib.get("triggers", "?"), real))
        return
    rows, total = search(" ".join(sys.argv[1:]))
    print("%d trigger(s)" % total)
    for r in rows:
        print("  %-52s %-14s %s" % (r["trigger"], r["library"], ", ".join("%s %ss%s" % (e["sample"], e["seconds"], "" if e["in_project"] else " (not imported)") for e in r["events"])[:110]))


if __name__ == "__main__":
    main()
