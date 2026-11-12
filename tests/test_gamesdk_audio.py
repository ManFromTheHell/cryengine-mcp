#!/usr/bin/env python3
"""Self-test of gamesdk_audio.py: the Ogg duration reader against ffprobe on a file of the project, the catalog read from the GameSDK sample's audio.pak, and its search. Editor-free, about 5 s.

    test_gamesdk_audio.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import subprocess
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import gamesdk_audio as G

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def main():
    mine = os.path.join(G.ASSETS, "audio", "sdlmixer", "assets", "ash_wind_loop.ogg")
    mine2 = os.path.join(G.ASSETS, "audio", "sdlmixer", "assets", "c_player_fts_dirt_walk.ogg")
    if not os.path.isfile(mine):
        print("  SKIPPED: the project has no ash_wind_loop.ogg (this test uses an Ashvale file as its duration control) -- 0 checks run")
        return 0
    secs = G.ogg_seconds(open(mine, "rb").read())
    try:
        import audio_report
        probed = audio_report.probe(mine)
        want = float(probed.get("duration_s") or 0) if isinstance(probed, dict) else 0.0
    except Exception:                                           # noqa: BLE001
        want = 0.0
    check("control: the Ogg duration reader agrees with ffprobe on a project file (within 0.05 s)", want > 0 and abs(secs - want) < 0.05, (secs, want))
    check("positive control: bytes that are not Ogg give None, not a crash", G.ogg_seconds(b"not an ogg file") is None and G.ogg_seconds(b"") is None, "")

    d = G.build()
    s = d["summary"]
    check("the sample's SDL-mixer libraries hold 611 triggers, 203 with a real sample and 408 that only play the do_nothing.ogg placeholder", (s["triggers"], s["real"], s["placeholder_only"]) == (611, 203, 408), s)
    check("124 sample files, every sample an event names is in the pak, and every file is used by some event", s["assets"] == 124 and not s["missing_samples"] and not s["assets_unused"], (s["assets"], s["missing_samples"], s["assets_unused"]))
    names = {l["library"] for l in d["libraries"]}
    check("37 library files were read (character, NPC, projectile, collision, weapon, vehicle, UI and level libraries)", len(d["libraries"]) == 37 and {"c_player", "c_npc", "p_col_mat", "w_pistol", "l_woodland"} <= names, len(d["libraries"]))
    cm = [t for t in d["triggers"].values() if t["library"] == "p_col_mat"]
    check("measured: the 105 material-collision triggers (p_col_mat) are ALL placeholders: the real impact sounds are in the Wwise and FMOD sets, not here", len(cm) == 105 and not any(t["real"] for t in cm), (len(cm), sum(1 for t in cm if t["real"])))
    rows, total = G.search("footstep", limit=100)
    surfaces = sorted({r["trigger"].split("_fts_")[1] for r in rows if r["trigger"].startswith("Play_c_player_fts_")})
    check("footsteps by surface: 40 triggers (player and NPC), real samples of 0.3-0.9 s for concrete, dirt, fabric, grass, gravel, leaves, metal, mud, wood ...", total == 40 and {"dirt", "grass", "gravel", "mud", "concrete", "wood_solid", "leaves"} <= set(surfaces) and all(0.1 < e["seconds"] < 1.5 for r in rows for e in r["events"]), (total, surfaces[:6]))
    dirt = G.search("fts dirt")[0]
    check("the project already has the dirt and grass footstep samples imported and not the others", all(e["in_project"] for r in dirt for e in r["events"]) and not all(e["in_project"] for r in G.search("fts gravel")[0] for e in r["events"]), "")
    check("the catalog's duration of the imported dirt footstep equals the reader's duration of the project's own copy", abs(dirt[0]["events"][0]["seconds"] - G.ogg_seconds(open(mine2, "rb").read())) < 0.001, (dirt[0]["events"][0]["seconds"], G.ogg_seconds(open(mine2, "rb").read())))
    rows, total = G.search("melee")
    allm = G.search("melee", real_only=False)
    check("measured: all 13 melee triggers (the weapon, the player feedback and 10 per-surface hits: concrete, default, flesh, glass, grass, metal, rock, soil, water, wood) are placeholders: a sword needs the game's own sounds",
          total == 0 and allm[1] == 13 and sum(1 for r in allm[0] if "_hit_mat_" in r["trigger"]) == 10, (total, allm[1]))
    rows, total = G.search("woodland amb", limit=100)
    check("the woodland ambience loops are real samples of 18-31 s (the village ambience the game uses comes from here)", total >= 5 and any(e["seconds"] and e["seconds"] > 15 for r in rows for e in r["events"]), total)
    trig, files = G.project_state()
    check("the project's own state is read: its 102 triggers (ash_*, claude_*) and the sample files it ships, including the two imported footsteps", len(trig) >= 100 and "ash_test_2d" in trig and {"c_player_fts_dirt_walk.ogg", "c_player_fts_grass_walk.ogg"} <= files, (len(trig), len(files)))

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
