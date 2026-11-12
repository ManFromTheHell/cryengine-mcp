#!/usr/bin/env python3
"""Self-test of anim_check.py: the chrparams and blend-space checks on synthetic text with positive controls (offline), then, with the editor open, the names the engine resolved for the villager
character and the project's own chrparams and blend space against them. About 5 s live.

    test_anim_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_check as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


JOINTS = ["Bip01", "Bip01 Pelvis", "Bip01 R Hand", "Bip01 R UpperArm", "weapon_bone", "Bip01 Spine"]
CHR = """<Params><IK_Definition><LimbIK_Definition><IK EndEffector="Bip01 R Hand" Handle="RgtArm01" Root="Bip01 R UpperArm" Solver="2BIK"/></LimbIK_Definition>
<Animation_Driven_IK_Targets><ADIKTarget Handle="RgtArm01" Target="weapon_bone" Weight="Bip01 Spine"/></Animation_Driven_IK_Targets>
<Recoil_Definition><ImpactJoints><ImpactJoint Arm="3" Delay="0.3" Weight="0.2" JointName="Bip01 Pelvis"/></ImpactJoints></Recoil_Definition></IK_Definition>
<Lod><JointList level="1"><Joint name="Bip01 Spine"/></JointList></Lod>
<AnimationList><Animation name="$AnimEventDatabase" path="Animations/x.animevents"/><Animation name="walk" path="whatever"/></AnimationList></Params>"""
BSP = """<ParaGroup><Dimensions><Param name="MoveSpeed" min="0" max="5" cells="15" scale="1.0"/><Param name="TurnSpeed" min="-1" max="1" cells="21" scale="1.0"/></Dimensions>
<AdditionalExtraction><Param name="TravelAngle"/></AdditionalExtraction>
<ExampleList><Example AName="walk_a"/><Example AName="walk_b"/><Example AName="run_a"/></ExampleList>
<ExamplePseudo><Pseudo p0="1" p1="0" w0="-2.0" w1="3.0"/></ExamplePseudo>
<Blendable><Face p0="0" p1="1" p2="3"/><Face p0="1" p1="2" p2="3"/></Blendable></ParaGroup>"""
ANIMS = ["walk_a", "walk_b", "run_a", "idle"]


def main():
    r = A.check_chrparams("x.chrparams", CHR, JOINTS, lambda p: True)
    check("control: a chrparams whose joints all exist has no finding (ImpactJoint's Weight=0.2 is a number, not a joint; ADIKTarget's Weight is a joint)", r == [], codes(r))
    r = A.check_chrparams("x.chrparams", CHR.replace('Root="Bip01 R UpperArm"', 'Root="Bip01 R Upper"'), JOINTS)
    check("positive control: an IK root that is not a joint is A001, an error, and names the joint", codes(r) == ["A001"] and r[0]["severity"] == "error" and "Bip01 R Upper" in r[0]["message"], codes(r))
    r = A.check_chrparams("x.chrparams", CHR.replace('Weight="Bip01 Spine"', 'Weight="Bip01 Spin"'), JOINTS)
    check("positive control: an animation-driven IK blend bone that is not a joint is A001", codes(r) == ["A001"] and "ADIKTarget Weight" in r[0]["message"], codes(r))
    r = A.check_chrparams("x.chrparams", CHR.replace('<Joint name="Bip01 Spine"/>', '<Joint name="Bip01 Ghost"/>'), JOINTS)
    check("positive control: a LOD joint list entry that matches nothing is A001 as a WARNING (it changes nothing)", codes(r) == ["A001"] and r[0]["severity"] == "warning", [f["severity"] for f in r])
    r = A.check_chrparams("x.chrparams", CHR, JOINTS, lambda p: "animevents" not in p)
    check("positive control: a $ animation-list entry whose file does not exist is A002 (a plain entry is not judged)", codes(r) == ["A002"] and "$AnimEventDatabase" in r[0]["message"], codes(r))
    check("positive control: a chrparams that is not XML is reported, not a crash", codes(A.check_chrparams("x.chrparams", "<Params", JOINTS)) == ["A001"], "")
    check("joint names match without regard to case (the engine compares joint names case-insensitively)", A.check_chrparams("x", CHR.replace("Bip01 Pelvis", "bip01 pelvis"), JOINTS) == [], "")

    r = A.check_bspace("x.bspace", BSP, ANIMS)
    check("control: a blend space whose examples exist, parameters are real and indices are in range has no finding", r == [], codes(r))
    r = A.check_bspace("x.bspace", BSP.replace('AName="walk_b"', 'AName="walk_c"'), ANIMS)
    check("positive control: an example that is not in the animation set is A003 and names it", codes(r) == ["A003"] and "walk_c" in r[0]["message"], codes(r))
    r = A.check_bspace("x.bspace", BSP.replace('<Param name="TurnSpeed"', '<Param name="TurnSpeeed"'), ANIMS)
    check("positive control: a dimension that is not a motion parameter is A005", codes(r) == ["A005"] and "TurnSpeeed" in r[0]["message"], codes(r))
    r = A.check_bspace("x.bspace", BSP.replace('<Pseudo p0="1" p1="0"', '<Pseudo p0="1" p1="7"'), ANIMS)
    check("positive control: a Pseudo point that refers to a point which does not exist is A004", "A004" in codes(r), codes(r))
    r = A.check_bspace("x.bspace", BSP.replace('<Face p0="1" p1="2" p2="3"/>', '<Face p0="1" p1="2" p2="9"/>'), ANIMS)
    check("positive control: a Face beyond examples + pseudo points is A004", "A004" in codes(r), codes(r))
    r = A.check_bspace("x.bspace", BSP.replace("<Blendable>", "<Blendabel>").replace("</Blendable>", "</Blendabel>"), ANIMS)
    check("positive control: a blend space with no faces is A006 (a warning)", codes(r) == ["A006"] and r[0]["severity"] == "warning", codes(r))
    EVENTS = """<anim_event_list><animation name="Animations/h/walk_a.caf"><event name="footstep" parameter="" time="0.2"/></animation>
