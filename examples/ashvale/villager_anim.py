#!/usr/bin/env python3
"""The villagers' animation set as ONE table: it writes the four Mannequin files and the C++ table, and checks every clip against the GameSDK library (anim_inventory.py).

    python examples/ashvale/villager_anim.py check        # validate the table (clips exist, weaponless third person, durations sane) and say whether the files on disk equal what the table makes
    python examples/ashvale/villager_anim.py write        # write Assets/Animations/Mannequin/ADB/Villager*.{adb,xml} and Code/Components/VillagerAnimTable.h

Why: the villagers originally had 13 clips in all, one idle for every NPC, soldier 'relaxed' locomotion, and Startle/Attack were a first-person
kick. The sample's library has hundreds of weaponless gestures; this table adds what the code can now use: a random FIDGET while standing (scratch, yawn, shrug, tap a foot, ...), TALK gestures while in
conversation, a short HIT reaction, a flinch for Startle, and LookAt (the engine's own Look IK: the head and eyes follow a target the code sets each frame).

DESIGN: one fragment PER CLIP (Fidget_Yawn, Talk_1 ...), not options of one fragment, because the code needs each clip's duration to know when the gesture is over and Mannequin picks an option at random
without telling you which; the code chooses at random from the generated table (VillagerAnimTable.h), so the database and the code cannot drift apart. mannequin_check then loads the result through the
engine's own loaders. Legacy fragments (Idle, Walk, Run, Vault, OpenGate, Crouch*, Attack, Death, Aim) are kept exactly as they were: Death's clips travel 2-5 m of root motion and Attack has no weaponless
replacement in the library (no sword swing exists there: that needs outside content), both are listed in NOT_DONE.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)

PROJECT = __import__("project_root").PROJECT
ADB_DIR = os.path.join(PROJECT, "Assets", "Animations", "Mannequin", "ADB")
HEADER = os.path.join(PROJECT, "Code", "Components", "VillagerAnimTable.h")

# (fragment, scope, controller flags, blend-out, [(clip, flags, blend-in)])   one AnimLayer per tuple, in order
LEGACY = [
    ("Idle", "FullBody", "Persistent+AutoReinstall", 0.2, [("relaxed_tac_idle_nw_3p_01", "Loop", 0.3), ("stand_tac_weaponpose_nw_3p_01", "Loop", 0.3)]),
    ("Walk", "FullBody", "", 0.2, [("villager_move", "Loop", 0.3), ("relaxed_tac_weaponpose_nw_3p_01", "Loop", 0.3)]),
    ("Run", "FullBody", "", 0.2, [("relaxed_tac_run_nw_fwd_fast_3p_01", "Loop", 0.3)]),
]
LEGACY_AFTER = [
    ("Vault", "FullBody", "", 0.2, [("stand_tac_vaultOverHigh_nw_3p_01", "", 0.15)]),
    ("OpenGate", "FullBody", "", 0.2, [("stand_tac_opendoor_nw_1p_01", "", 0.25)]),
    ("CrouchIn", "FullBody", "", 0.2, [("relaxed_tac_idle_crouchRepairElectricBoxIn_3p_01", "", 0.25)]),
    ("CrouchLoop", "FullBody", "", 0.2, [("relaxed_tac_idle_crouchRepairElectricBox_3p_01", "Loop", 0.2)]),
    ("CrouchOut", "FullBody", "", 0.2, [("relaxed_tac_idle_crouchRepairElectricBoxOut_3p_01", "", 0.2)]),
    ("Attack", "FullBody", "", 0.2, [("melee_killingblow_rightkick_1p_01", "", 0.12)]),
    ("Death", "FullBody", "", 0.2, [("stand_tac_death_loco_aVel_torso_fwd_01", "", 0.1)]),
    ("Aim", "FullBody", "", 0.25, [("stand_tac_shootidle_rifle_3p_01", "Loop", 0.25)]),
]
# Startle was the first-person kick; a flinch is what a startled villager does.
STARTLE = ("Startle", "FullBody", "", 0.2, [("stand_tac_flinch_front_01", "", 0.15)])

# GAITS: the villagers' other ways of walking, MotionBricks clips (NVIDIA's generative locomotion model, retargeted onto the Bip01 rig and compiled by motionbricks_clip.py). One looping clip
# per fragment; the order is the code's ENpcGait order after Normal (Slow, Scared, Injured, Stealth). The speed each clip travels at comes from motionbricks_clips.json (measured on the cycle), because the code
# moves the body at exactly that speed and scales playback by the remainder.
GAITS = [("WalkSlow", "mb_walk_slow"), ("WalkScared", "mb_walk_scared"), ("WalkInjured", "mb_walk_injured"), ("WalkStealth", "mb_walk_stealth")]
GAIT_CLIP_DIR = os.path.join(PROJECT, "Assets", "Animations", "human", "male", "motionbricks")
GAIT_MANIFEST = os.path.join(HERE, "motionbricks_clips.json")


def manifest():
    """{clip: measured numbers} from motionbricks_clips.json (motionbricks_clip.py, kimodo_clips.py and motionbricks_actions.py keep it); {} if it is missing."""
    import json
    try:
        return json.load(open(GAIT_MANIFEST))["clips"]
    except (OSError, ValueError, KeyError):
        return {}


def gait_speeds():
    """{clip: metres per second} from the manifest; {} if it is missing."""
    return {n: c["speed"] for n, c in manifest().items() if "speed" in c}


def f32(v):
    """A C++ float literal: 0 -> 0.0f, 1 -> 1.0f, -91 -> -91.0f ('%gf' alone writes 0f, which does not compile)."""
    s = "%g" % v
    return s + ("f" if ("." in s or "e" in s) else ".0f")


def turn_profile(clip):
    """The clip's heading curve as progress 0..1 every 0.1 s (from the manifest's yaw_profile in degrees); the signed total in degrees."""
    c = manifest().get(clip, {})
    prof = c.get("yaw_profile") or []
    total = prof[-1] if prof else 0.0
    return ([max(0.0, min(1.0, v / total)) for v in prof] if abs(total) > 1.0 else []), float(total)


# standing gestures, played one at a time at random while a villager has stood idle for a while (weaponless, third person, 4-12 s)
FIDGETS = [
    ("Fidget_Dawdle", "relaxed_tac_idledawdling_nw_3p_01"), ("Fidget_DrumLegs", "relaxed_tac_idledrummingonlegs_nw_3p_01"), ("Fidget_HeadScratch", "relaxed_tac_idleheadscratch_3p_05"),
    ("Fidget_Swat1", "relaxed_tac_idleinsectswat_lefthand_3p_01"), ("Fidget_Swat2", "relaxed_tac_idleinsectswat_lefthand_3p_02"), ("Fidget_RubNeck", "relaxed_tac_idlerubneck_3p_01"),
    ("Fidget_Shift1", "relaxed_tac_idleshift_3p_01"), ("Fidget_Shift2", "relaxed_tac_idleshift_3p_02"), ("Fidget_TapFoot", "relaxed_tac_idletappingfoot_3p_01"),
    ("Fidget_Teeter", "relaxed_tac_idleteetering_nw_3p_01"), ("Fidget_Yawn", "relaxed_tac_idleyawn_nw_3p_01"),
    ("Fidget_Shrug1", "relaxed_tac_idleshouldershrug_3p_01"), ("Fidget_Shrug2", "relaxed_tac_idleshouldershrug_3p_02"), ("Fidget_Shrug3", "relaxed_tac_idleshouldershrug_3p_03"),
    ("Fidget_KickDust", "relaxed_tac_idlekickdust_3p_01"), ("Fidget_KickStone", "relaxed_tac_idlekickstone_3p_01"),
]
# played while the villager is in conversation with the player (the code re-queues one when it ends)
TALKS = [("Talk_1", "relaxed_tac_idletalk_nw_3p_01"), ("Talk_2", "relaxed_tac_idletalk_nw_3p_02"), ("Talk_3", "relaxed_tac_idletalk_nw_3p_03")]
# a short reaction when someone is hit and lives; light hits only (the heavier ones travel 1-3 m of root motion)
HITS = [("Hit_Front", "stand_tac_hit_light_front_torsoup_3p_02"), ("Hit_Left", "stand_tac_hit_light_lft_torso_3p_02"), ("Hit_Right", "stand_tac_hit_light_rgt_torso_3p_02"), ("Hit_Flinch", "stand_tac_flinch_front_01")]
# KIMODO (NVIDIA's text-to-motion model, human skeleton; kimodo_clips.py): what the library lacks. One fragment per clip, picked at random by the code (never the same one twice running).
# The attack's hit moment (the instant the club lands, from the hand's speed peak) comes from the manifest, so BanditBrain damages when the blow lands, not at a constant.
ATTACKS = [("Attack_A", "kd_attack_a"), ("Attack_B", "kd_attack_b"), ("Attack_C", "kd_attack_c")]
AIMS = [("Aim_A", "kd_aim_a"), ("Aim_B", "kd_aim_b")]
DEATHS = [("Death_A", "kd_death_a"), ("Death_B", "kd_death_b"), ("Death_C", "kd_death_c")]
SITS = [("SitDown", "kd_sit_down", ""), ("SitLoop", "kd_sit_idle", "Loop"), ("StandUp", "kd_stand_up", "")]
# turning on the spot: the code rotates the body along the clip's own heading curve (manifest yaw_profile) so the planted feet pivot with it
TURNS = [("TurnLeft", "kd_turn_left"), ("TurnRight", "kd_turn_right"), ("Turn180Left", "kd_turn_180_left"), ("Turn180Right", "kd_turn_180_right")]
# MotionBricks starts and stops, one pair per gait (same order as GAITS): standing -> walking, walking -> standing. Their travel and speed profile are in the manifest.
TRANSITIONS = [("Slow", "mb_start_slow", "mb_stop_slow"), ("Scared", "mb_start_scared", "mb_stop_scared"), ("Injured", "mb_start_injured", "mb_stop_injured"), ("Stealth", "mb_start_stealth", "mb_stop_stealth")]
# KIMODO clips: work, daily life, fear and combat reactions, more talk and idle variety. (fragment, clip, loops, C++ name); every row becomes one Mannequin fragment
# and one `VillagerAnim::SActionClip` constant (clip length, locator speed, root travel, end heading from the manifest); the arrays below name the fragments that are variants of one thing.
ACTIONS = [
    ("Work_Chop", "kd_work_chop", True, "kWorkChop"), ("Work_Hammer", "kd_work_hammer", True, "kWorkHammer"), ("Work_Sweep", "kd_work_sweep", True, "kWorkSweep"), ("Work_Hoe", "kd_work_hoe", True, "kWorkHoe"),
    ("Carry_Walk", "kd_work_carry_walk", True, "kCarryWalk"),
    ("Eat", "kd_eat", True, "kEat"), ("Drink", "kd_drink", False, "kDrink"),
    ("Sleep_Down", "kd_sleep_down", False, "kSleepDown"), ("Sleep_Loop", "kd_sleep_loop", True, "kSleepLoop"), ("Sleep_Up", "kd_sleep_up", False, "kSleepUp"),
    ("Pray_In", "kd_pray_in", False, "kPrayIn"), ("Pray_Loop", "kd_pray_loop", True, "kPrayLoop"), ("Pray_Out", "kd_pray_out", False, "kPrayOut"),
    ("Bow", "kd_bow", False, "kBow"), ("Greet", "kd_wave_greet", False, "kGreet"),
    ("Cower", "kd_cower", True, "kCower"), ("Flee", "kd_flee_run", True, "kFlee"),
    ("Block", "kd_block", False, "kBlock"), ("ParryRecoil", "kd_parry_recoil", False, "kParryRecoil"), ("DodgeSide", "kd_dodge_side", False, "kDodgeLeft"), ("DodgeSideR", "kd_dodge_side_r", False, "kDodgeRight"),
    ("HitHeavyFront", "kd_hit_heavy_front", False, "kHitHeavyFront"), ("HitHeavyBack", "kd_hit_heavy_back", False, "kHitHeavyBack"),
    ("Knockdown", "kd_knockdown", False, "kKnockdown"), ("GetUp", "kd_get_up", False, "kGetUp"), ("WoundedCrawl", "kd_wounded_crawl", True, "kWoundedCrawl"),
    ("TalkExtra_1", "kd_talk_point", False, "kTalkExtra1"), ("TalkExtra_2", "kd_talk_plead", False, "kTalkExtra2"), ("TalkExtra_3", "kd_talk_laugh", False, "kTalkExtra3"),
    ("TalkExtra_4", "kd_talk_nod", False, "kTalkExtra4"), ("TalkExtra_5", "kd_talk_shake_head", False, "kTalkExtra5"),
    ("IdleVariant_1", "kd_idle_tired", True, "kIdleVariant1"), ("IdleVariant_2", "kd_idle_nervous", True, "kIdleVariant2"), ("IdleVariant_3", "kd_idle_arms_folded", True, "kIdleVariant3"),
    ("IdleVariant_4", "kd_idle_look_around", True, "kIdleVariant4"),
]
ACTION_ARRAYS = [("kWorks", ["Work_Chop", "Work_Hammer", "Work_Sweep", "Work_Hoe"]), ("kTalkExtras", ["TalkExtra_%d" % i for i in range(1, 6)]), ("kIdleVariants", ["IdleVariant_%d" % i for i in range(1, 5)]),
                 ("kHitHeavies", ["HitHeavyFront", "HitHeavyBack"]), ("kDodges", ["DodgeSide", "DodgeSideR"])]
ACTION_QUICK = {"Block", "ParryRecoil", "DodgeSide", "DodgeSideR", "HitHeavyFront", "HitHeavyBack", "Knockdown"}      # reactions: a short blend in, the body is already moving
KIMODO_CLIP_DIR = os.path.join(PROJECT, "Assets", "Animations", "human", "male", "kimodo")
LOOK_CLIP = "stand_tac_lookposes_head_3p_01"        # head and eyes; the full-body variant also turns the spine
LOOK_SCOPE = ("LookPose", 15, 1)                    # the engine's default look-IK layer (manual: Look IK)
LEGACY_NONWEAPONLESS = {"melee_killingblow_rightkick_1p_01", "stand_tac_shootidle_rifle_3p_01", "stand_tac_opendoor_nw_1p_01"}   # kept as they were (see NOT_DONE)
NOT_DONE = ["Sit: the clips and actions exist (SitDown / SitLoop / StandUp) but the level has no bench or stool for a villager to use, so nothing plays them yet",
            "Start / Stop: the fragments and clips exist and are engine-validated, but villagers move by the navigation system at a constant speed; playing them needs the path follower's braking checked in the running game",
            "Work, eat, drink, sleep, pray, bow, greet, cower, flee, block, parry, dodge, heavy hits, knockdown, get up, wounded crawl, extra talk and idle variants (Kimodo): the clips and fragments exist, nothing plays them yet (brains, bed and seat anchors, props such as axe and basket are the game's)"]


def fragments():
    out = list(LEGACY)
    out.append(STARTLE)
    out += LEGACY_AFTER
    out += [(n, "FullBody", "", 0.3, [(c, "", 0.3)]) for n, c in FIDGETS]
    out += [(n, "FullBody", "", 0.3, [(c, "", 0.3)]) for n, c in TALKS]
    out += [(n, "FullBody", "", 0.2, [(c, "", 0.1)]) for n, c in HITS]
    out += [(n, "FullBody", "", 0.25, [(c, "Loop", 0.35)]) for n, c in GAITS]
    out += [(n, "FullBody", "", 0.2, [(c, "", 0.12)]) for n, c in ATTACKS]
    out += [(n, "FullBody", "", 0.25, [(c, "", 0.25)]) for n, c in AIMS]
    out += [(n, "FullBody", "", 0.2, [(c, "", 0.1)]) for n, c in DEATHS]
    out += [(n, "FullBody", "", 0.3, [(c, fl, 0.3)]) for n, c, fl in SITS]
    out += [(n, "FullBody", "", 0.25, [(c, "", 0.25)]) for n, c in TURNS]
    for n, c, loop, _ in ACTIONS:
        out.append((n, "FullBody", "", 0.3 if loop else 0.25, [(c, "Loop" if loop else "", 0.3 if loop else (0.1 if n in ACTION_QUICK else 0.25))]))
    for gait, start, stop in TRANSITIONS:
        out.append(("Start" + gait, "FullBody", "", 0.25, [(start, "", 0.2)]))
        out.append(("Stop" + gait, "FullBody", "", 0.25, [(stop, "", 0.2)]))
    return out


def files():
    frs = fragments()
    ids = ["Idle", "Walk", "Run", "Startle", "Vault", "OpenGate", "CrouchIn", "CrouchLoop", "CrouchOut", "Attack", "Death", "Aim"]
    names = [f[0] for f in frs] + ["LookAt"]
    # keep the original order for the legacy IDs (the engine reads IDs by name, but a stable file is easier to diff)
    order = [n for n in ids if n in names] + [n for n in names if n not in ids]
    frag_ids = '<TagDefinition version="2">\n <Tags>\n' + "".join(' <Tag name="%s"/>\n'.replace(" <Tag", "  <Tag", 1) % n for n in order) + " </Tags>\n</TagDefinition>\n"
    ctl = ['<ControllerDef>', ' <Tags filename="Animations/Mannequin/ADB/VillagerTags.xml"/>', ' <Fragments filename="Animations/Mannequin/ADB/VillagerFragmentIds.xml"/>', ' <FragmentDefs>']
    for n in order:
        if n == "LookAt":
            ctl.append('  <LookAt scopes="LookPose" flags="Persistent+AutoReinstall"/>')
            continue
        f = next(x for x in frs if x[0] == n)
        ctl.append('  <%s scopes="%s"%s/>' % (n, f[1], (' flags="%s"' % f[2]) if f[2] else ""))
    ctl += [' </FragmentDefs>', ' <ScopeContextDefs>', '  <MainCharacter/>', ' </ScopeContextDefs>', ' <ScopeDefs>', '  <FullBody layer="0" numLayers="3" context="MainCharacter"/>',
            '  <%s layer="%d" numLayers="%d" context="MainCharacter"/>' % LOOK_SCOPE, ' </ScopeDefs>', '</ControllerDef>']
    adb = ['<AnimDB FragDef="Animations/Mannequin/ADB/VillagerFragmentIds.xml" TagDef="Animations/Mannequin/ADB/VillagerTags.xml">', ' <FragmentList>']
    for n in order:
        adb.append("  <%s>" % n)
        if n == "LookAt":
            adb += ['   <Fragment BlendOutDuration="0.3" Tags="">', '    <ProcLayer>', '     <Blend ExitTime="0" StartTime="0" Duration="0.3"/>', '     <Procedural type="LookPose">',
                    '      <ProceduralParams CryXmlVersion="2" Animation="%s" BlendTime="0.4" ScopeLayer="0"/>' % LOOK_CLIP, '     </Procedural>', '    </ProcLayer>', '   </Fragment>']
        else:
            f = next(x for x in frs if x[0] == n)
            adb.append('   <Fragment BlendOutDuration="%g" Tags="">' % f[3])
            for clip, fl, blend in f[4]:
                adb += ['    <AnimLayer>', '     <Blend ExitTime="0" StartTime="0" Duration="%g"/>' % blend, '     <Animation name="%s"%s/>' % (clip, (' flags="%s"' % fl) if fl else ""), '    </AnimLayer>']
            adb.append("   </Fragment>")
        adb.append("  </%s>" % n)
    adb += [" </FragmentList>", "</AnimDB>"]
    cpp = ["// GENERATED by villager_anim.py (python examples/ashvale/villager_anim.py write): do not edit; change the table there.",
           "// The villagers' gesture fragments (Assets/Animations/Mannequin/ADB/Villager.adb) and the clip each one plays, so the code can read the clip's length and pick one at random.",
           "#pragma once", "", "namespace VillagerAnim", "{", "struct SGesture", "{", "\tconst char* szFragment;", "\tconst char* szClip;", "};", ""]
    for arr, table in (("kFidgets", FIDGETS), ("kTalks", TALKS), ("kHits", HITS)):
        cpp.append("constexpr SGesture %s[] = {" % arr)
        cpp += ['\t{ "%s", "%s" },' % (n, c) for n, c in table]
        cpp += ["};", "constexpr int %sCount = %d;" % (arr, len(table)), ""]
    speeds = gait_speeds()
    cpp.append("// The gaits (MotionBricks): fragment, clip, and the clip's own travel speed in m/s (same order; the first is ENpcGait::Slow).")
    cpp.append("constexpr SGesture kGaits[] = {")
    cpp += ['\t{ "%s", "%s" },' % (n, c) for n, c in GAITS]
    cpp += ["};", "constexpr float kGaitSpeeds[] = { %s };" % ", ".join(f32(speeds.get(c, 0.0)) for _, c in GAITS), "constexpr int kGaitsCount = %d;" % len(GAITS), ""]
    man = manifest()
    cpp += ["// Kimodo's attack / aim / death variants: fragment, clip and, for an attack, the second into the clip at which the blow lands (the hand's speed peak).", "struct SVariant", "{",
            "\tconst char* szFragment;", "\tconst char* szClip;", "\tfloat       hitSeconds;", "};", ""]
    for arr, table in (("kAttacks", ATTACKS), ("kAims", AIMS), ("kDeaths", DEATHS)):
        cpp.append("constexpr SVariant %s[] = {" % arr)
        cpp += ['\t{ "%s", "%s", %s },' % (n, c, f32(man.get(c, {}).get("hit_time", 0.0))) for n, c in table]
        cpp += ["};", "constexpr int %sCount = %d;" % (arr, len(table)), ""]
    cpp.append("// Sitting (Kimodo): down, a loop while seated, up. The clips carry the body's travel to and from a seat (root_travel in motionbricks_clips.json); no seat exists in the level yet.")
    for (n, c, fl), tag in zip(SITS, ("SitDown", "SitLoop", "StandUp")):
        cpp.append('constexpr SGesture k%s = { "%s", "%s" };' % (tag, n, c))
    cpp += ["",
            "// Turning on the spot (Kimodo): left / right about a quarter turn, then the two half turns. `degrees` is the signed turn the clip makes (left positive); `profile` is how far through the turn the heading is",
            "// (0..1) every 0.1 s of the clip, so the code can turn the body along the same curve.", "struct STurnClip", "{", "\tconst char*  szFragment;", "\tconst char*  szClip;", "\tfloat        degrees;",
            "\tconst float* profile;", "\tint          steps;", "};", ""]
    for i, (n, c) in enumerate(TURNS):
        prof, total = turn_profile(c)
        cpp.append("constexpr float kTurnProfile%d[] = { %s };" % (i, ", ".join(f32(round(v, 3)) for v in (prof or [0.0, 1.0]))))
    cpp.append("constexpr STurnClip kTurns[] = {")
    for i, (n, c) in enumerate(TURNS):
        prof, total = turn_profile(c)
        cpp.append('\t{ "%s", "%s", %s, kTurnProfile%d, %d },' % (n, c, f32(round(total, 1)), i, len(prof or [0.0, 1.0])))
    cpp += ["};", "constexpr int kTurnsCount = %d;" % len(TURNS), ""]
    cpp += ["// Starts and stops of the gaits (MotionBricks), same order as kGaits: standing -> walking, walking -> standing, and the metres the root covers in each (the speed profiles are in motionbricks_clips.json).",
            "struct SGaitTransition", "{", "\tconst char* szStartFragment;", "\tconst char* szStartClip;", "\tconst char* szStopFragment;", "\tconst char* szStopClip;", "\tfloat       startTravel;",
            "\tfloat       stopTravel;", "};", "constexpr SGaitTransition kTransitions[] = {"]
    for gait, start, stop in TRANSITIONS:
        def travel(clip):
            t = man.get(clip, {}).get("root_travel", [0.0, 0.0])
            return (t[0] ** 2 + t[1] ** 2) ** 0.5
        cpp.append('\t{ "Start%s", "%s", "Stop%s", "%s", %s, %s },' % (gait, start, gait, stop, f32(round(travel(start), 3)), f32(round(travel(stop), 3))))
    cpp += ["};", "constexpr int kTransitionsCount = %d;" % len(TRANSITIONS), ""]
    cpp += ["// Work, daily life, fear and combat reactions, talk and idle variety (Kimodo). `durationSeconds` is the clip's length; `speed` is the m/s the locator travels (only the walking and running loops and the crawl:",
            "// the code moves the body at it); `travelX/Y` are the metres the locator covers over the clip (a dodge leaves 1.1 m to the side, a get-up carries the body up from the ground), `endYawDegrees` its final heading",
            "// (left positive); `bLoop` clips are meant to be played with the Loop flag until the code stops them.", "struct SActionClip", "{", "\tconst char* szFragment;", "\tconst char* szClip;", "\tfloat       durationSeconds;",
            "\tfloat       speed;", "\tfloat       travelX;", "\tfloat       travelY;", "\tfloat       endYawDegrees;", "\tbool        bLoop;", "};", ""]
    by_name = {}
    for n, c, loop, cname in ACTIONS:
        m = man.get(c, {})
        t = m.get("root_travel", [0.0, 0.0])
        cpp.append('constexpr SActionClip %s = { "%s", "%s", %s, %s, %s, %s, %s, %s };' % (cname, n, c, f32(round(m.get("duration", 0.0), 3)), f32(round(m.get("speed", 0.0), 3)), f32(round(t[0], 3)), f32(round(t[1], 3)),
                                                                                         f32(round(m.get("end_yaw_deg", 0.0), 1)), "true" if loop else "false"))
        by_name[n] = cname
    cpp.append("")
    for arr, names in ACTION_ARRAYS:
        cpp.append("constexpr SActionClip %s[] = { %s };" % (arr, ", ".join(by_name[n] for n in names)))
        cpp.append("constexpr int %sCount = %d;" % (arr, len(names)))
    cpp.append("")
    cpp += ['constexpr const char* kLookAtFragment = "LookAt";', "}", ""]
    return {
        os.path.join(ADB_DIR, "VillagerFragmentIds.xml"): frag_ids,
        os.path.join(ADB_DIR, "VillagerControllerDefinition.xml"): "\n".join(ctl) + "\n",
        os.path.join(ADB_DIR, "Villager.adb"): "\n".join(adb) + "\n",
        HEADER: "\n".join(cpp),
    }


def engine_animations():
    """the animation names (lower case) the RUNNING editor knows, or None when the editor cannot be asked (then the 'clip exists in the engine' checks are skipped)"""
    import anim_check
    try:
        return {a.lower() for a in anim_check.names()["animations"]}
    except Exception as exc:                                      # noqa: BLE001 - no editor / bridge error: the offline checks still run
        print("NOTE editor not reachable (%s): engine animation checks skipped" % str(exc)[:120])
        return None


def problems():
    """-> [text]: every clip must exist in the library; new clips must be weaponless third person; gesture lengths sane."""
    import anim_inventory
    engine = engine_animations()
    inv = {c["name"].lower(): c for c in anim_inventory.load()}
    out = []
    seen = set()
    for name, scope, flags, blend, layers in fragments():
        if name in seen:
            out.append("fragment %s twice" % name)
        seen.add(name)
    for group, table, lo, hi in (("fidget", FIDGETS, 2.0, 25.0), ("talk", TALKS, 4.0, 25.0), ("hit", HITS, 0.3, 3.0)):
        for n, clip in table:
            c = inv.get(clip.lower())
            if engine is not None and clip.lower() not in engine:
                out.append("%s %s: clip %s is not in the character's animation set" % (group, n, clip))
            elif c is None:
                out.append("%s %s: clip %s has no header in the library" % (group, n, clip))
            else:
                if not c["civilian"]:
                    out.append("%s %s: clip %s is not weaponless third person (%s)" % (group, n, clip, c))
                if not lo <= c["dur"] <= hi:
                    out.append("%s %s: clip %s lasts %.2f s, outside %.1f-%.1f" % (group, n, clip, c["dur"], lo, hi))
    speeds = gait_speeds()
    for n, clip in GAITS:
        if not os.path.isfile(os.path.join(GAIT_CLIP_DIR, clip + ".caf")):
            out.append("gait %s: %s.caf is not in Assets/Animations/human/male/motionbricks (python animation/motionbricks_clip.py <style> %s)" % (n, clip, clip))
        if not 0.2 <= speeds.get(clip, 0.0) <= 2.5:
            out.append("gait %s: clip %s has no sane speed in motionbricks_clips.json (%s)" % (n, clip, speeds.get(clip)))
    man = manifest()
    generated = [(n, c, KIMODO_CLIP_DIR) for t in (ATTACKS, AIMS, DEATHS, TURNS) for n, c in t] + [(n, c, KIMODO_CLIP_DIR) for n, c, _ in SITS]
    generated += [(n, c, GAIT_CLIP_DIR) for g, a, b in TRANSITIONS for n, c in (("Start" + g, a), ("Stop" + g, b))]
    for n, clip, folder in generated:
        if not os.path.isfile(os.path.join(folder, clip + ".caf")):
            out.append("%s: %s.caf is not in %s (python animation/kimodo_clips.py / motionbricks_actions.py)" % (n, clip, os.path.relpath(folder, PROJECT)))
        if clip not in man:
            out.append("%s: clip %s is not in motionbricks_clips.json" % (n, clip))
    for n, clip, loop, cname in ACTIONS:
        if not os.path.isfile(os.path.join(KIMODO_CLIP_DIR, clip + ".caf")):
            out.append("%s: %s.caf is not in %s (python animation/kimodo_clips.py %s)" % (n, clip, os.path.relpath(KIMODO_CLIP_DIR, PROJECT), clip))
        m = man.get(clip)
        if m is None:
            out.append("%s: clip %s is not in motionbricks_clips.json" % (n, clip))
            continue
        if bool(m.get("checks", {}).get("loop")) != loop:
            out.append("%s: the table says loop=%s, the manifest's loop promise is %s" % (n, loop, m.get("checks", {}).get("loop")))
        if not 0.4 <= m.get("duration", 0.0) <= 10.0:
            out.append("%s: clip %s lasts %.2f s, outside 0.4-10" % (n, clip, m.get("duration", 0.0)))
        if loop and m.get("duration", 0.0) < 1.5 and not m.get("speed", 0.0) > 0.0:
            out.append("%s: a looping clip of %.2f s without travel is too short to read as a loop" % (n, m.get("duration", 0.0)))
    action_names = [a[0] for a in ACTIONS]
    for arr, members in ACTION_ARRAYS:
        for n in members:
            if n not in action_names:
                out.append("array %s names %s, which is not in ACTIONS" % (arr, n))
    if len({a[3] for a in ACTIONS}) != len(ACTIONS):
        out.append("two ACTIONS rows share a C++ name")
    for n, clip in ATTACKS:
        if not 0.2 <= man.get(clip, {}).get("hit_time", 0.0) <= 0.9:
            out.append("attack %s: hit_time %s outside 0.2-0.9 s" % (n, man.get(clip, {}).get("hit_time")))
    for n, clip in TURNS:
        prof, total = turn_profile(clip)
        if len(prof) < 5 or abs(total) < 60.0:
            out.append("turn %s: clip %s has no usable yaw_profile in the manifest (%d steps, %.0f deg)" % (n, clip, len(prof), total))
    pending = pending_clips(engine)
    for clip in [l[0] for f in fragments() for l in f[4]] + [LOOK_CLIP]:
        if engine is not None and clip.lower() not in engine and clip not in pending:
            out.append("clip %s is not in the character's animation set" % clip)
    return out


def pending_clips(engine):
    """The ACTIONS clips that are compiled (a .caf on disk) but not in the RUNNING editor's animation set yet: the engine caches loose assets by name, so a new clip is only seen after an editor restart
    (anim_validate.py / mannequin_check then prove it). Not a problem of the table, so `problems` lets them through and `check` reports them."""
    if engine is None:
        return set()
    return {c for _, c, _, _ in ACTIONS if c.lower() not in engine and os.path.isfile(os.path.join(KIMODO_CLIP_DIR, c + ".caf"))}


def check():
    bad = problems()
    for b in bad:
        print("PROBLEM", b)
    stale = [os.path.relpath(p, PROJECT) for p, t in files().items() if not os.path.isfile(p) or open(p, encoding="utf-8").read() != t]
    print("%d fragments (%d fidgets, %d talks, %d hits, %d work/life/fear/variety actions, LookAt); %d problem(s); files on disk %s" % (len(fragments()) + 1, len(FIDGETS), len(TALKS), len(HITS), len(ACTIONS), len(bad), "equal the table" if not stale else "DIFFER: " + ", ".join(stale)))
    engine = engine_animations()
    pend = sorted(pending_clips(engine))
    if pend:
        print("NOTE %d new clip(s) are compiled but the running editor has not loaded them yet (restart it, then anim_validate.py and mannequin_check): %s" % (len(pend), ", ".join(pend[:6]) + (" ..." if len(pend) > 6 else "")))
    return not bad and not stale


def write():
    bad = problems()
    if bad:
        raise SystemExit("not written: " + "; ".join(bad[:4]))
    for p, t in files().items():
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.isfile(p) and not os.path.exists(p + ".bak"):
            import shutil
            shutil.copy2(p, p + ".bak")           # the first original is kept once
        tmp = p + ".tmp"                      # written whole, then renamed: a concurrent build never reads half a header
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        os.replace(tmp, p)
        print("wrote", os.path.relpath(p, PROJECT))
    print("not done (needs outside content or a placement check):", "; ".join(NOT_DONE))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(0 if check() else 1)
    elif cmd == "write":
        write()
    else:
        print(__doc__)
