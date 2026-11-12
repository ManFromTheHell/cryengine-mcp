#!/usr/bin/env python3
"""Play a packaged Ashvale build through everything that draws, to find (or record) the shader
combinations its global shader cache lacks.

The manual's measure ("Generating Shader Combinations"): every combination missing from the global
cache is a GCM -- compiled at run time, a hitch -- and a non-Release build appends each one to
<user>/Shaders/ShaderCacheMisses.txt; "ideally empty". A miss is only logged when the combination is
in NEITHER the shipped cache NOR the build's user cache (D3DHWShaderCompiling.cpp:3578-3649), so this
script starts from an empty <build>/user/shaders (the old one is moved aside, never deleted).

    shader_tour.py <build> [--rsc] [--quick] [--only-undercroft] [--label name]

--rsc   also send every miss to the Remote Shader Compiler on 127.0.0.1 (the manual's recording step;
        start the engine's CrySCompileServer.exe first). Profile builds only:
        Release drops non-whitelisted +cvar arguments and never compiles remotely.
--quick the boot + one walk only (checks the script, not the cache)
--only-undercroft  the boot, a new game and chapter II's leg only (the hatch, the dungeon's 20 viewpoints, the vault, the way back): a few minutes

Route: main menu, New Game, every villager's dialogue, every building inside and out, the bridge,
fields, fires, the bandit camp and the fight (death screen + respawn included), the time of day
round the clock at three places, rain, a cutscene, the pause menu, save and load, the ending, and
back to the menu. After each leg it logs ash_shader_stats (GCM so far) when the build has it.
Sandbox is closed for the run (the game bridge is port 37777) and the watchdog held off.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

BUILD = os.path.abspath(sys.argv[1])
LABEL = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else os.path.basename(BUILD)
RSC = "--rsc" in sys.argv
QUICK = "--quick" in sys.argv
ONLY_UC = "--only-undercroft" in sys.argv
HERE = _paths.ROOT
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                if os.path.isfile(p))
RELEASE = "win_x64_release" in LAUNCHER
LOG = os.environ.get("CRYENGINE_NPC_LOG") or _paths.workspace("claude_npc.log")      # the game's trace log (workspace folder, state/ by default)
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")      # the watchdog's deploy lock
PORT = 37777
USER_SHADERS = os.path.join(BUILD, "user", "shaders")
MISSES = os.path.join(USER_SHADERS, "shadercachemisses.txt")


def send(cmd, timeout=30.0):
    with socket.create_connection(("127.0.0.1", PORT), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def c(cmd, wait=0.5):
    try:
        send(cmd)
    except Exception as e:
        print("   !", cmd, e, flush=True)
    time.sleep(wait)


def log_size():
    for _ in range(20):
        try:
            return os.path.getsize(LOG)
        except (PermissionError, FileNotFoundError):
            time.sleep(0.05)
    return 0


def log_since(off):
    for _ in range(20):
        try:
            with open(LOG, "rb") as f:
                f.seek(off)
                return f.read().decode("utf-8", "replace")
        except PermissionError:
            time.sleep(0.05)
    return ""


def status():
    off = log_size()
    c("ash_status", 0.6)
    lines = [l for l in log_since(off).splitlines() if "status:" in l]
    return lines[-1] if lines else ""


def miss_lines():
    try:
        with open(MISSES, encoding="utf-8", errors="replace") as f:
            return sum(1 for l in f if l.strip())
    except OSError:
        return 0


legs = []


def checkpoint(name):
    off = log_size()
    c("ash_shader_stats", 0.6)
    m = re.search(r"shader cache: global misses (\d+), level misses (\d+), async compiles (\d+), compiling (\w+)", log_since(off))
    gcm = int(m.group(1)) if m else None
    legs.append((name, gcm, miss_lines()))
    print("LEG|%-22s|GCM %s|miss-file lines %d" % (name, gcm if gcm is not None else "n/a", miss_lines()), flush=True)


def visit(entity, dist=3.0, dwell=2.5):
    c("ash_goto %s %.1f" % (entity, dist), dwell)


def talk(npc, choices):
    c("ash_goto %s 1.6" % npc, 0.6)
    c("ash_use", 1.0)
    for n in choices:
        c("ash_choose %d" % n, 0.8)


def clock(hours=(6, 9.5, 12, 17, 19, 20.5, 22, 0, 3, 5)):
    for h in hours:
        c("claude_tod %s" % h, 2.2)


def underground():
    off = log_size()
    c("ash_status", 0.7)
    return "underground 1" in log_since(off)


def undercroft_leg():
    """Chapter II: the cellar hatch seen close, the trip down, the 20 standard viewpoints of the dungeon, the vault door and the strongbox, the stair back. The Undercroft's
    materials, the hatch's, the lit lamps, the vault door's lift and the loading page between levels draw combinations the first chapter never did."""
    import math
    sys.path.insert(0, HERE)
    import undercroft_kit as K
    from uc_shots import VIEWS
    stand = json.load(open(os.path.join(HERE, "uc_stand.json"), encoding="utf-8"))

    def at(name, wait=1.2):
        s = stand[name]
        c("ash_goto_pos %g %g %g %g" % (s["x"], s["y"], s["z"], s["yaw"]), wait)

    def wait_level(down, seconds=90):
        t = time.time()
        while time.time() - t < seconds:
            time.sleep(2.0)
            if underground() == down:
                break
        time.sleep(2.0)

    c("ash_flag wat_dead 1", 0.5)
    for x, y, yaw in ((513.5, 777.0, 160), (509.2, 777.0, -160), (511.8, 775.5, 180)):          # the kerb, the lid and the dark pit from three sides
        c("ash_goto_pos %g %g 88.0 %d" % (x, y, yaw), 1.8)
    c("ash_goto TowerHatch 1.4", 1.0)
    c("ash_use", 0.5)
    wait_level(True)
    checkpoint("down the hatch")
    for name, (eye, tgt) in VIEWS.items():
        x, y = eye[0], eye[1]
        z = K.floor_z(x, y)
        yaw = math.degrees(math.atan2(-(tgt[0] - x), tgt[1] - y))
        c("ash_goto_pos %g %g %g %g" % (K.O[0] + x, K.O[1] + y, K.O[2] + z, yaw), 1.6)
    checkpoint("the Undercroft")
    c("ash_flag vault_key 1", 0.3)
    at("VaultDoor", 1.0)
    c("ash_use", 6.0)                                           # the slab lifts
    c("ash_goto LordsStrongbox 1.3", 1.0)
    c("ash_use", 1.5)
    checkpoint("the vault")
    at("UndercroftExit", 1.0)
    c("ash_use", 0.5)
    wait_level(False)
    checkpoint("the way back")


# ---------------------------------------------------------------- hold the watchdog, close Sandbox
stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        with open(LOCK, "w") as f:
            f.write("shader_tour %s\n" % LABEL)
        stop.wait(60)


threading.Thread(target=keep_lock, daemon=True).start()
for _image in ("Sandbox.exe", "GameLauncher.exe"):        # never kill a program by image name: it may be the editor with unsaved work or another CRYENGINE game
    if _image in subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + _image, "/NH"], capture_output=True, text=True).stdout:
        stop.set()
        raise SystemExit("%s is running: close it first (the job layer does that; this script does not kill programs it did not start)" % _image)
if os.path.isdir(USER_SHADERS):
    aside = USER_SHADERS + "_before_tour_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.move(USER_SHADERS, aside)
    print("moved the old user shader cache aside:", aside, flush=True)
# NO display keep-awake request here (it used to call SetThreadExecutionState): the display's idle timer is the
# machine owner's to set. A tour of the whole route is long enough to meet it; `--only-undercroft` is a few minutes.
args = [LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"]
if RSC:
    args += ["+r_ShadersRemoteCompiler", "1", "+r_ShaderCompilerServer", "127.0.0.1"]
class _EarlyEnd(Exception):
    pass


launcher = subprocess.Popen(args, cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
t0 = time.time()
try:
    ready = False
    for _ in range(90):
        time.sleep(3)
        try:
            off = log_size()
            send("ash_status", 5)
            time.sleep(0.5)
            if "status: ticks" in log_since(off):
                ready = True
                break
        except Exception:
            pass
    print("booted: %s (%s, %s, pid %d, %.0f s)" % (ready, "Release" if RELEASE else "Profile",
                                                   "RSC recording" if RSC else "no RSC", launcher.pid, time.time() - t0), flush=True)
    if not ready:
        raise SystemExit(1)
    c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
    c("ash_skip_menu 0", 0.1)
    c("ash_mainmenu", 4.0)
    checkpoint("boot + main menu")

    c("ash_newgame", 1.0)
    for _ in range(40):                  # the intro is a Track View flight; wait for play to begin
        if "phase Playing" in status():
            break
        time.sleep(0.5)
    c("ash_hold w 3", 3.5)
    checkpoint("new game + intro")
    if QUICK:
        raise _EarlyEnd()
    if ONLY_UC:                          # a refresh that keeps the recorded list and adds only chapter II's combinations (shader_cache_refresh.py --append --only-undercroft)
        undercroft_leg()
        raise _EarlyEnd()

    for npc, choices in (("Hilda", [1, 1, 1]), ("Oswin", [1, 1]), ("Tom", [1, 1, 1]), ("Garrick", [1]),
                         ("Martha", [1, 1]), ("Edmund", [1, 1])):
        talk(npc, choices)
    checkpoint("dialogues")

    # ash_goto only finds ENTITIES (34 in the level); walls, floors, the bridge, fences and road pieces are
    # brushes, so those places are stood on by position (from main.lyr) and looked round in four directions.
    for e, d in (("AshvaleStart", 4), ("AshfordVillage", 12), ("AshfordWoodpile", 3), ("Firewood0", 2),
                 ("GreenFire", 3), ("ClearingFire", 4), ("TomChest", 1.5), ("ReeveChest", 1.5),
                 ("Tavern_Door", 1.0), ("Chapel_Door", 1.0), ("CottageA_Door", 1.0), ("CottageB_Door", 1.0),
                 ("CottageC_Door", 1.0), ("Reeve_Door", 1.0), ("TomCabin_Door", 1.0)):
        visit(e, d)
    for name, x, y, z in (("Tavern", 524, 374, 36.2), ("Chapel", 500, 398, 36.2), ("CottageA", 476, 346, 36.2),
                          ("CottageB", 486, 330, 36.2), ("CottageC", 532, 396, 36.2), ("Reeve", 478, 372, 36.2),
                          ("Smithy", 522, 344, 36.2), ("Barn", 457, 392, 36.2), ("TomCabin", 493, 600, 44.2),
                          ("Well", 500, 352, 36.6), ("Green", 495.5, 364, 36.6), ("Bridge", 512, 471, 27.0),
                          ("Fields", 464, 310, 36.6), ("Shrine", 514.5, 166, 31.8), ("RoadS", 511.9, 152.5, 31.6),
                          ("Road025", 506.7, 277.5, 36.7), ("Road050", 508.4, 427.5, 36.6), ("Road075", 503.4, 607.5, 44.6),
                          ("Road100", 517.4, 734.4, 78.0), ("Road102", 516.3, 745.3, 87.7), ("Camp", 521, 772, 89.8)):
        for yaw in (0, 90, 180, 270):
            c("ash_goto_pos %s %s %s %d" % (x, y, z, yaw), 1.3)
    checkpoint("village + roads")

    c("ash_goto AshfordVillage 25", 1.0)
    clock()
    c("ash_goto_pos 512 471 27.0 0", 1.0)
    clock((22, 0, 6, 12))
    c("ash_goto ClearingFire 5", 1.0)
    clock((20, 23, 2, 9.5))
    # The night lamps and the carried torch: wall torches, lanterns, and torch.cgf in the hand close
    # to the camera. Without this stop the tour never saw them and the cache missed two Illum combinations of
    # torch.cgf.
    c("claude_tod 21", 1.0)
    c("ash_torch 1", 0.5)
    for pos in ("484.9 336.9 36.3 180", "514.5 372.9 36.3 -90", "500 352.5 36.4 0"):
        c("ash_goto_pos %s" % pos, 3.0)
    c("ash_torch 0", 0.5)
    c("claude_tod 9.5", 1.0)
    checkpoint("time of day")

    c("ash_goto AshfordVillage 15", 1.0)
    c("claude_rain 1", 5.0)
    c("ash_goto_pos 512 471 27.0 180", 3.0)
    c("claude_tod 21", 3.0)
    c("ash_goto_pos 500 398 36.2 0", 2.5)
    c("claude_rain 0", 1.0)
    c("claude_tod 9.5", 1.0)
    checkpoint("rain")

    c("claude_cutscene Hilda 6", 7.5)
    c("claude_cutscene BanditFire 4", 5.0)
    checkpoint("cutscenes")

    # The menus are FlashUI: Scaleform's shaders (glyphs, gouraud, cxform textures, the blur of a
    # question dialog) are in no stock cache, and a package cannot compile them at run time, so the tour must open
    # every kind of page: buttons, tiles, sliders and a switch, a table, a list, an image, and the overwrite question.
    c("ash_pause", 2.5)
    for page in ("controls", "settings", "load", "save", "journal", "map", "credits"):
        c("ash_menu %s" % page, 2.0)
        c("ash_menu back", 1.0)
    c("ash_menu save", 1.5)
    c("ash_menu save:slot1", 1.5)
    c("ash_menu save:slot1", 2.5)      # an occupied slot: the question dialog
    c("ash_menu back", 1.5)
    c("ash_pause", 1.5)
    checkpoint("menu pages")

    # The Quality option (Settings) moves sys_spec live; each level brings shader combinations the default one never
    # draws (shadow and effect variants). Visit low and high with the village, the forest and rain, then back to the
    # project's medium: a package cannot compile a combination the cache lacks.
    for quality in (1, 3, 2):
        c("ash_quality %d" % quality, 5.0)
        c("ash_goto_pos 500 398 36.2 0", 2.5)
        c("ash_goto_pos 503 605 44.6 0", 2.5)
        c("claude_rain 1", 4.0)
        c("claude_tod 21", 2.5)
        c("claude_tod 9.5", 1.5)
        c("claude_rain 0", 1.5)
    checkpoint("quality levels")
    c("ash_pause", 2.0)
    c("ash_pause", 1.0)
    c("ash_savegame shader_tour", 2.0)
    c("ash_goto_pos 512 471 27.0 0", 1.0)
    c("ash_loadgame shader_tour", 6.0)
    checkpoint("pause + save/load")

    undercroft_leg()

    # The camp and the fight. Stand among them first without swinging, so the death screen shows.
    for e in ("CampFire", "CampChest", "BanditFire", "BanditYard"):
        visit(e, 4, 2.0)
    c("ash_goto BanditGateW 2", 0.5)
    died = False
    for _ in range(40):
        time.sleep(0.5)
        if "phase Dead" in status():
            died = True
            break
    if died:
        time.sleep(3.0)
        c("ash_respawn", 3.0)
    print("   death screen reached: %s" % died, flush=True)
    fight_start = log_size()
    for bandit in ("BanditGateW", "BanditGateE", "BanditFire", "BanditYard", "BlackWat"):
        for _ in range(16):
            if "phase Dead" in status():
                c("ash_respawn", 1.5)
            c("ash_goto %s 1.5" % bandit, 0.1)
            c("ash_attack", 0.8)
            if re.search(r"%s: DIED" % bandit, log_since(fight_start)):
                break
    checkpoint("camp + fight")

    c("ash_goto TitheChest 1.3", 0.5)
    c("ash_use", 1.0)
    talk("Hilda", [1, 1])
    time.sleep(6)
    st = status()
    print("   ending reached: %s" % ("phase Ending" in st), flush=True)
    # The project's own shaders (AshFlat, AshIllum, ReferenceImage) are only on test materials today;
    # draw each once, day and night, so the cache carries them.
    c("ash_goto_pos 495.5 364 36.6 0", 1.0)
    for material in ("materials/ashvale/ash_refmagenta", "materials/ashvale/ash_flat", "materials/ashvale/ash_illum"):
        c("ash_shader_probe %s 3" % material, 3.0)
        c("claude_tod 22", 2.0)
        c("claude_tod 9.5", 2.0)
    c("ash_shader_probe off", 0.5)
    checkpoint("project shaders")
    c("ash_mainmenu", 4.0)
    checkpoint("ending + main menu")
except _EarlyEnd:                        # --quick / --only-undercroft: leave the route here, but still print the summary below
    pass
finally:
    try:
        send("quit", 5)
    except Exception:
        pass
    time.sleep(4)
    if launcher.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(launcher.pid)], capture_output=True)       # only the launcher this run started
    stop.set()
    try:
        os.remove(LOCK)
    except OSError:
        pass

print("RESULT %s: %s, %.0f s, GCM by leg %s, miss-file lines %d" % (
    LABEL, "Release" if RELEASE else "Profile", time.time() - t0,
    " ".join("%s=%s" % (n.split()[0], g) for n, g, _ in legs), miss_lines()))
# The pass/fail the suite needs (the counts above alone would end 'ok' whatever they were): every leg ran, and the
# route compiled nothing at run time. --rsc / --quick runs are recording, not checking.
if "--rsc" not in sys.argv and not QUICK and not ONLY_UC:
    _total = sum(g or 0 for _, g, _ in legs if isinstance(g, int))
    _unreadable = [n for n, g, _ in legs if not isinstance(g, int)]
    _checks = [("every leg of the route reported its shader-cache count", len(legs) >= 12 and not _unreadable, "%d legs, unreadable: %s" % (len(legs), _unreadable or "none")),
               ("no shader combination was compiled at run time (GCM)", _total == 0, "%d in total" % _total),
               ("the miss file is empty", miss_lines() == 0, "%d lines" % miss_lines())]
    for _n, _ok, _d in _checks:
        print("CHECK|%s|%s|%s" % ("PASS" if _ok else "FAIL", _n, _d))
    print("RESULT shader_tour_check: %d of %d" % (sum(1 for _, ok, _ in _checks if ok), len(_checks)))
if os.path.isfile(MISSES):
    groups = {}
    for line in open(MISSES, encoding="utf-8", errors="replace"):
        m = re.match(r"\[[^\]]*\]<\d+>(\w+)@(\w+)", line)
        if m:
            groups[m.group(1)] = groups.get(m.group(1), 0) + 1
    print("misses by shader:", ", ".join("%s %d" % kv for kv in sorted(groups.items(), key=lambda kv: -kv[1])))
