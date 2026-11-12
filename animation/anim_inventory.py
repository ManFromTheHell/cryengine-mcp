#!/usr/bin/env python3
"""Every animation the GameSDK character can play, sorted by what it is FOR and whether a villager can use it, offline (no editor, no game).

    python animation/anim_inventory.py report                    # counts per category: all clips / weaponless third-person clips
    python animation/anim_inventory.py find <word> [word ...] [--civ] [--max 6.0] [--min 1.0] [--cat talk]    # clips whose name has all the words
    python animation/anim_inventory.py json                      # write engine_data/anim_inventory.json

Source: the compiled clip headers in the sample's animations.pak (Animations/Animations.img, read by anim_img.py): name, duration, root travel distance. Nothing is copied or downloaded.
A name is read as <stance>_tac_<what>[_<weapon>]_<view>_<variant>, e.g. relaxed_tac_idle_nw_3p_01. CIVILIAN (--civ) means a villager could play it as it is: third person (3p or no view tag; 1p clips are for the
player's own arms), not additive (an 'add' clip only adds to another pose), and no weapon token in the name (rifle, pistol, scar, hammer, bayonet, gren, c4, shotgun ... mean a weapon is held or aimed);
'nw' (= no weapon) counts as weaponless. Clips of the soldier set that are weaponless by their name are NOT proof they look civilian: that needs a picture of the motion.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import collections
import json
import os
import re
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_img  # noqa: E402
import pakread  # noqa: E402

# Folder of the GameSDK sample project (holds animations.pak); override with CRYENGINE_GAMESDK_DIR (same variable as asset_lint.py).
GAMESDK = os.environ.get("CRYENGINE_GAMESDK_DIR", os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk"))
PAK = os.path.join(GAMESDK, "animations.pak")
OUT = os.path.join(HERE, "engine_data", "anim_inventory.json")
WEAPONS = ("rifle", "pistol", "scar", "hammer", "bayonet", "gren", "grenade", "c4", "shotgun", "smg", "mg", "fgl", "gauss", "dsg1", "law", "mike", "feline", "pickable", "radio", "binoc", "knife", "machete", "sniper")
STANCES = ("relaxed", "stand", "alerted", "crouch", "kneel", "prone", "slide", "cover", "coverhigh", "coverlow", "lean", "sit", "swim", "ladder")
# category -> words in the name (first match wins, top to bottom)
CATEGORIES = [
    ("talk", ("talk", "conversation", "chat")), ("sit", ("sit", "seat")), ("sleep", ("sleep",)), ("drink", ("drink",)), ("shrug", ("shrug",)), ("clap", ("clap",)), ("wave", ("wave",)),
    ("smoke", ("smoke", "cigar")), ("look", ("look", "search", "scan", "gaze")), ("repair/work", ("repair", "fix", "work", "pickup", "pick_up", "dig", "use")), ("door/interact", ("opendoor", "door", "button", "lever", "switch", "pull", "push")),
    ("death", ("death", "dead", "dying", "kill")), ("hit/flinch", ("hit", "flinch", "impact", "stagger")), ("melee", ("melee", "punch", "kick", "bash", "swing", "slash")),
    ("fear/surrender", ("scared", "surrender", "cower", "panic", "afraid")), ("vault/climb", ("vault", "climb", "mantle", "ladder", "jump", "fall", "land")), ("swim", ("swim",)), ("vehicle", ("vehicle", "ltv", "driver", "passenger")),
    ("turn/step", ("turn", "step", "pivot", "start", "stop", "idle2move", "move2idle", "i2m", "m2i")), ("run", ("run", "sprint")), ("walk", ("walk", "move", "locomotion", "strafe")),
    ("aim/shoot", ("aim", "shoot", "fire", "reload", "recoil", "weaponpose", "weaponadjust", "radiocombat")), ("idle", ("idle", "breath", "pose")),
]


def tokens(name):
    return re.split(r"[_\W]+", name.lower())


def parse(name):
    t = tokens(name)
    low = name.lower()
    weapon = next((w for w in WEAPONS if w in t), "")
    if not weapon:
        weapon = next((w for w in WEAPONS if len(w) > 3 and w in low), "")
    view = "1p" if "1p" in t else ("3p" if "3p" in t else "")
    stance = next((s for s in STANCES if s in t), t[0] if t else "")
    additive = "add" in t or "additive" in low or low.startswith("additive")
    cat = next((c for c, words in CATEGORIES if any(w in low for w in words)), "other")
    return {"stance": stance, "weapon": weapon, "nw": "nw" in t, "view": view, "additive": additive, "category": cat}


def civilian(c):
    return c["view"] != "1p" and not c["additive"] and not c["weapon"] and not name_is_bspace(c["name"])


def name_is_bspace(name):
    return "bspace" in name.lower() or name.lower().startswith(("1d-", "2d-", "3d-"))


def load():
    if not os.path.isfile(PAK):
        raise FileNotFoundError("GameSDK animations.pak not found at %s; set CRYENGINE_GAMESDK_DIR to the sample project's gamesdk folder" % PAK)
    data = pakread.read(PAK, "Animations/Animations.img")
    out = {}
    for h in anim_img.headers(data):
        base = h["path"].replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r"\.(caf|i_caf|fbx)$", "", base, flags=re.I)
        c = parse(name)
        c.update({"name": name, "dur": round(h["dur"], 3), "dist": round(h["dist"], 3), "speed": round(h["speed"], 3), "folder": "/".join(h["path"].replace("\\", "/").split("/")[:-1])})
        c["civilian"] = civilian(c)
        out[name.lower()] = c
    return sorted(out.values(), key=lambda c: c["name"])


def report(clips):
    cat = collections.defaultdict(lambda: [0, 0])
    for c in clips:
        cat[c["category"]][0] += 1
        cat[c["category"]][1] += 1 if c["civilian"] else 0
    print("%d clips; %d are weaponless third-person and not additive ('civilian' by name)" % (len(clips), sum(1 for c in clips if c["civilian"])))
    print("%-16s %6s %9s" % ("category", "all", "civilian"))
    for k, (a, b) in sorted(cat.items(), key=lambda kv: -kv[1][0]):
        print("%-16s %6d %9d" % (k, a, b))


def find(clips, words, civ=False, mx=None, mn=None, category=None):
    out = []
    for c in clips:
        low = c["name"].lower()
        if all(w.lower() in low for w in words) and (not civ or c["civilian"]) and (mx is None or c["dur"] <= mx) and (mn is None or c["dur"] >= mn) and (category is None or c["category"] == category):
            out.append(c)
    return out


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    clips = load()
    if a[0] == "report":
        report(clips)
    elif a[0] == "json":
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(clips, open(OUT, "w"), indent=0)
        print("wrote %d clips to %s" % (len(clips), OUT))
    elif a[0] == "find":
        opt = lambda k: a[a.index(k) + 1] if k in a else None
        words = [w for i, w in enumerate(a[1:], 1) if not w.startswith("--") and a[i - 1] not in ("--max", "--min", "--cat")]
        res = find(clips, words, "--civ" in a, float(opt("--max")) if opt("--max") else None, float(opt("--min")) if opt("--min") else None, opt("--cat"))
        for c in res:
            print("%-58s %5.2fs dist %5.2f  %-14s %s%s" % (c["name"], c["dur"], c["dist"], c["category"], c["stance"], "  [weapon: %s]" % c["weapon"] if c["weapon"] else ""))
        print("%d clip(s)" % len(res))


if __name__ == "__main__":
    main()