<animation name="Animations/h/vault.caf"><event name="audio_trigger" parameter="Play_vault" time="0.4"/><event name="break_high" parameter="" time="0.5"/></animation>
<animation name="Animations/h/swim.caf"><event name="effect" parameter="water.bubbles.arms" time="0.1"/></animation>
<animation name="Animations/h/unused.caf"><event name="audio_trigger" parameter="Play_unused" time="0.1"/></animation></anim_event_list>"""
    ev = A.clip_events(EVENTS)
    check("the events file is read per clip stem (the .caf name's base, lower case) with every event, its parameter and time", sorted(ev) == ["swim", "unused", "vault", "walk_a"] and ev["vault"][0] == ("audio_trigger", "Play_vault", "0.4"), sorted(ev))
    used = {"vault": "x.adb", "swim": "y.adb", "walk_a": "z.bspace"}
    r = A.check_events(used, ev, {"Play_other": {}}, {"smoke.a"})
    check("positive control: a used clip's audio_trigger that no project library defines is A007, information, naming the trigger and the cvar", codes(r, "Play_vault") == ["A007"] and [f["severity"] for f in r if f["code"] == "A007"] == ["info"] and all("ca_SkeletonEffectsPlayAudioInEngine" in f["message"] for f in r if f["code"] == "A007"), codes(r))
    check("positive control: a used clip's effect event that no particle library has is A008", codes(r, "water.bubbles.arms") == ["A008"], codes(r))
    check("control: a clip the project does not use is not reported (Play_unused), and footstep / break_high events are not audio triggers", codes(r, "Play_unused") == [] and codes(r, "footstep") == [] and len(r) == 2, codes(r))
    r2 = A.check_events(used, ev, {"play_vault": {}}, {"WATER.BUBBLES.ARMS".lower()})
    check("control: a trigger and an effect that exist (any letter case) leave nothing to report", r2 == [], codes(r2))
    check("the engine's 14 motion parameters are listed (CharacterManager::GetMotionParameterDetails)", len(A.MOTION_PARAMS) == 14 and "MoveSpeed" in A.MOTION_PARAMS and "BlendWeight7" in A.MOTION_PARAMS, len(A.MOTION_PARAMS))

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        d = A.names()
        # the set is the library's 1,692 names plus every loose .caf the project adds (the MotionBricks gaits: Assets/Animations/human/male/motionbricks): derived, not pinned, so a new project clip is not a failure
        loose = []
        for sub in ("motionbricks", "kimodo"):       # MotionBricks gaits / starts / stops, Kimodo attacks / aims / deaths / turns / sits
            folder = os.path.join(os.path.dirname(HERE), "Assets", "Animations", "human", "male", sub)
            loose += [f for f in os.listdir(folder) if f.endswith(".caf")] if os.path.isdir(folder) else []
        check("the engine resolves the villager's skeleton (163 joints, Bip01 ... weapon_bone) and its animation set (the library's 1,692 names + the project's %d loose clips)" % len(loose), d["created"] and len(d["joints"]) == 163 and len(d["animations"]) == 1692 + len(loose) and "Bip01 Pelvis" in d["joints"] and "weapon_bone" in d["joints"], (len(d["joints"]), len(d["animations"]), len(loose)))
        check("the project's blend space joined the animation set by its file name, as its chrparams comment says: 'villager_move' is one of the names", "villager_move" in [a.lower() for a in d["animations"]], "")
        res = A.check()
        check("the project's chrparams and blend space are checked, with no error: the IK and aim joints all exist and all 7 blend space examples are in the animation set", res["ok"] and res["checked"]["bspace"] and res["checked"]["chrparams"], res["checked"])
        lod = [f for f in res["findings"] if f["code"] == "A001"]
        check("the one real finding: the GameSDK's chrparams LOD list names 'Bip01 L ForeTwist3' and 'Bip01 R ForeTwist3', which this skeleton lacks (a warning)", sorted(f["message"].split("'")[1] for f in lod) == ["Bip01 L ForeTwist3", "Bip01 R ForeTwist3"] and all(f["severity"] == "warning" for f in lod), [f["message"].split("'")[1] for f in lod])
        ev_f = [f for f in res["findings"] if f["code"] in ("A007", "A008")]
        check("live: the one clip the villagers use that carries an audio event is the fence vault; Play_c_player_fol_ledge_grab_catjump has no trigger in the project (A007, information)", len(ev_f) == 1 and "vaultoverhigh_nw_3p_01" in ev_f[0]["where"] and "Play_c_player_fol_ledge_grab_catjump" in ev_f[0]["message"] and ev_f[0]["severity"] == "info", [(f["code"], f["where"][:40]) for f in ev_f])
        check("live: the events file was read from the GameSDK pak through the chrparams' $AnimEventDatabase entry (623 clips with events)", res["checked"].get("events") == "Animations/human/male/events.animevents" and len(A.clip_events(A.read_game_file(res["checked"]["events"]))) == 623, res["checked"].get("events"))
        d2 = A.names("Objects/Characters/zz_no_such.cdf")
        check("control: a character that does not exist is reported as not created, with no names", not d2["created"] and "joints" not in d2, d2)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
