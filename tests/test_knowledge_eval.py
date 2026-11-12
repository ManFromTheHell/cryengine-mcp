#!/usr/bin/env python3
"""Does the knowledge base answer the questions a newcomer asks? (the goal: an AI can build a game without any issues with tools and knowledge)

THREE KINDS OF NUMBER, kept apart:
  1. HOLD-OUT (knowledge_holdout.json): real questions graded by expected CHUNK ID, never tuned on. THIS is the honest accuracy; it is reported with
     rank-1, top-3 and MRR, and the test fails if it falls below the floor recorded in that file.
  2. DEV (knowledge_dev.json): the questions synonyms / weights are tuned on. Reported, floor-checked, NOT an accuracy.
  3. 'dev, self-graded' (the 76 questions below): written by the authors of the chunks, graded by title substring, synonyms were tuned on them. A regression
     check only (95% top-3, playbook questions first). Never quote it as accuracy: on 25 independent questions the audit measured 44% strict.
Also checks the corpus machinery: the two corpus copies are byte-identical, the mirror folder's rag_query.py delegates to the real one and gives the same answers,
every chunk validates (corpus_lib), superseded chunks are excluded unless asked for, category names are alias tolerant, and validate() can really fail.
Editor-free.

    test_knowledge_eval.py       # exit 1 on any failed check
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import subprocess
import sys

HERE = _paths.ROOT
sys.path.insert(0, os.path.join(HERE, "rag"))
sys.path.insert(0, HERE)
import rag_query as R
import corpus_lib as L
import knowledge_eval as K

QA = [
    ("terrain renders black after creating a new level", ["Black terrain"]),
    ("how do I paint grass on the terrain", ["paint trees and grass"]),
    ("how do I place trees on slopes", ["Placing trees", "paint trees and grass"]),
    ("water river or ocean setup", ["water"]),
    ("sky and time of day lighting presets", ["environment presets", "time-of-day", "time of day"]),
    ("fog density changes after editor restart", ["Time-of-day settings live in the preset"]),
    ("how do I make a day night cycle", ["SetTime", "night as bright as day", "Day/night"]),
    ("how to bake navigation mesh for AI", ["PLAYBOOK: bake"]),
    ("NPC cannot reach destination navmesh", ["NPCs cannot path", "PLAYBOOK: bake", "navmesh"]),
    ("NPC patrol path", ["NPC", "patrol", "AI Path"]),
    ("how to make an NPC walk with animation", ["movement speed", "walk animation"]),
    ("character floats above ground collider", ["must float above the feet"]),
    ("player controller movement", ["Player component overview"]),
    ("how do I add an ambient sound loop", ["ambient layers", "Sound with no middleware"]),
    ("footstep sounds by surface", ["footsteps"]),
    ("audio has no distance falloff", ["attenuates", "Sound with no middleware"]),
    ("how do I create a material with a normal map", ["PLAYBOOK: make a new prop", "material_check"]),
    ("mesh renders flat grey material missing", ["names a material file that does not exist"]),
    ("how do I make LODs for a mesh", ["LODs the engine's way", "LOD tool"]),
    ("particles fire effect", ["Particles", "FORWARD axis"]),
    ("particle effect keys ignored", ["pfx_check", "54 features", "particle effect"]),
    ("UI menu with flash scaleform", ["Scaleform"]),
    ("how do I load a level from the main menu", ["loader while a level loads", "Ashvale: the whole game"]),
    ("pickup items inventory", ["items lying in the world"]),
    ("dialogue between NPCs", ["PLAYBOOK: NPC dialogue", "Dynamic Response System"]),
    ("quest objectives system", ["objectives", "quest"]),
    ("save and load game state", ["Save/load"]),
    ("how to package the game for release", ["Packaging Ashvale"]),
    ("shaders missing in packaged build", ["shader cache"]),
    ("editor crashes AMD driver", ["Sandbox dies", "PLAYBOOK: a tool says the editor is not open"]),
    ("how do I run a console command in the editor", ["command registries"]),
    ("python script in editor indentation error", ["Multi-line Python", "collapses whitespace"]),
    ("set a cvar in a release build", ["Release", "RELEASE"]),
    ("performance frame time profiling", ["Statoscope", "frame_profile", "performance"]),
    ("collision on house walls", ["Invisible walls", "create_collision_wall"]),
    ("door open close interaction", ["doors", "gate"]),
    ("enemy AI attack bandits behaviour tree", ["Bandits as a faction", "Behaviour trees"]),
    ("how do I know which components exist", ["PLAYBOOK: which components"]),
    ("how do I write a new C++ component", ["PLAYBOOK: write and build"]),
    ("game DLL build fails", ["Building Game.dll"]),
    ("rain weather", ["rain"]),
    ("decals blood stains", ["decal"]),
    ("cinematic intro sequence track view", ["Track View sequence"]),
    ("lamp torch light at night", ["lamp beside a wall"]),
    ("vis area interior indoor", ["vis area"]),
    ("how do I add a house building", ["timber houses"]),
    ("voice lines text to speech", ["Piper", "PLAYBOOK: NPC dialogue"]),
    ("music loops composition", ["music"]),
    ("undo a change made by the tools", ["undo step"]),
    ("I am a new AI, where do I start", ["PLAYBOOK: START HERE"]),
    ("something does not work, which tool should I run", ["PLAYBOOK: something does not work"]),
    ("how do I build a new level from scratch", ["PLAYBOOK: build a new level"]),
    ("tool says the editor is not open", ["PLAYBOOK: a tool says the editor is not open"]),
    ("how do I create a terrain heightmap", ["PLAYBOOK: build a new level", "heightmap", "terrain"]),
    ("how do I find the ground height at a position to place a building", ["PLAYBOOK: build a new level from scratch with the tools"]),
    ("make a flat area for a building on the terrain", ["PLAYBOOK: build a new level from scratch with the tools"]),
    ("editor crashed Error spawning SHLighting thread after export", ["SHLighting"]),
    ("how to add trees to my level and paint vegetation", ["vegetation", "PLAYBOOK: build a new level from scratch with the tools"]),
    ("how do I add a second level the player enters from the first with a loading screen", ["PLAYBOOK: add a second level"]),
    ("how do I build a dungeon or an underground interior with build_mesh", ["PLAYBOOK: build an underground"]),
    ("level_validate says Geometry file Failed to Load for a GameSDK prop", ["TRAP: a plan that names GameSDK props"]),
    ("write_material surface type mat_dirt undefined", ["TRAP: write_material / pipeline accepted surface types"]),
    ("redefinition error in a new component cpp file", ["TRAP: a new Components"]),
    ("files disappeared after I cleaned up imported assets by modified time, how do I get them back", ["TRAP: I deleted 1,005 files"]),
    ("the dirt road looks like it is floating above the ground, is there another way to make paths", ["PLAYBOOK: roads and paths"]),
    ("after cleaning up imported materials the level says texture tif not found for my road material", ["TRAP: prune_imports deleted the textures"]),
    ("the npc animations look bad, every villager stands and walks the same way, how do I improve them", ["PLAYBOOK: better NPC animation"]),
    ("can we use nvidia motionbricks to generate animations for the characters", ["DECISION: NVIDIA MotionBricks"]),
    ("my textures look artificial and plastic, how do I get natural looking materials", ["PLAYBOOK: make assets look natural"]),
    ("a cave floor or terrain material renders black on my mesh", ["PLAYBOOK: make assets look natural"]),
    ("loading screen is black during a level change but worked at game start", ["TRAP: a loading page that worked at game start"]),
    ("package job failed PermissionError access denied output folder", ["TRAP: the package job failed after 2 s"]),
    ("how do I test a level change in the packaged game with a script", ["PLAYBOOK: test a level change"]),
    ("package job failed textures missing from the package after importing props", ["TRAP: after importing GameSDK props the package job stops"]),
    ("how do I make a new area part of the story with a quest hint and journal entry", ["PLAYBOOK: weave a new area into the story"]),
    ("how can I check that every quest flag in the dialogue can still be reached", ["PLAYBOOK: weave a new area into the story"]),
]
FIRST = {"how to bake navigation mesh for AI", "how do I know which components exist", "how do I write a new C++ component", "dialogue between NPCs", "I am a new AI, where do I start",
         "something does not work, which tool should I run", "how do I build a new level from scratch", "tool says the editor is not open"}

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-110s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def graded_sets(chunks):
    """HOLD-OUT and DEV, graded by chunk id. The hold-out floor is the one recorded in the file (a regression guard, not a goal)."""
    data, qs = K.load_questions(os.path.join(HERE, "knowledge_holdout.json"))
    res = K.evaluate(R, chunks, qs)
    print("  " + K.summary_line("HOLD-OUT (honest, never tuned on)", res))
    floor = data["recorded"]["floor_for_test_knowledge_eval"]
    check("hold-out rank-1 %.1f%% >= floor %.0f%%" % (100 * res["rank1"], 100 * floor["rank1"]), res["rank1"] >= floor["rank1"])
    check("hold-out top-3 %.1f%% >= floor %.0f%%" % (100 * res["top3"], 100 * floor["top3"]), res["top3"] >= floor["top3"])
    check("hold-out MRR %.3f >= floor %.2f" % (res["mrr"], floor["mrr"]), res["mrr"] >= floor["mrr"])
    check("hold-out has >= 40 graded questions and every expected id exists", res["n"] >= 40 and all(
        e in {c["id"] for c in chunks} for q in qs for e in (q.get("expected") or [])))
    check("hold-out gaps are reported, not graded (%d)" % res["gaps"], res["gaps"] >= 1)
    _d, dqs = K.load_questions(os.path.join(HERE, "knowledge_dev.json"))
    dres = K.evaluate(R, chunks, dqs)
    print("  " + K.summary_line("DEV (tuned on; not an accuracy)", dres))
    check("dev top-3 %.1f%% >= 85%%" % (100 * dres["top3"]), dres["top3"] >= 0.85)
    hold_q = {q["q"].lower() for q in qs}
    check("no question is shared between hold-out and dev", not any(q["q"].lower() in hold_q for q in dqs))


def machinery(chunks):
    """The corpus is one thing in two places, and the library can really refuse."""
    have_mirror = os.path.isdir(os.path.dirname(L.MIRROR))      # CRYENGINE_CORPUS_MIRROR points at the docs-folder copy
    if have_mirror:
        check("the two corpus files are byte-identical (%s)" % L.MIRROR, L.copies_identical())
    else:
        print("  SKIPPED: no mirror folder (%s; set CRYENGINE_CORPUS_MIRROR) -- the mirror checks are not run" % os.path.dirname(L.MIRROR))
    check("every chunk validates (corpus_lib.validate: 0 errors)", L.validate().ok, L.validate().text(warnings=False)[:70])
    # the mirror folder's rag_query is a thin wrapper around the real one: same answers
    wrapper = os.path.join(os.path.dirname(L.MIRROR), "rag_query.py")
    real = os.path.join(HERE, "rag", "rag_query.py")
    if not have_mirror:
        pass
    elif os.path.exists(wrapper):
        ok = True
        for q in ("navmesh unreachable after restart", "material renders black", "bake navigation mesh"):
            a = subprocess.run([sys.executable, wrapper, q, "--top", "3"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
            b = subprocess.run([sys.executable, real, q, "--top", "3", "--corpus", L.MIRROR], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60).stdout
            ok = ok and a == b and "id=" in a
        check("the mirror's rag_query.py gives exactly the real rag_query's answers", ok)
    else:
        check("the mirror's rag_query.py exists", False, wrapper)
    # superseded chunks stay out unless asked for
    sup = [c for c in chunks if c["status"] == "superseded"]
    check("superseded chunks exist and each names a live replacement", bool(sup) and all(
        c["superseded_by"] and any(x["id"] == c["superseded_by"] for x in chunks) for c in sup), len(sup))
    hits = [c["id"] for _s, c in R.search(chunks, "NPC requested velocity exactly 0.0000 villagers do not move", top=10)]
    hits_all = [c["id"] for _s, c in R.search(chunks, "NPC requested velocity exactly 0.0000 villagers do not move", top=10, include_superseded=True)]
    check("a solved bug is not ranked as an open problem: npc-open-problem is excluded by default", "npc-open-problem" not in hits and hits[:1] == ["npc-duplicate-components"], hits[:2])
    check("... and returned with include_superseded=True", "npc-open-problem" in hits_all)
    # categories: aliases, case, plural, old names
    n_trap = len(R.search(chunks, "silent failure", top=500, category="trap"))
    check("category filter is case / plural tolerant: 'Traps' == 'trap' == 'TRAP'", n_trap > 5 and n_trap == len(R.search(chunks, "silent failure", top=500, category="Traps")) == len(R.search(chunks, "silent failure", top=500, category="TRAP")), n_trap)
    check("category filter understands an old name: 'open-problem' selects the chunks that carried it", 0 < len([1 for c in chunks if R.category_matches(c, "open-problem")]) < 10)
    check("every chunk's category is one of the %d canonical ones" % len(R.CATEGORIES), all(c["category"] in R.CATEGORIES for c in chunks))
    check("every chunk has status, verified_in, date, superseded_by", all(all(k in c for k in ("status", "verified_in", "date", "superseded_by")) for c in chunks))
    check("the 21 chunks once listed as unclassified are marked verified_in none / partial / packaged (not 'unclassified')", all(
        next((c for c in chunks if c["id"] == i), {"verified_in": "n/a"})["verified_in"] != "unclassified" for i in (
            "playbook-natural-materials-from-the-gamesdk", "playbook-natural-outdoor-lighting", "trap-editor-viewport-has-no-game-ambient-scaling",
            "playbook-roads-as-engine-road-objects", "playbook-villager-animation-from-the-library", "decision-motionbricks-used-for-locomotion",
            "playbook-npc-gaits-from-motionbricks", "decision-kimodo-used-for-actions", "playbook-npc-kimodo-variants-and-turns",
            "finding-water-in-the-open-source-projects-and-the-manual", "craft-invisible-walls", "craft-cinematics", "merged-mesh-grass-measured",
            "trap-visual-regression-same-game-differs-between-launches", "fact-csharp-in-this-engine-measured-and-the-recommendation",
            "ai-behavior-tree-component", "nav-offmesh-links-smartobjects", "anim-crysis-fbx-package", "animation-rig-compatibility",
            "trap-level-change-black-screen-flashui-unload")))
    check("the fixed black-screen trap is recorded as proven in the packaged game", next((c for c in chunks if c["id"] == "trap-level-change-black-screen-flashui-unload"), {"verified_in": "packaged"})["verified_in"] == "packaged")
    check("no chunk is longer than 3,000 characters unless generated (one symptom per chunk)", all(len(c["content"]) <= 3000 or "generated" in c["tags"] for c in chunks))
    # the library can really fail (negative controls on in-memory copies; nothing is written)
    rows = [dict(c) for c in L.read_rows()]
    good = L.validate(rows)
    bad1 = [dict(r) for r in rows]
    bad1[0]["title"] = "x" * 200
    bad2 = [dict(r) for r in rows]
    bad2[1]["status"] = "superseded"
    bad2[1]["superseded_by"] = ""
    bad3 = [dict(r) for r in rows]
    bad3[2]["parent"] = "no-such-chunk"
    bad4 = [dict(r) for r in rows] + [dict(rows[3])]
    bad5 = [dict(r) for r in rows]
    bad5[4]["category"] = "traps"
    check("validate() passes the real corpus (control)", good.ok)
    check("validate() refuses: a title over 140 chars", not L.validate(bad1).ok)
    check("validate() refuses: superseded without superseded_by", not L.validate(bad2).ok)
    check("validate() refuses: a dangling parent", not L.validate(bad3).ok)
    check("validate() refuses: a duplicate id", not L.validate(bad4).ok)
    check("validate() refuses: a category outside the list", not L.validate(bad5).ok)
    d = [dict(r) for r in rows]
    d[5]["content"] = d[5]["content"] + " see playbook-no-such-chunk-anywhere for more"
    check("lint flags text naming a chunk id that does not exist", any("playbook-no-such-chunk-anywhere" in m for _c, m in L.validate(d).warnings))
    import datetime
    e = [dict(r) for r in rows]
    e[6]["title"] = "OPEN PROBLEM: something old"
    e[6]["date"] = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    check("lint flags an open problem older than 14 days", any("days old" in m for _c, m in L.validate(e).warnings))


def main():
    chunks = R.load(os.path.join(HERE, "rag", "cryengine_project_rag.jsonl"))
    hit = 0
    misses = []
    for q, want in QA:
        top = R.search(chunks, q, top=3)
        titles = [(c["title"] + " " + c["id"]).lower() for _s, c in top]
        ok = any(w.lower() in t for w in want for t in titles)
        hit += ok
        if not ok:
            misses.append((q, [c["title"][:50] for _s, c in top]))
        if q in FIRST:
            check("first answer: %r -> a PLAYBOOK chunk" % q, bool(top) and any(w.lower() in titles[0] for w in want), top[0][1]["title"][:60] if top else "none")
    rate = hit / float(len(QA))
    for q, got in misses:
        print("    miss: %r -> %s" % (q, got))
    check("DEV, SELF-GRADED (not an accuracy): %d of %d questions have a right answer in the top 3 (%.0f%%; the bar is 95%%)" % (hit, len(QA), 100 * rate), rate >= 0.95, "")
    machinery(chunks)
    graded_sets(chunks)
    check("the corpus holds the playbook chunks (10) and over 340 chunks in all", sum(1 for c in chunks if c["category"] == "playbook") >= 10 and len(chunks) >= 340, (len(chunks)))
    ids = [c["id"] for c in chunks]
    check("chunk ids are unique", len(ids) == len(set(ids)), len(ids) - len(set(ids)))
    syn_ok = R.expand(R.tokenize("bake the navmesh"))
    check("query synonyms are on: 'bake' also searches for 'regenerate'", "regenerate" in syn_ok and "navmesh" in syn_ok, syn_ok[:5])
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
