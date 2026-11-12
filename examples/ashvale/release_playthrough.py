#!/usr/bin/env python3
"""Play a packaged (Release) Ashvale build from its main menu to its ending and out through Quit -- the manual's last step: "test that your entire project works when running any of the
executables, without having to add any additional files".

Driven the way the game's own inputs are (menu items, dialogue choices, use, attack, movement -- the ash_*
commands call the same functions the key bindings do; OS-injected keys are not used, so nothing lands in
another window), over the game bridge (port 37777), and checked from the game's own trace:

  main menu, Controls screen, New Game from the menu -> the Track View intro -> play, walking up the road,
  the four quest dialogues, the locked tithe chest, the pause menu, a save before the fight, the bandits'
  chase and the whole fight (death screen + respawn if it happens), a save after it, loading each save
  back and checking what came back, recovering and returning the tithe -> the ending -> main menu, a
  second New Game (must start from scratch), Quit to main menu, and Quit -- the process must exit on its
  own with no Windows crash report. Frame time (ash_perf) and shader-cache misses (ash_shader_stats) are
  logged along the way.

    release_playthrough.py <build> [--windowed] [--set cvar=value,...]
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

BUILD = os.path.abspath(sys.argv[1])
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                if os.path.isfile(p))
WINDOWED = "--windowed" in sys.argv
# --set e_MergedMeshes=0,...: CVars set live after boot, for A/B runs of the same route (for example: is the
# playthrough's vsync-bound frame time the grass?)
EXTRA = [kv.split("=", 1) for kv in sys.argv[sys.argv.index("--set") + 1].split(",")] if "--set" in sys.argv else []
# CLAUDE_STATE_DIR: the folder the game writes claude_npc.log to (default: the workspace folder, state/ unless CRYENGINE_WORKSPACE is set); the deploy lock sits where safe_close.py / the watchdog look for it
STATE_DIR = os.environ.get("CLAUDE_STATE_DIR") or _paths.WORKSPACE
try:
    os.makedirs(STATE_DIR, exist_ok=True)
except OSError:
    pass
import safe_close  # noqa: E402  (also used below to save and close the editor, or refuse)
LOG = os.path.join(STATE_DIR, "claude_npc.log")
LOCK = _paths.workspace("claude_deploy_in_progress")
SHOTS = os.path.join(BUILD, "playthrough_shots")
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


def bandit_lines():
    off = log_size()
    c("claude_bandit_status", 0.6)
    return [l.strip() for l in since(off).splitlines() if l.startswith("bandit ")]


def wait_camp_calm(seconds=40):
    """Until every living bandit is on guard again (the visit to the tithe chest sets the whole camp after the player; a test that wants to see
    the camp notice the player must start from a camp that has not just chased somebody: bandits walking home through the gate
    are on the wrong side of the wall and facing the wrong way)."""
    for _ in range(int(seconds / 2)):
        states = [l.split()[2] for l in bandit_lines() if len(l.split()) > 2]
        if states and all(s == "Guard" for s in states):
            return True
        time.sleep(2)
    return False


def status():
    off = log_size()
    c("ash_status", 0.6)
    lines = [l for l in since(off).splitlines() if "status:" in l]
    return lines[-1] if lines else ""


def flag(st, name):
    m = re.search(r"\b%s=(\d+)" % name, st)
    return int(m.group(1)) if m else None


def num(st, name):
    m = re.search(r"\b%s (\d+)" % name, st)
    return int(m.group(1)) if m else None


def pos(st):
    m = re.search(r"at ([-\d.]+) ([-\d.]+) ([-\d.]+)", st)
    return tuple(float(v) for v in m.groups()) if m else None


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, str(detail)[:200]), flush=True)


user32 = ctypes.WinDLL("user32")
user32.SetProcessDPIAware()


def shot(name):
    """What the SCREEN shows. The engine's own screenshot (bridge.screenshot, r_getscreenshot) is taken before the 2D overlays, so it never has a menu, a dialogue or the HUD in it -- that once
    read as 'Release draws no UI' (a desktop capture does show the menu). Taken through window_capture.capture_screen, which REFUSES unless the game owns the centre and the
    four corners of its window at the expected size (an unchecked screen copy saves another program's window when it is in front of the game)."""
    os.makedirs(SHOTS, exist_ok=True)
    hwnd = game_hwnd(launcher.pid)
    if not hwnd:
        print("   shot %s: no game window" % name, flush=True)
        return
    try:
        w, h, rows = capture_screen(hwnd, (1280, 720) if WINDOWED else (2560, 1440))
    except RuntimeError as e:
        print("   shot %s: NOT TAKEN (%s)" % (name, str(e)[:90]), flush=True)
        return
    save_png(os.path.join(SHOTS, name + ".png"), w, h, rows)


def perf(label):
    off = log_size()
    c("ash_perf 4 %s" % label, 5.0)
    m = re.search(r"perf '%s': (\d+) frames .* median ([\d.]+) ms .* worst ([\d.]+) ms" % label, since(off))
    print("PERF|%s|%s" % (label, "median %s ms, worst %s ms" % (m.group(2), m.group(3)) if m else "no result"), flush=True)


def talk(npc, choices):
    # A villager startled by the player appearing 1.6 m away hurries off and E finds no one: walk up again.
    for _ in range(3):
        tried = log_size()
        c("ash_goto %s 1.6" % npc, 0.6)
        c("ash_use", 1.0)
        if "talking to %s" % npc in since(tried):
            break
        time.sleep(1.5)
    for n in choices:
        c("ash_choose %d" % n, 0.8)


def wait_phase(want, seconds):
    t = time.time()
    st = status()
    while want not in st and time.time() - t < seconds:
        time.sleep(0.5)
        st = status()
    return st


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        with open(LOCK, "w") as f:
            f.write("release_playthrough\n")
        stop.wait(60)


threading.Thread(target=keep_lock, daemon=True).start()
# safe_close (imported above): save and close the editor, or refuse: never taskkill /F Sandbox
_closed = safe_close.close_editor("release_playthrough", hold_minutes=120)
if not _closed["ok"]:
    stop.set()                                             # the lock written by keep_lock must not stay behind when nothing ran
    try:
        os.remove(LOCK)
    except OSError:
        pass
    raise SystemExit("the editor was not closed, nothing was run: " + "; ".join(_closed["refused"] or ["it did not close"]))
kill_build_launchers()
time.sleep(3)
# NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
res = ["-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720"] if WINDOWED else \
      ["-claude_test", "+r_Fullscreen", "1", "+r_Width", "2560", "+r_Height", "1440"]
started_at = time.time()
launcher = subprocess.Popen([LAUNCHER] + res + ["+s_IgnoreWindowFocus", "1"], cwd=os.path.dirname(LAUNCHER),
                            creationflags=0x8 | 0x200)
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
    check("the build boots on its own (%s, %s)" % ("Release" if "release" in LAUNCHER else "Profile", BUILD), ready)
    if not ready:
        raise SystemExit(1)
    c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
    for k, v in EXTRA:
        print("   %s -> %s" % (k, send("bridge.cvar_set %s %s" % (k, v), 5).get("output")), flush=True)
    c("ash_skip_menu 0", 0.1)

    st = status()
    check("it starts at the main menu", "phase MainMenu" in st, st[:80])
    shot("01_main_menu")
    c("ash_choose 2", 1.0)
    shot("02_controls")
    c("ash_select", 0.6)
    check("Controls opens and closes, still at the menu", "phase MainMenu" in status())

    # New Game, from the menu.
    off = log_size()
    c("ash_choose 1", 2.5)
    shot("03_intro")
    st = wait_phase("phase Playing", 25)
    text = since(off)
    check("New Game plays the Track View intro", re.search(r"cinematic 'ashvale_intro': playing .* camera bound 1", text) is not None)
    check("...which ends in play", "phase Playing" in st and "cinematic 'ashvale_intro': finished" in text, st[:80])
    shot("04_play_starts")
    p0 = pos(status())
    c("ash_hold w 5", 6.0)
    p1 = pos(status())
    walked = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5 if p0 and p1 else 0.0
    check("walking up the road moves the player (%.1f m in 5 s, z %.1f -> %.1f)" % (walked, p0[2] if p0 else 0, p1[2] if p1 else 0),
          walked > 12.0 and p1 and abs(p1[2] - p0[2]) < 6.0)
    perf("road")

    talk("Hilda", [])                         # with the retry: she startles when the player appears and may have walked off (an occasional flake)
    shot("05a_dialogue")
    for n in (1, 1, 1):
        c("ash_choose %d" % n, 0.8)
    st = status()
    check("Hilda: met_hilda", flag(st, "met_hilda") == 1, st[-120:])
    perf("village")
    talk("Oswin", [1, 1])
    st = status()
    check("Oswin gives bread", (num(st, "bread") or 0) >= 2, st[:120])
    talk("Tom", [1, 1, 1])
    st = status()
    check("Tom: found_tom", flag(st, "found_tom") == 1, st[-120:])
    talk("Garrick", [1])
    st = status()
    check("Garrick lends the blade", flag(st, "has_blade") == 1, st[-120:])
    c("ash_goto TitheChest 1.3", 0.5)
    c("ash_use", 0.8)
    check("the tithe chest is locked before Wat dies", flag(status(), "has_tithe") == 0)

    c("ash_pause", 1.0)
    paused = "phase Paused" in status()
    shot("05_pause")
    c("ash_choose 1", 0.8)
    check("pause menu opens, Resume returns to play", paused and "phase Playing" in status())

    off = log_size()
    c("ash_savegame pt_before_fight", 1.5)
    before_save = status()
    check("save before the fight", "saved:" in since(off), before_save[-100:])

    # The camp.
    off = log_size()
    c("ash_goto Tom 2", 0.3)
    # the visit to the tithe chest is in the middle of the camp and the camp may have killed the player by now: bandits ignore a dead player
    # (bPlayerAvailable), so the check below would test nothing
    if "phase Dead" in status():
        c("ash_respawn", 1.0)
        c("ash_goto Tom 2", 0.3)
    calm = wait_camp_calm()
    off = log_size()
    c("ash_goto_pos 515 750 88.1 0", 5.0)               # on the road in line with the gate's gap (ash_goto <guard> 9 stood wherever the patrolling guard happened to be)
    gate_log = since(off)
    check("the gate bandits notice and chase", re.search(r"Bandit\w*: \w+ -> Chase", gate_log) is not None,
          "camp calm before: %s; %s" % (calm, "; ".join(l.strip()[:140] for l in (gate_log.splitlines() + bandit_lines()) if "goto" in l or "[Bandit]" in l or l.startswith("bandit "))[:600]))
    shot("06_camp")
    perf("camp")
    deaths = 0
    fight_start = log_size()
    # the two archers behind the gate too: left alive they shoot the scripted player at the chest, and the run fails at random
    for bandit in ("BanditGateW", "BanditGateE", "BanditArcher1", "BanditArcher2", "BanditFire", "BanditYard", "BlackWat"):
        for _ in range(16):
            if "phase Dead" in status():
                deaths += 1
                if deaths == 1:
                    shot("07_death_screen")
                c("ash_select", 1.5)        # the death screen's "wake at the well"
                c("ash_goto %s 6" % bandit, 0.2)
            c("ash_goto %s 1.5" % bandit, 0.1)
            c("ash_attack", 0.8)
            if re.search(r"%s: DIED" % bandit, since(fight_start)):
                break
    st = status()
    check("all seven bandits killed (%d player deaths)" % deaths, (flag(st, "bandits_killed") or 0) >= 7 and flag(st, "wat_dead") == 1,
          st[-120:])
    off = log_size()
    c("ash_savegame pt_after_fight", 1.5)
    after_save = status()
    check("save after the fight", "saved:" in since(off))

    # Load each save back.
    off = log_size()
    c("ash_loadgame pt_before_fight", 5.0)
    st = status()
    check("loading the first save brings back the pre-fight story",
          "loaded:" in since(off) and flag(st, "wat_dead") == 0 and flag(st, "has_blade") == 1
          and flag(st, "bandits_killed") in (0, None), st[-140:])
    off = log_size()
    c("ash_loadgame pt_after_fight", 5.0)
    st = status()
    check("loading the second brings back the won fight",
          "loaded:" in since(off) and flag(st, "wat_dead") == 1 and (flag(st, "bandits_killed") or 0) >= 5, st[-140:])

    c("ash_goto TitheChest 1.3", 0.5)
    c("ash_use", 0.8)
    check("tithe recovered", flag(status(), "has_tithe") == 1)
    talk("Hilda", [1, 1])
    st = wait_phase("phase Ending", 6)
    check("returning the tithe ends the story", flag(st, "tithe_returned") == 1 and "phase Ending" in st, st[:100])
    shot("08_ending")
    time.sleep(4.0)
    c("ash_select", 1.5)
    check("the ending returns to the main menu", "phase MainMenu" in status())

    # A second game starts clean.
    c("ash_choose 1", 2.0)
    c("ash_select", 1.0)                 # skip the intro, as a returning player would
    st = wait_phase("phase Playing", 10)
    check("a second New Game starts from scratch", "phase Playing" in st and flag(st, "met_hilda") in (0, None)
          and flag(st, "wat_dead") in (0, None), st[-140:])
    c("ash_pause", 1.0)
    c("ash_choose 3", 1.5)
    check("Quit to main menu", "phase MainMenu" in status())

    off = log_size()
    c("ash_shader_stats", 0.8)
    m = re.search(r"shader cache: global misses (\d+)", since(off))
    check("no shader compiled at run time over the whole play (no rain in this route)", m and int(m.group(1)) == 0,
          m.group(0) if m else "no stats line")

    # Quit, the player's way.
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
    if _closed["was_running"]:                             # only the marker THIS script wrote (final_packages holds its own through the whole gate)
        safe_close.release()                               # the watchdog may reopen the editor, and the level that was open

# Windows' own crash record: Application Error / Windows Error Reporting events naming the launcher since start.
minutes = int((time.time() - started_at) / 60) + 2
ps = ("Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-%d)} -ErrorAction SilentlyContinue"
      " | Where-Object { ($_.ProviderName -in 'Application Error','Windows Error Reporting') -and $_.Message -match 'GameLauncher' }"
      " | Measure-Object | Select-Object -ExpandProperty Count" % minutes)
wer = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
check("no Windows crash report for the launcher (%s events)" % (wer or "?"), wer == "0")
print("RESULT %s: %d of %d, %.0f s" % (os.path.basename(BUILD), sum(ok for _, ok in results), len(results),
                                      time.time() - started_at))
