#!/usr/bin/env python3
"""Kimodo (NVIDIA text-to-motion, human SOMA skeleton) motions -> the villagers' attack, aim, death and sit clips. Project Python, numpy only (the generation itself runs in a separate Kimodo install, see docs/animation-pipeline.md).

    python animation/kimodo_clips.py [name ...] [--no-rc] [--out <folder for the .caf>]

Sources are the chosen generated motions kept in anim_sources/kimodo/<src>.npz (model Kimodo-SOMA-RP-v1.1, prompt and seed in CLIPS below: the same prompt and seed give the same motion only on the same
machine and library versions, which is why the motion files are kept). Per clip: retarget onto Bip01 (kimodo_retarget), cut a window (anim_oneshot), build the curves into a Crytek FBX template (motionbricks_fbx),
compile with rc.exe, and write what the game needs into motionbricks_clips.json (duration, the root's travel, and for attacks the moment of impact).

kinds: oneshot (plays once, root motion in the locator), loop (an in-place loop closed by a crossfade).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import os
import sys

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import anim_oneshot as AO  # noqa: E402
import kimodo_retarget as KR  # noqa: E402
import motionbricks_clip as MC  # noqa: E402
import motionbricks_fbx as MF  # noqa: E402
import motionbricks_loop as ML  # noqa: E402
import motionbricks_retarget as MR  # noqa: E402

SRC = os.path.join(HERE, "anim_sources")
import project_root  # noqa: E402
OUT = os.path.join(project_root.ASSETS, "Animations", "human", "male", "kimodo")      # the active project's Assets (project_root.py)
# MOTIONBRICKS_WORK (default <MOTIONBRICKS_ROOT>/work): scratch folder for the clips being built; KIMODO_OUT: where Kimodo wrote its candidate .npz files (used when a candidate is not yet in anim_sources/kimodo)
WORK = os.environ.get("MOTIONBRICKS_WORK") or os.path.join(_paths.MOTIONBRICKS_ROOT, "work")
KIMODO_OUT = os.environ.get("KIMODO_OUT") or os.path.join(HERE, "anim_work", "kimodo_out")
MODEL = "Kimodo-SOMA-RP-v1.1"
STANDING = ("attack", "aim", "work", "talk", "idle", "greet", "react", "flee", "daily")    # groups whose clips keep the feet on the ground (checks.feet_on_ground); deaths, sits, sleep, prayer on the knees, falls do not

# name: source file (anim_sources/kimodo/<src>.npz), the prompt/seed it came from, how to cut it
CLIPS = {
    # a bandit's club strike: the moment the right hand is fastest is the impact, placed 0.55 s into a 1.1 s clip (BanditBrain kHitMoment 0.55, the strike ends at 1.1 s)
    "kd_attack_a": dict(src="attack_side_00", prompt="A person swings a club sideways across their body in a wide strike.", seed=12, cut="peak", pre=0.55, post=0.55, root="fixed", kind="oneshot", group="attack"),
    "kd_attack_b": dict(src="attack_two_03", prompt="A person attacks with a club, swinging it hard at an enemy in front of them.", seed=13, cut="peak", pre=0.55, post=0.55, root="fixed", kind="oneshot", group="attack"),
    "kd_attack_c": dict(src="attack_overhead_01", prompt="A person swings a heavy club in a strong overhead strike.", seed=11, cut="peak", peak_after=1.1, pre=0.55, post=0.55, root="fixed", kind="oneshot", group="attack"),    # the raise (frame 17) is the first speed peak; the downswing is the one after 1.1 s
    # an archer's draw: raised, drawn and held for the 1.1 s the brain waits before the release, then 0.4 s more; mirrored so the LEFT arm holds the bow out and the right hand is at the face
    "kd_aim_a": dict(src="aimB_rifle_01", prompt="A person raises a rifle to their shoulder and aims at a target.", seed=73, cut="range", t0=0.0, t1=1.6, mirror=True, root="fixed", kind="oneshot", group="aim"),
    "kd_aim_b": dict(src="aimB_rifle_03", prompt="A person raises a rifle to their shoulder and aims at a target.", seed=73, cut="range", t0=0.0, t1=1.6, mirror=True, root="fixed", kind="oneshot", group="aim"),
    # deaths: the whole fall, the last frame is held by the game; the locator carries the body's travel (the game discards it: the body falls where it stood)
    "kd_death_a": dict(src="death_back_00", prompt="A person is struck and falls backward onto the ground and lies still.", seed=31, cut="range", t0=0.0, t1=3.4, root="path", kind="oneshot", group="death"),
    "kd_death_b": dict(src="death_back_02", prompt="A person is struck and falls backward onto the ground and lies still.", seed=31, cut="range", t0=0.0, t1=3.4, root="path", kind="oneshot", group="death"),
    "kd_death_c": dict(src="death_side_00", prompt="A person staggers, collapses and drops to the ground, lying motionless.", seed=33, cut="range", t0=0.0, t1=3.4, root="path", kind="oneshot", group="death"),
    # turning on the spot (a villager faces the player, a bandit turns to the archer's target): the locator carries the heading (end_yaw_deg in the manifest: positive = left), cut from the first to the last
    # frame the body really turns, plus a beat of lead-in and a settle at the end
    "kd_turn_left": dict(src="turn_left_02", prompt="A person stands and turns on the spot to the left.", seed=61, cut="turn", root="path", yaw="follow", kind="oneshot", group="turn"),
    "kd_turn_right": dict(src="turn_right_02", prompt="A person stands and turns on the spot to the right.", seed=62, cut="turn", root="path", yaw="follow", kind="oneshot", group="turn"),
    "kd_turn_180_left": dict(src="turn180_00", prompt="A person stands and turns around on the spot to face the opposite direction.", seed=81, cut="turn", root="path", yaw="follow", kind="oneshot", group="turn"),
    "kd_turn_180_right": dict(src="turn180_01", prompt="A person stands and turns around on the spot to face the opposite direction.", seed=81, cut="turn", root="path", yaw="follow", kind="oneshot", group="turn"),
    # sitting
    "kd_sit_down": dict(src="sit_down_00", prompt="A person sits down on a chair.", seed=41, cut="range", t0=0.0, t1=2.9, root="path", kind="oneshot", group="sit"),
    "kd_sit_idle": dict(src="sit_idle_00", prompt="A person sits on a chair, relaxed, looking around.", seed=42, cut="loop", kind="loop", group="sit"),
    "kd_stand_up": dict(src="sit_up_00", prompt="A person stands up from a chair.", seed=43, cut="range", t0=0.0, t1=2.9, root="path", kind="oneshot", group="sit"),
    # ---- villager work, daily life, fear and combat reactions, talk and idle variety. Loops are in place; one-shots keep the locator fixed unless noted.
    "kd_work_chop": dict(src="w_chop_02", prompt="A person chops wood with an axe, swinging it overhead and down onto a log, again and again.", seed=101, cut="loop", min_s=1.8, max_s=3.0, kind="loop", group="work", note="no axe in the hands: the prop is the game's"),
    "kd_work_hammer": dict(src="w_hammer_02", prompt="A person hammers on an anvil, swinging a hammer down hard again and again.", seed=102, cut="loop", min_s=2.0, max_s=4.0, kind="loop", group="work"),
    "kd_work_sweep": dict(src="w_sweep_02", prompt="A person sweeps the floor with a broom, standing in place.", seed=103, cut="loop", min_s=2.0, max_s=4.0, kind="loop", group="work"),
    "kd_work_hoe": dict(src="w_hoe_02", prompt="A person digs the ground with a hoe, bending forward and pulling it back again and again.", seed=104, cut="loop", min_s=2.0, max_s=4.0, kind="loop", group="work"),
    "kd_work_carry_walk": dict(src="w_carry_01", prompt="A person walks forward carrying a heavy basket in both arms.", seed=105, cut="walk", min_s=0.8, max_s=1.6, kind="loop", group="work", checks=dict(feet_on_ground=True)),
    "kd_eat": dict(src="d_eat_01", prompt='A person eats from a bowl, lifting a spoon to their mouth again and again.', seed=111, cut='loop', min_s=2.5, max_s=5.0, kind='loop', group='daily', note='seated: the pelvis is 0.58 m up, a seat anchor is needed'),
    "kd_drink": dict(src="d_drink_00", prompt='A person drinks from a mug, lifting it to their mouth, drinking and lowering it.', seed=112, cut='range', t0=0.0, t1=5.0, root='fixed', kind='oneshot', group='daily'),
    "kd_sleep_loop": dict(src="d_sleep_loop_00", prompt='A person lies on their back asleep, breathing slowly.', seed=114, cut='loop', min_s=2.5, max_s=5.0, kind='loop', group='sleep', note='lying: the pelvis is 0.18 m up'),
    "kd_sleep_up": dict(src="d_sleep_up_02", prompt='A person lying on a bed wakes up, sits up and stands up out of bed.', seed=115, cut='range', t0=1.6, t1=5.0, root='path', kind='oneshot', group='sleep'),
    "kd_pray_in": dict(src="d_pray_in_00", prompt='A person kneels down and clasps their hands in prayer.', seed=116, cut='range', t0=0.0, t1=3.6, root='path', kind='oneshot', group='pray'),
    "kd_pray_loop": dict(src="d_pray_loop_01", prompt='A person kneels still with clasped hands, head bowed in prayer.', seed=117, cut='loop', min_s=2.5, max_s=5.0, kind='loop', group='pray'),
    "kd_pray_out": dict(src="d_pray_out_00", prompt='A person finishes praying and stands up from kneeling.', seed=118, cut='range', t0=1.0, t1=4.0, root='path', kind='oneshot', group='pray'),
    "kd_bow": dict(src="d_bow_00", prompt='A person bows politely at the waist and straightens up.', seed=119, cut='range', t0=0.0, t1=3.5, root='fixed', kind='oneshot', group='greet'),
    "kd_wave_greet": dict(src="d_wave_00", prompt='A person waves hello with their right hand.', seed=120, cut='range', t0=0.0, t1=3.5, root='fixed', kind='oneshot', group='greet'),
    "kd_cower": dict(src="f_cower_02", prompt='A person cowers in fear, crouching with both hands over their head.', seed=121, cut='loop', t0=1.5, t1=6.0, min_s=2.0, max_s=3.5, kind='loop', group='react'),
    "kd_flee_run": dict(src="f_flee_00", prompt='A person runs away in fear, glancing back over their shoulder.', seed=122, cut='walk', min_s=0.4, max_s=1.0, skip_s=2.8, tail_s=0.8, kind='loop', group='flee'),
    "kd_block": dict(src="f_block_02", prompt='A person raises a club in front of them with both hands to block an attack.', seed=123, cut='range', t0=0.0, t1=3.0, root='fixed', kind='oneshot', group='react'),
    "kd_hit_heavy_front": dict(src="f_hitfront_03", prompt='A person is hit hard in the stomach, doubles over and staggers back.', seed=126, cut='range', t0=0.0, t1=2.6, root='compress', kind='oneshot', group='react'),
    "kd_hit_heavy_back": dict(src="f_hitback_00", prompt='A person is struck hard from behind, lurches forward and stumbles.', seed=127, cut='range', t0=0.0, t1=2.2, root='compress', kind='oneshot', group='react'),
    "kd_get_up": dict(src="f_getup_02", prompt='A person lying on the ground gets up onto their feet.', seed=129, cut='range', t0=1.2, t1=4.0, root='path', kind='oneshot', group='fall'),
    "kd_talk_point": dict(src="t_point_00", prompt='A person talks and points at something in the distance with their arm.', seed=141, cut='range', t0=0.0, t1=4.2, root='fixed', kind='oneshot', group='talk'),
    "kd_talk_plead": dict(src="t_plead_03", prompt='A person pleads, holding out both open hands in appeal.', seed=142, cut='range', t0=0.0, t1=4.4, root='fixed', kind='oneshot', group='talk'),
    "kd_talk_laugh": dict(src="t_laugh_03", prompt='A person laughs, throwing back their head and holding their belly.', seed=143, cut='range', t0=0.0, t1=5.0, root='fixed', kind='oneshot', group='talk'),
    "kd_talk_shake_head": dict(src="t_shake_02", prompt='A person shakes their head in disagreement while talking.', seed=145, cut='range', t0=0.0, t1=4.0, root='fixed', kind='oneshot', group='talk'),
    "kd_idle_tired": dict(src="i_tired_02", prompt='A person stands tired with slumped shoulders, rubbing their eyes and sighing.', seed=151, cut='loop', min_s=3.5, max_s=6.5, kind='loop', group='idle'),
    "kd_idle_nervous": dict(src="i_nervous_02", prompt='A person stands nervously, fidgeting with their hands and glancing around.', seed=152, cut='loop', min_s=3.5, max_s=6.5, kind='loop', group='idle'),
    "kd_idle_arms_folded": dict(src="i_folded_03", prompt='A person stands with arms folded, shifting their weight now and then.', seed=153, cut='loop', min_s=3.5, max_s=6.5, kind='loop', group='idle', note='almost still: breathing and a few degrees of head motion only'),
    "kd_idle_look_around": dict(src="i_look_01", prompt='A person stands and looks around slowly, turning their head left and right.', seed=154, cut='loop', min_s=3.5, max_s=6.5, kind='loop', group='idle'),
    "kd_sleep_down": dict(src="s_down_02", prompt='A person stands still. A person lies down on the ground on their back and lies still.', seed=201, cut='range', t0=3.0, t1=5.4, root='path', kind='oneshot', group='sleep', note='two-sentence prompt, 3 s each; the cut is the second sentence; the lowering takes about 1 s'),
    "kd_knockdown": dict(src="k_down_02", prompt='A person stands ready. A person is hit hard, knocked off their feet and falls onto their back.', seed=203, cut='range', t0=2.3, t1=5.0, root='path', kind='oneshot', group='fall', note='two-sentence prompt'),
    "kd_dodge_side": dict(src="k_dodge2_00", prompt='A person leaps sideways to the left, landing on both feet.', seed=205, cut='range', t0=0.0, t1=1.9, root='path', kind='oneshot', group='react', note='1.1 m to the LEFT in the locator'),
    "kd_dodge_side_r": dict(src="k_dodge2_00", prompt='A person leaps sideways to the left, landing on both feet.', seed=205, cut='range', t0=0.0, t1=1.9, root='path', mirror=True, kind='oneshot', group='react', note='the mirror of kd_dodge_side: 1.1 m to the RIGHT'),
    "kd_parry_recoil": dict(src="k_parry_00", prompt="A person's club is knocked aside by a heavy blow and the person staggers backward off balance.", seed=206, cut='range', t0=0.0, t1=2.6, root='compress', max_excursion=0.35, kind='oneshot', group='react'),
    "kd_talk_nod": dict(src="t_nod_01", prompt='A person nods their head in agreement while talking.', seed=144, cut='range', t0=0.0, t1=4.0, root='fixed', kind='oneshot', group='talk'),
    "kd_wounded_crawl": dict(src="k_crawl2_02", prompt='A person crawls forward on their hands and knees.', seed=208, cut='walk', min_s=0.8, max_s=2.4, skip_s=1.5, tail_s=0.5, kind='loop', group='fall', note='hands and knees, about 0.55 m/s'),
}


class _Walk:
    """The retargeted Kimodo result in the shape motionbricks_loop.find_cycle / build_loop take (they were written for the MotionBricks walks)."""

    def __init__(self, r, human):
        self.local_q = np.asarray(r["local_q"])
        self.world_p = np.asarray(r["world_p"], dtype=np.float64)
        self.fps = float(r["fps"])
        self.human = human
        self.ix = human.ix
        self.names = human.names
        self._wq = np.asarray(r["world_q"])

    def world_q(self):
        return self._wq


def load(src, mirror=False):
    human = MR.Human(os.path.join(SRC, "sdk_player_bindpose.json"))
    soma = KR.Soma(os.path.join(SRC, "soma77_skeleton.json"))
    path = os.path.join(SRC, "kimodo", src + ".npz")
    if not os.path.isfile(path):                      # a candidate not chosen yet (choose = copy it into anim_sources/kimodo)
        path = os.path.join(KIMODO_OUT, src + ".npz")
    posed = np.load(path)["posed_joints"]
    if mirror:
        posed = AO.mirror_soma(posed, soma)
    return human, KR.retarget(posed, human, soma)


def right_hand_peak(r, human, after=0.0):
    """The frame the right hand moves fastest, searching from `after` seconds on (an overhead strike raises the club fast first: its blow is the later, downward peak)."""
    hand = np.asarray(r["world_p"], dtype=np.float64)[:, human.ix["Bip01 R Hand"]]
    sp = np.linalg.norm(np.diff(hand, axis=0), axis=1) * r["fps"]
    smooth = AO.lowpass(sp[:, None], 1)[:, 0]
    k0 = int(round(after * r["fps"]))
    k = k0 + int(np.argmax(smooth[k0:])) + 1
    return k, float(sp[k0:].max())


def cut(spec):
    """-> (clip dict, human, manifest info, world positions (F, J, 3) of the frames the clip shows: for a contact sheet)."""
    human, r = load(spec["src"], spec.get("mirror", False))
    fps = r["fps"]
    info = {"style": spec["group"], "seed": spec["seed"], "kind": spec["kind"], "source": "kimodo/%s.npz" % spec["src"], "model": MODEL, "prompt": spec["prompt"], "group": spec["group"]}
    if spec["cut"] == "peak":
        k, vmax = right_hand_peak(r, human, spec.get("peak_after", 0.0))
        pre, post = int(round(spec["pre"] * fps)), int(round(spec["post"] * fps))
        w = AO.slice_pad(r, k - pre, k + post)
        clip = AO.make_clip(w, human, spec["root"], spec.get("yaw", "fixed"), max_excursion=spec.get("max_excursion", 0.3))
        info.update({"hit_time": round(pre / fps, 3), "hand_peak_m_s": round(vmax, 2)})
        shown = w["world_p"]
    elif spec["cut"] == "range":
        w = AO.slice_pad(r, int(round(spec["t0"] * fps)), int(round(spec["t1"] * fps)))
        clip = AO.make_clip(w, human, spec["root"], spec.get("yaw", "fixed"), max_excursion=spec.get("max_excursion", 0.3))
        shown = w["world_p"]
    elif spec["cut"] == "turn":
        psi = AO.pelvis_yaw(r, human)
        d = np.abs(psi - psi[0])
        total = float(psi[-1] - psi[0])
        a = int(np.argmax(d > np.radians(3.0)))                                  # the body starts turning
        b = len(psi) - 1 - int(np.argmax(np.abs(psi[::-1] - psi[-1]) > np.radians(3.0)))   # ... and has stopped
        w = AO.slice_pad(r, a - int(round(0.15 * fps)), b + int(round(0.4 * fps)))
        clip = AO.make_clip(w, human, spec["root"], spec.get("yaw", "fixed"), max_excursion=spec.get("max_excursion", 0.3))
        info.update({"turn_deg": round(float(np.degrees(total)), 1), "cut_frames": [a, b]})
        shown = w["world_p"]
    elif spec["cut"] == "loop":
        w = AO.slice_pad(r, int(round(spec.get("t0", 0.0) * fps)), int(round(spec["t1"] * fps))) if "t1" in spec else r
        i0, n, d = AO.best_inplace_loop(w, human, min_s=spec.get("min_s", 2.5), max_s=spec.get("max_s", 4.5), skip_s=spec.get("skip_s", 0.3), tail_s=spec.get("tail_s", 0.3))
        i0 = max(i0, 8)                                                  # make_inplace_loop crossfades into the 8 frames BEFORE i0
        clip = AO.make_inplace_loop(w, human, i0, n)
        info.update({"cycle_from_frame": i0, "feature_distance": round(d, 4)})
        shown = np.asarray(w["world_p"])[i0:i0 + n + 1]
    elif spec["cut"] == "walk":
        # a walking or running cycle: the locator travels along +Y at the cycle's own speed (the engine moves the body by it), the body is the cycle (motionbricks_loop, as for the MotionBricks gaits)
        rt = _Walk(r, human)
        i0, n, d, gmin, _ = ML.find_cycle(rt, spec.get("min_s", 0.6), spec.get("max_s", 2.4), spec.get("skip_s", 0.8), spec.get("tail_s", 0.5))
        clip = ML.build_loop(rt, i0, n)
        info.update({"cycle_from_frame": i0, "feature_distance": round(d, 4), "facing_vs_travel_deg": round(clip["facing_vs_travel_deg"], 1)})
        shown = np.asarray(r["world_p"])[i0:i0 + n + 1]
    else:
        raise ValueError(spec["cut"])
    pxy = np.asarray(clip["pelvis_rel"])[:, :2]
    info["pelvis_excursion_m"] = round(float(np.max(np.linalg.norm(pxy - pxy[0], axis=1))), 3)      # how far the body strays from the locator (an entity that does not move: how far from its collider)
    pel = np.asarray(clip["pelvis_rel"])[:, 2] + 0.0
    info["pelvis_height"] = [round(float(pel[0]), 3), round(float(pel.min()), 3), round(float(pel[-1]), 3)]
    info["checks"] = {"loop": spec["kind"] == "loop", "feet_on_ground": spec["group"] in STANDING}
    info["checks"].update(spec.get("checks", {}))
    if "note" in spec:
        info["note"] = spec["note"]
    return clip, human, info, shown


def build(name, spec, tpl, out_dir, compile_caf=True):
    clip, human, info, _ = cut(spec)
    exp, caf, size = MC.finish(name, clip, human, tpl, out_dir, WORK, info, compile_caf)
    return exp, caf, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--no-rc", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    tpl = MF.Template(MC.TEMPLATE)
    for name in (a.names or list(CLIPS)):
        exp, caf, size = build(name, CLIPS[name], tpl, a.out, not a.no_rc)
        print("%-12s %3d frames %.2f s  FK check max %.4f m  root travel %s -> %s" % (name, exp["frames"], exp["duration"], exp["fk_check_max_m"],
                                                                                   [round(float(x), 2) for x in (exp["expected"][-1]["joints"]["Bip01 Pelvis"][:2])], caf or "(no rc)"))


if __name__ == "__main__":
    main()
