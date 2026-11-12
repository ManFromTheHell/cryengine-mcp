#!/usr/bin/env python3
"""Self-test of mannequin_check.py: the comparison logic on synthetic documents (offline), then, with the editor open, the villager's real files through the engine's own Mannequin loaders and
deliberately broken copies (positive controls) next to the good fragments as controls. The copies get unique names (CryPak caches a loose file by name) and are deleted again; the villager's
own files are only read. About 20 s live.

    test_mannequin_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import time
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import mannequin_check as M

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(r, where_part=""):
    return sorted({f["code"] for f in r["findings"] if where_part in f["where"] or where_part in f["message"]})


def main():
    # ------------------------------------------------------------------ offline: the comparison
    check("tag lists compare as sets (the engine sorts them) and numbers as numbers (0.3 = 0.30000001)", M.same_value("Tags", "b+a", "a+b") and M.same_value("Duration", "0.3", "0.30000001") and not M.same_value("Duration", "0.3", "0.5"), "")
    src = ET.fromstring('<AnimDB><FragmentList><Idle><Fragment Tags="b+a"><AnimLayer><Animation name="x" flags="Loop"/></AnimLayer></Fragment><Fragment Tags=""/></Idle><Bogus><Fragment Tags=""/></Bogus></FragmentList></AnimDB>')
    eng = ET.fromstring('<AnimDB><FragmentList><Idle><Fragment Tags=""/><Fragment Tags="a+b"><AnimLayer><Animation name="x" flags="Loop" speed="1"/></AnimLayer></Fragment></Idle></FragmentList></AnimDB>')
    d = M.diff_documents(src, eng)
    check("options are matched by tag set, not by position (the engine re-sorts): nothing differs but the fragment the engine did not keep", [M._pretty(p) for p in d["dropped"]] == ["AnimDB/FragmentList/bogus"] and not d["changed"] and not d["dropped_attrs"], d["dropped"])
    d2 = M.diff_documents(ET.fromstring('<A><FragmentList><Idle><Fragment Tags=""><AnimLayer><Animation name="x" flags="Loop"/></AnimLayer></Fragment></Idle></FragmentList></A>'),
                          ET.fromstring('<A><FragmentList><Idle><Fragment Tags=""><AnimLayer><Animation name="y" flags=""/></AnimLayer></Fragment></Idle></FragmentList></A>'))
    check("a changed clip name and a lost flag are reported", [c[1] for c in d2["changed"]] == ["name", "flags"], d2["changed"])
    d3 = M.diff_documents(ET.fromstring('<TagDefinition version="2"><Tags/></TagDefinition>'), ET.fromstring('<TagDefinition version="2"/>'))
    check("an empty element the engine does not write back is not a loss", not d3["dropped"], d3)
    lf = M.log_findings(["[CAnimationDatabaseManager::LoadDatabase] Warning missing fragmentID Bogus", "$4[Error] [TagListToFlags] Invalid tag 'X'", "Skipping unknown fragment 'Ghost' in a.xml", "something else"])
    check("the engine's log lines about dropped entries become findings, others are ignored", [f["code"] for f in lf] == ["M002", "M004", "M003"] and "Bogus" in lf[0]["message"], lf)
    over = M._layer_overlaps([{"name": "A", "context": "c", "layer": 0, "num_layers": 3}, {"name": "B", "context": "c", "layer": 2, "num_layers": 1}, {"name": "C", "context": "d", "layer": 0, "num_layers": 3}, {"name": "D", "context": "c", "layer": 3, "num_layers": 2}])
    check("scopes of one context that share a layer are found (A 0-2 and B 2), other contexts and adjacent ranges are not", [(a["name"], b["name"]) for a, b in over] == [("A", "B")], over)
    import shutil
    import tempfile
    tmp_code = tempfile.mkdtemp()
    try:
        open(os.path.join(tmp_code, "a.cpp"), "w").write('void f(){ GetFragmentId("Idle"); SetDefaultFragmentName("Walk"); QueueFragment("Run"); }\nconst char* ActionFragment(int a)\n{\n\treturn "Vault";\n}\n')
        syn = M.code_fragment_names(tmp_code)
    finally:
        shutil.rmtree(tmp_code, ignore_errors=True)
    check("the code scanner finds fragment names in the call forms and in a ...Fragment() function's returns, with a file:line", {"Idle", "Walk", "Run", "Vault"} <= set(syn) and ":" in syn["Idle"][0], sorted(syn))
    names = M.code_fragment_names()
    if names:
        check("the game's C++ names its fragments: Idle, Walk, Run, Startle and the eight villager actions are found with a file:line (Ashvale)", {"Idle", "Walk", "Run", "Startle", "Vault", "OpenGate", "CrouchIn", "CrouchLoop", "CrouchOut", "Attack", "Death", "Aim"} <= set(names) and ":" in names["Idle"][0], sorted(names))
    else:
        print("  --   the project's C++ names no Mannequin fragments: the Ashvale fragment-name check is skipped")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        r = M.check()
        import villager_anim as VA        # the villagers' database is generated from that table (2026-10-15): the expected counts come from it, not from a number typed here
        want_fragments = len(VA.fragments()) + 1                                              # + LookAt
        want_clips = len({layer[0].lower() for f in VA.fragments() for layer in f[4]})
        check("the villager's files load through the engine and everything is kept: %d fragments (the table's), 0 errors, %d animations all found in the character's set of 1,000+" % (want_fragments, want_clips),
              r["ok"] and len(r["fragments"]) == want_fragments and r["animations"]["used"] == want_clips and not r["animations"]["missing"] and r["animations"]["animation_set_size"] > 1000, (r["errors"], r["warnings"], r["animations"]))
        check("the code against the database: no fragment name the code asks for is missing, and every defined fragment is asked for", not [f for f in r["findings"] if f["code"] in ("M040", "M041")], [f["message"] for f in r["findings"]][:2])
        check("the controller's scopes are read: FullBody on layers 0-2 and LookPose on layer 15 (the look-IK layer), both in context MainCharacter; Idle and LookAt are Persistent + AutoReinstall (flags 3)",
              r["controller"]["scope_defs"] == [{"name": "FullBody", "layer": 0, "num_layers": 3, "context": "MainCharacter"}, {"name": "LookPose", "layer": 15, "num_layers": 1, "context": "MainCharacter"}]
              and r["controller"]["fragments"][0]["flags"] == 3 and next(f for f in r["controller"]["fragments"] if f["name"] == "LookAt")["flags"] == 3, r["controller"]["scope_defs"])

        stamp = "%d_%d" % (os.getpid(), int(time.time()))
        adir = os.path.join(M.ASSETS, "Animations", "Mannequin", "ADB")
        made = []
        try:
            good = open(os.path.join(adir, "Villager.adb"), encoding="utf-8").read()
            bad = good.replace(" </FragmentList>", """  <Bogus>
   <Fragment BlendOutDuration="0.2" Tags="">
    <AnimLayer><Blend ExitTime="0" StartTime="0" Duration="0.3"/><Animation name="villager_move" flags="Loop"/></AnimLayer>
   </Fragment>
  </Bogus>
  <Walk>
   <Fragment BlendOutDuration="0.2" Tags="NoSuchTag">
    <AnimLayer><Blend ExitTime="0" StartTime="0" Duration="0.3"/><Animation name="villager_move" flags="Loop"/></AnimLayer>
   </Fragment>
   <Fragment BlendOutDuration="0.2" Tags="">
    <AnimLayer><Blend ExitTime="0" StartTime="0" Duration="0.3"/><Animation name="no_such_animation_xyz" flags="Loop"/></AnimLayer>
   </Fragment>
  </Walk>
 </FragmentList>
 <FragmentBlendList>
  <Blend from="Bogus" to="Idle"><Variant from="" to=""><Fragment selectTime="0" enterTime="0"/></Variant></Blend>
 </FragmentBlendList>""")
            assert bad != good
            bad_rel = "Animations/Mannequin/ADB/zz_mcp_bad_%s.adb" % stamp
            with open(os.path.join(M.ASSETS, bad_rel.replace("/", os.sep)), "w", encoding="utf-8") as f:
                f.write(bad)
            made.append(os.path.join(M.ASSETS, bad_rel.replace("/", os.sep)))
            rb = M.check(bad_rel, M.DEFAULT_CONTROLLER, M.DEFAULT_CHARACTER)
            msgs = " | ".join(f["message"] for f in rb["findings"])
            check("positive control: a fragment the FragmentID file does not define is reported (from the engine log) and as dropped", "Bogus" in msgs and any(f["code"] == "M002" and "Bogus" in f["message"] for f in rb["findings"]) and any(f["code"] == "M010" and "bogus" in f["message"].lower() for f in rb["findings"]), codes(rb))
            check("positive control: an option with an unknown tag is reported and dropped", any(f["code"] == "M002" and "NoSuchTag" in f["message"] for f in rb["findings"]) and any(f["code"] == "M010" and "nosuchtag" in f["message"].lower() for f in rb["findings"]), codes(rb))
            check("positive control: the engine's own validation names the missing animation and the fragment it is in (Walk, option 1)", any(f["code"] == "M020" and "no_such_animation_xyz" in f["message"] and "Walk" in f["where"] for f in rb["findings"]) and "no_such_animation_xyz" in rb["animations"]["missing"], rb["animations"])
            check("positive control: a transition between an unknown fragment and Idle is dropped without a log line, and the comparison finds it", any(f["code"] == "M010" and "FragmentBlendList" in f["message"] for f in rb["findings"]), [f["message"] for f in rb["findings"] if f["code"] == "M010"])
            check("control: Idle and Run, written correctly, have no finding at all; the validate flag lies (true) while a callback error exists", not any(("Idle" in f["where"] or "Run" in f["where"]) and f["code"] in ("M020", "M021") for f in rb["findings"]) and rb["engine"]["validate_ok_flag"] is True and rb["errors"] >= 1, rb["engine"]["validate_ok_flag"])

            ctl = open(os.path.join(adir, "VillagerControllerDefinition.xml"), encoding="utf-8").read()
            badctl = ctl.replace("  <Aim scopes=\"FullBody\"/>\n", "  <Ghost scopes=\"FullBody\"/>\n  <Idle scopes=\"FullBody\"/>\n").replace(
                "  <FullBody layer=\"0\" numLayers=\"3\" context=\"MainCharacter\"/>", "  <FullBody layer=\"0\" numLayers=\"3\" context=\"MainCharacter\"/>\n  <Torso layer=\"2\" numLayers=\"2\" context=\"MainCharacter\"/>")
            assert badctl != ctl
            ctl_rel = "Animations/Mannequin/ADB/zz_mcp_ctl_%s.xml" % stamp
            with open(os.path.join(M.ASSETS, ctl_rel.replace("/", os.sep)), "w", encoding="utf-8") as f:
                f.write(badctl)
            made.append(os.path.join(M.ASSETS, ctl_rel.replace("/", os.sep)))
            rc = M.check(None, ctl_rel, None)
            check("positive control: an entry for an unknown fragment and a duplicate entry are reported from the engine's log", any(f["code"] == "M003" and "Ghost" in f["message"] for f in rc["findings"]) and any(f["code"] == "M003" and "Idle" in f["message"] for f in rc["findings"]), codes(rc))
            check("positive control: two scopes of one context sharing layer 2 are reported (the engine does not check)", any(f["code"] == "M030" for f in rc["findings"]), codes(rc))
            check("positive control: a fragment the controller definition has no entry for (Aim) is reported (the manual: exactly one per FragmentID)", any(f["code"] == "M031" and "'Aim'" in f["message"] for f in rc["findings"]), codes(rc))
            check("control: the villager's own controller definition, checked the same way, has none of these", not any(f["code"] in ("M003", "M030", "M031") for f in r["findings"]), codes(r))
        finally:
            for p in made:
                if os.path.exists(p):
                    os.remove(p)
        clips = M.clips(refresh=True)
        by = {c["name"]: c for c in clips}
        check("the engine's procedural clip registry has 27 types, Audio takes StartTrigger/StopTrigger, one clip's parameter dump is an unreadable stub (reported as a note)",
              len(clips) == 27 and "StartTrigger" in by["Audio"]["params"] and "StopTrigger" in by["Audio"]["params"] and by["PositionAdjustAnimPosContinuously"]["params"] is None and by["PositionAdjustAnimPosContinuously"]["note"], len(clips))
        check("clip_search finds a clip by a parameter name", [c["name"] for c in M.clip_search("EffectName")] == ["ParticleEffect"], [c["name"] for c in M.clip_search("EffectName")])
        left = [f for f in os.listdir(os.path.join(M.ASSETS, "Animations", "Mannequin", "ADB")) if f.startswith("zz_mcp_")]
        check("the project is as it was: no test file is left in the ADB folder", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
