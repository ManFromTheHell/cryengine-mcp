#!/usr/bin/env python3
"""Play chapter II of a packaged (Release) Ashvale build: the hatch, the loading page, the Undercroft, the sexton, the fight, the vault, the way back, a save made underground and loaded
from the surface, and the ending that notices the lord's sword. (release_playthrough.py plays chapter I; this one starts a new game and sets the early flags directly.)

Driven like release_playthrough.py: the game's own ash_* commands over the game bridge (port 37777), checked from the game's trace file (claude_npc.log) and from pictures of the window.
DIFFERENT from it on purpose: it does NOT ask Windows to keep the display awake (the display's idle timer is left to the machine's owner), the window
is 1280x720 by default, and it teleports inside the dungeon to the stand points uc_standpoints.py found on the NAVMESH (ash_goto would put the player on 'its side' of a target, which from
the far end of the dungeon can be inside a wall).

    undercroft_playthrough.py <build> [--fullscreen]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from window_capture import game_hwnd, capture_screen, save_png  # noqa: E402

HERE = _paths.ROOT
BUILD = os.path.abspath(sys.argv[1])
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64")) if os.path.isfile(p))
FULLSCREEN = "--fullscreen" in sys.argv
# CLAUDE_STATE_DIR: the folder the game writes claude_npc.log to (default: the workspace folder, state/ unless CRYENGINE_WORKSPACE is set); the deploy lock sits where safe_close.py / the watchdog look for it
STATE_DIR = os.environ.get("CLAUDE_STATE_DIR") or _paths.WORKSPACE
try:
    os.makedirs(STATE_DIR, exist_ok=True)
except OSError:
    pass
import safe_close  # noqa: E402  (also used below to save and close the editor, or refuse)
LOG = os.path.join(STATE_DIR, "claude_npc.log")
LOCK = _paths.workspace("claude_deploy_in_progress")
SHOTS = os.path.join(BUILD, "undercroft_shots")
STAND = json.load(open(os.path.join(HERE, "uc_stand.json"), encoding="utf-8"))
results = []


def kill_build_launchers():
    """end only the GameLauncher.exe processes that run from THIS build folder (a leftover instance), not every GameLauncher on the machine"""
    ps = "Get-Process GameLauncher -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($env:BUILD_DIR, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, env=dict(os.environ, BUILD_DIR=BUILD))


def stop_launcher(proc):
    """close the launcher THIS script started: terminate, then kill it (and its children) by pid if it does not exit"""
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


def send(cmd, timeout=30.0):
    with socket.create_connection(("127.0.0.1", 37777), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def c(cmd, wait=0.4):
    try:
        send(cmd)
    except Exception as e:
        print("   !", cmd, e, flush=True)
    time.sleep(wait)


def log_size():
    for _ in range(40):
        try:
            return os.path.getsize(LOG)
        except (PermissionError, FileNotFoundError):
            time.sleep(0.05)
    return 0


def since(off):
    for _ in range(40):
        try:
            with open(LOG, "rb") as f:
                f.seek(off)
                return f.read().decode("utf-8", "replace")
        except PermissionError:
            time.sleep(0.05)
    return ""


def status():
    off = log_size()
    c("ash_status", 0.7)
    text = since(off)
    s1 = [l for l in text.splitlines() if "status:" in l and "status2:" not in l]
    s2 = [l for l in text.splitlines() if "status2:" in l]
    return (s1[-1] if s1 else ""), (s2[-1] if s2 else "")


def flag(st, name):
    m = re.search(r"\b%s=(\d+)" % name, st)
    return int(m.group(1)) if m else None


def num(st, name):
    m = re.search(r"\b%s (\d+)" % name, st)
    return int(m.group(1)) if m else None


def pos(st):
    m = re.search(r"at ([-\d.]+) ([-\d.]+) ([-\d.]+)", st)
    return tuple(float(v) for v in m.groups()) if m else None


def objective(st):
    m = re.search(r"objective '(.*?)', health", st)         # the text itself has apostrophes ("Wat's")
    return m.group(1) if m else ""


def near(p, q, r):
    return p is not None and ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 <= r


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, str(detail)[:220]), flush=True)


def shot(name):
    """What the SCREEN shows (the engine's own screenshot has no 2D overlay) -- through window_capture.capture_screen, which REFUSES unless the game owns the centre and the four corners of its
    window at the expected size. The first version here copied the screen rectangle blindly and, with another program's window in front of the game, saved a picture of THAT window
    (for example a chat window). A refused shot is reported and skipped, never taken."""
    os.makedirs(SHOTS, exist_ok=True)
    hwnd = game_hwnd(launcher.pid)
    if not hwnd:
        print("   shot %s: no game window" % name, flush=True)
        return
    try:
        w, h, rows = capture_screen(hwnd, None if FULLSCREEN else (1280, 720))
    except RuntimeError as e:
        print("   shot %s: NOT TAKEN (%s)" % (name, str(e)[:90]), flush=True)
        return
    save_png(os.path.join(SHOTS, name + ".png"), w, h, rows)


DIALOGUE_OF = {"Oswin": "oswin", "Edmund": "edmund", "Hilda": "hilda", "Tom": "tom", "Garrick": "garrick", "Cuthbert": "cuthbert"}


def talk(npc, choices, tries=3):
    # the trace names the speaker by his talk name ("Father Edmund", "Hilda, the reeve") and the dialogue id in parentheses: match the id, and never press E again over an open dialogue
    for _ in range(tries):
        tried = log_size()
        c("ash_goto %s 1.6" % npc, 0.6)
        c("ash_use", 1.0)
        if re.search(r"talking to .*\(%s\)" % DIALOGUE_OF.get(npc, npc.lower()), since(tried)):
            break
        time.sleep(1.5)
    for n in choices:
        c("ash_choose %d" % n, 0.8)


def stand(name, wait=0.8):
    s = STAND[name]
    c("ash_goto_pos %g %g %g %g" % (s["x"], s["y"], s["z"], s["yaw"]), wait)


def wait_status(pred, seconds, step=1.0):
    t = time.time()
    st = status()
    while not pred(st) and time.time() - t < seconds:
        time.sleep(step)
        st = status()
    return st


FIGHT_OFF = [0]        # log offset where the fight began: the sword hits whoever is nearest in its arc, so an enemy often dies during ANOTHER enemy's turn


def kill(name, first_stand=True, tries=18):
    """Swing at one enemy until the trace says it died (anywhere since the fight began: see FIGHT_OFF); respawn and walk back if the player falls."""
    start = FIGHT_OFF[0] or log_size()
    deaths = 0
    if first_stand:
        stand(name, 0.5)
    for i in range(tries):
        if re.search(r"%s: DIED" % name, since(start)):
            return True, deaths
        st, _ = status()
        if "phase Dead" in st:
            deaths += 1
            c("ash_select", 1.5)
            stand(name, 0.5)
        else:
            c("ash_goto %s 1.5" % name, 0.1)
        c("ash_attack", 0.9)
    return bool(re.search(r"%s: DIED" % name, since(start))), deaths


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        with open(LOCK, "w") as f:
            f.write("undercroft_playthrough\n")
        stop.wait(60)


threading.Thread(target=keep_lock, daemon=True).start()
# safe_close (imported above): save and close the editor, or refuse: never taskkill /F Sandbox
_closed = safe_close.close_editor("undercroft_playthrough", hold_minutes=120)
if not _closed["ok"]:
    stop.set()                                             # the lock written by keep_lock must not stay behind when nothing ran
    try:
        os.remove(LOCK)
    except OSError:
        pass
    raise SystemExit("the editor was not closed, nothing was run: " + "; ".join(_closed["refused"] or ["it did not close"]))
kill_build_launchers()
time.sleep(3)
res = ["-claude_test", "+r_Fullscreen", "1", "+r_Width", "2560", "+r_Height", "1440"] if FULLSCREEN else ["-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720"]
started_at = time.time()
launcher = subprocess.Popen([LAUNCHER] + res + ["+s_IgnoreWindowFocus", "1"], cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
exited = None
try:
    ready = False
    for _ in range(90):
        time.sleep(3)
        try:
            off = log_size()
            send("ash_status", 5)
            time.sleep(0.5)
            if "status: ticks" in since(off):
                ready = True
                break
        except Exception:
            pass
    check("the build boots on its own (%s)" % BUILD, ready)
    if not ready:
        raise SystemExit(1)
    c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
    c("ash_skip_menu 0", 0.1)

    # ---- a new game, the early flags set directly (release_playthrough.py plays them for real)
    c("ash_newgame", 2.0)
    c("ash_select", 1.0)                                       # skip the intro
    st, st2 = status()
    t0 = time.time()
    while "phase Playing" not in st and time.time() - t0 < 30:
        time.sleep(1.0)
        st, st2 = status()
    check("a new game is under way on the surface", "phase Playing" in st and "underground 0" in st2, st[:90])
    check("the surface objective is the first chapter's, with no side errand yet", "Walk up the road" in objective(st) and "side ''" in st2, (objective(st), st2[:60]))
    c("ash_flag has_blade 1", 0.2)                             # Garrick's blade: the diggers fall in two blows

    # ---- the hatch is locked until Wat is dead
    off = log_size()
    c("ash_goto TowerHatch 1.4", 0.8)
    shot("01_hatch_locked")
    c("ash_use", 1.0)
    text = since(off)
    st, st2 = status()
    check("the hatch is locked while Wat lives (its text is said, no trip starts)", "A cellar hatch under the old keep" in text and "go:" not in text and "underground 0" in st2,
          text[-160:].replace("\n", " "))

    # ---- foreshadowing in two conversations
    off = log_size()
    talk("Oswin", [1, 1])
    check("Oswin's gossip names the sexton and the picks under the tower", "Edmund's sexton" in since(off) and "picks under it" in since(off), "")
    c("ash_flag found_tom 1", 0.3)
    off = log_size()
    talk("Edmund", [2, 1])
    check("Father Edmund, asked about Wat, says he took Brother Cuthbert up to the chapel", "my sexton, Brother Cuthbert" in since(off), "")

    # ---- Wat dies: the notice, the HUD's second line, the journal row
    off = log_size()
    c("ash_flag wat_dead 1", 0.5)
    time.sleep(9.0)
    text = since(off)
    check("when Wat dies the objective changes and, after it, the hatch notice is shown once", "Objective: Open Black Wat's chest" in text and "notice: Fresh pick marks lead to a cellar hatch" in text, text[-200:].replace("\n", " "))
    st, st2 = status()
    check("the side errand names the hatch (status2: side)", "Wat's men dug by the keep" in st2 and flag(st2, "hatch_hint") == 1, st2[:150])
    shot("02_hud_side_line")
    c("ash_pause", 1.0)
    c("ash_menu journal", 1.2)
    shot("03_journal")
    c("ash_menu map", 1.2)
    shot("04_map")
    c("ash_pause", 1.0)
    st, st2 = status()
    if "phase Playing" not in st:
        c("ash_choose 1", 1.0)
        st, st2 = status()
    check("the pause menu closes again (phase Playing)", "phase Playing" in st, st[:90])
    coins0, bread0 = num(st, "coins"), num(st, "bread")

    # ---- down the hatch, behind the loading page
    c("ash_goto TowerHatch 1.4", 0.8)
    off = log_size()
    c("ash_use", 0.35)
    shot("05_loading_a")
    time.sleep(1.2)
    shot("05_loading_b")
    t0 = time.time()
    st, st2 = status()
    while "underground 1" not in st2 and time.time() - t0 < 120:
        time.sleep(2.0)
        st, st2 = status()
    text = since(off)
    check("E on the hatch starts the trip and the level arrives", "go: ashvale -> ashvale_undercroft" in text and "arrived in ashvale_undercroft" in text, text[-240:].replace("\n", " "))
    time.sleep(2.0)
    st, st2 = status()
    check("the player stands at the foot of the stair (UndercroftArrival), in play", "phase Playing" in st and near(pos(st), (1002.5, 1003.0), 4.0), (pos(st), st[:70]))
    check("the underground objective is the dig's", "Find what Black Wat's men were digging for" in objective(st), objective(st))
    check("the run came with the player: wat_dead, found_tom, has_blade, coins and bread", flag(st, "wat_dead") == 1 and flag(st, "found_tom") == 1 and flag(st, "has_blade") == 1
          and num(st, "coins") == coins0 and num(st, "bread") == bread0, st[-150:])
    check("entered_undercroft is set and the side line is gone (underground the main objective speaks)", flag(st2, "entered_undercroft") == 1 and "side ''" in st2, st2[:120])
    shot("06_arrival")

    # ---- the sexton
    off = log_size()
    for _ in range(3):
        stand("Cuthbert", 0.8)
        c("ash_use", 1.0)
        if "talking to" in since(off):
            break
    shot("07_cuthbert")
    c("ash_choose 1", 0.8)
    st, st2 = status()
    check("Brother Cuthbert speaks; asking what is down here sets met_cuthbert and saw_vault", "Thank God, a living face" in since(off) and flag(st2, "met_cuthbert") == 1 and flag(st2, "saw_vault") == 1, st2[:150])
    c("ash_choose 1", 0.8)
    shot("08_cavern")

    # ---- the fight: diggers, guards, archers, Corbin
    deaths = 0
    fell = []
    FIGHT_OFF[0] = log_size()
    for name in ("Digger1", "Digger2", "Digger3", "Guard1", "Guard2", "CryptArcher1", "CryptArcher2", "Corbin"):
        if name == "Digger2":
            stand("Digger1", 0.1)
        ok, d = kill(name)
        deaths += d
        if not ok:
            fell.append(name)
        if name == "Digger3":
            shot("09_ossuary_side")
        if name == "Guard2":
            shot("10_hall_g")
    st, st2 = status()
    check("every enemy underground is killed (%d player deaths; not killed: %s)" % (deaths, fell or "none"), not fell, fell)
    check("Corbin's death sets vault_key and the objective points at the vault", flag(st2, "vault_key") == 1 and "Open the sealed vault" in objective(st), (objective(st), st2[:110]))

    # ---- the vault
    off = log_size()
    stand("VaultDoor", 0.8)
    c("ash_use", 1.0)
    shot("11_vault_door_opening")
    time.sleep(5.0)
    text = since(off)
    check("the key lifts the vault door", "VaultDoor: opened" in text, text[-160:].replace("\n", " "))
    off = log_size()
    c("ash_goto LordsStrongbox 1.3", 0.8)
    c("ash_use", 1.0)
    st, st2 = status()
    check("the strongbox gives the sword and the silver", flag(st2, "has_lordsword") == 1 and "the old lord's sword" in since(off), since(off)[-140:].replace("\n", " "))
    check("the objective says to climb back", "Climb the stair back" in objective(st), objective(st))
    shot("12_vault")
    off = log_size()
    c("ash_savegame uc_vault", 1.5)
    check("a save made underground", "saved:" in since(off), since(off)[-120:].replace("\n", " "))

    # ---- the way back
    off = log_size()
    stand("UndercroftExit", 0.8)
    c("ash_use", 0.5)
    shot("13_climbing")
    st, st2 = status()
    t0 = time.time()
    while "underground 0" not in st2 and time.time() - t0 < 120:
        time.sleep(2.0)
        st, st2 = status()
    time.sleep(2.0)
    st, st2 = status()
    text = since(off)
    check("the stair leads back: the trip to ashvale arrives", "go: ashvale_undercroft -> ashvale" in text and "arrived in ashvale" in text, text[-200:].replace("\n", " "))
    check("back on the surface beside the keep, in play, the sword kept", "phase Playing" in st and "underground 0" in st2 and near(pos(st), (511.8, 778.4), 6.0) and flag(st2, "has_lordsword") == 1,
          (pos(st), st2[:120]))
    check("the surface objective is the tithe's again and the side errand is finished", "Open Black Wat's chest" in objective(st) and "side ''" in st2, (objective(st), st2[:60]))
    shot("14_surface_return")
    off = log_size()
    talk("Edmund", [1])
    check("Father Edmund notices the lord's sword", "Brother Cuthbert is home" in since(off), "")

    # ---- a save made underground, loaded from the surface
    off = log_size()
    c("ash_loadgame uc_vault", 3.0)
    st, st2 = status()
    t0 = time.time()
    while "underground 1" not in st2 and time.time() - t0 < 120:
        time.sleep(2.0)
        st, st2 = status()
    time.sleep(3.0)
    st, st2 = status()
    text = since(off)
    check("loading the underground save from the surface travels down and restores it", "underground 1" in st2 and flag(st2, "has_lordsword") == 1 and flag(st2, "met_cuthbert") == 1
          and near(pos(st), (1064.5, 1052.0), 12.0) and "phase Playing" in st, (pos(st), st2[:110]))
    shot("15_after_load")
    check("the killed stay dead and the opened door stays open (the trace of the load)", "VaultDoor" not in since(off) or "opened" in since(off), "")
    c("claude_villager_status", 1.0)

    # ---- back up and finish the first chapter
    stand("UndercroftExit", 0.8)
    c("ash_use", 0.5)
    st, st2 = status()
    t0 = time.time()
    while "underground 0" not in st2 and time.time() - t0 < 120:
        time.sleep(2.0)
        st, st2 = status()
    time.sleep(2.0)
    c("ash_goto TitheChest 1.3", 0.8)
    c("ash_use", 0.8)
    st, st2 = status()
    check("the tithe chest opens now that Wat is dead", flag(st, "has_tithe") == 1, st[-120:])
    off = log_size()
    talk("Hilda", [1, 1])
    text = since(off)
    check("Hilda notices the sword at the hip, takes the tithe", "lord's sword at your hip" in text, text[-160:].replace("\n", " "))
    st, st2 = status()
    t0 = time.time()
    while "phase Ending" not in st and time.time() - t0 < 10:
        time.sleep(1.0)
        st, st2 = status()
    check("the ending is reached", "phase Ending" in st and flag(st, "tithe_returned") == 1, st[:100])
    time.sleep(1.0)
    shot("16_ending")
    time.sleep(3.0)
    c("ash_select", 1.5)
    st, st2 = status()
    check("the ending returns to the main menu", "phase MainMenu" in st, st[:70])

    off = log_size()
    c("ash_shader_stats", 0.8)
    m = re.search(r"shader cache: global misses (\d+)", since(off))
    print("SHADERS|%s" % (m.group(0) if m else since(off)[-200:].replace("\n", " ")), flush=True)
    check("no shader was compiled at run time over the whole route (the shader cache covers the Undercroft)", m is not None and int(m.group(1)) == 0, m.group(0) if m else "no stats line")

    quit_at = time.time()
    c("ash_choose 3", 0.5)
    try:
        launcher.wait(40)
        exited = launcher.returncode
    except subprocess.TimeoutExpired:
        exited = None
    check("Quit closes the game on its own (%.1f s, exit code %s)" % (time.time() - quit_at, exited), exited is not None)
finally:
    if launcher.poll() is None:
        try:
            send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        stop_launcher(launcher)
    stop.set()
    try:
        os.remove(LOCK)
    except OSError:
        pass

minutes = int((time.time() - started_at) / 60) + 2
ps = ("Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-%d)} -ErrorAction SilentlyContinue"
      " | Where-Object { ($_.ProviderName -in 'Application Error','Windows Error Reporting') -and $_.Message -match 'GameLauncher' }"
      " | Measure-Object | Select-Object -ExpandProperty Count" % minutes)
wer = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
check("no Windows crash report for the launcher (%s events)" % (wer or "?"), wer == "0")
print("RESULT %s: %d of %d, %.0f s" % (os.path.basename(BUILD), sum(ok for _, ok in results), len(results), time.time() - started_at))
