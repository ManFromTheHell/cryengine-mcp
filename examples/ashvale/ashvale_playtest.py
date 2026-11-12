"""Play Ashvale through in editor game mode, from the console, and check every step.

Injected keys never reach the game's action maps, so the story is driven with the ash_*
test commands (ash_goto / ash_use / ash_choose / ash_attack) and verified from the game's
own trace ([Ashvale] lines in claude_npc.log) plus screenshots.

    python examples/ashvale/ashvale_playtest.py [outdir]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import time

import build_ashvale as B
import editor_state as E

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
LOG = os.environ.get("CRYENGINE_NPC_LOG") or _paths.workspace("claude_npc.log")   # the game's trace log (workspace folder, state/ by default)
failures = []


def log_size():
    for _ in range(40):
        try:
            return os.path.getsize(LOG) if os.path.exists(LOG) else 0
        except PermissionError:
            time.sleep(0.05)
    return 0


def tail_since(offset):
    # The game appends to this file line by line (fopen/fclose); a read that lands on its open is
    # refused on Windows -- retry, as package_test.py does.
    for _ in range(40):
        try:
            with open(LOG, "rb") as f:
                f.seek(offset)
                return f.read().decode("utf-8", "replace")
        except PermissionError:
            time.sleep(0.05)
    return ""


def cmd(c, wait=0.4):
    r = B.send(c, timeout=60)
    time.sleep(wait)
    return r


def status():
    off = log_size()
    cmd("ash_status", 0.6)
    lines = [l for l in tail_since(off).splitlines() if "status:" in l]
    return lines[-1] if lines else ""


def shot(name):
    path = os.path.join(OUT, name + ".bmp").replace("\\", "/")
    r = B.send("bridge.viewport.screenshot settle=400 %s" % path, timeout=60)
    try:
        o = json.loads(r["output"])
        if o.get("saturation", 99) < 6:
            time.sleep(1.5)
            o = json.loads(B.send("bridge.viewport.screenshot settle=800 %s" % path, timeout=60)["output"])
        print("   shot %-18s saturation %.1f" % (name, o.get("saturation", -1)))
    except Exception:
        print("   shot", name, str(r)[:120])


def expect(label, cond, detail=""):
    # Two lines on purpose: one for a human, one for machines. The readable line carries the
    # detail text, which made check NAMES unstable when a harness parsed it.
    print("%s %s %s" % ("PASS" if cond else "FAIL", label, detail))
    print("CHECK|%s|%s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        failures.append(label)


def flag(st, name):
    m = re.search(name + r"=(\d+)", st)
    return int(m.group(1)) if m else -1


def talk(npc, choices):
    """Walk up, press E, pick the given choices (1-based); returns the trace.
    Appearing 1.6 m from a walking villager startles him and he hurries off, so E can find no one
    (trace line: 'Garrick: startled by the player at 1.6 m'); a player would walk up again -- up to 3 tries."""
    off = log_size()
    for _ in range(3):
        tried = log_size()
        cmd("ash_goto %s 1.6" % npc, 0.5)
        cmd("ash_use", 0.8)
        if "talking to %s" % npc in tail_since(tried):
            break
        time.sleep(1.5)
    for c in choices:
        cmd("ash_choose %d" % c, 0.6)
    return tail_since(off)


def fight(bandit, max_swings=12):
    """Stand at the bandit and swing until it dies; returns (killed, trace)."""
    off = log_size()
    for i in range(max_swings):
        cmd("ash_goto %s 1.5" % bandit, 0.1)
        cmd("ash_attack", 0.8)
        t = tail_since(off)
        if re.search(r"%s.*(died|killed|dead)" % bandit, t, re.I):
            return True, t
    return False, tail_since(off)


def ticking():
    """Is the game actually running? The tick counter only advances in game mode."""
    a = re.search(r"ticks (\d+)", status())
    time.sleep(1.0)
    b = re.search(r"ticks (\d+)", status())
    return a is not None and b is not None and int(b.group(1)) > int(a.group(1))


def enter_game_mode():
    """Hold the editor active, enter game mode by the editor's own report, then check the game
    ticks. (The old version toggled until the tick counter moved and alternated with a
    synthesized Ctrl+G. Both were wrong: the toggle is deferred until the editor is active, and
    a flat counter also means 'in game mode but frozen', where a toggle switches it OFF. See
    editor_state.py.)"""
    E.hold_active()
    if E.set_game_mode(True) is None:
        return False
    return ticking()



def main():
    start = log_size()
    cmd("ash_skip_menu 0", 0.1)
    expect("game mode running", enter_game_mode(), "")
    # A previous run may have left a game in progress; the menu is the start state.
    cmd("ash_mainmenu", 1.5)
    st = status()
    expect("main menu on entering the game", "phase MainMenu" in st, st[:80])
    shot("p01_mainmenu")

    cmd("ash_newgame", 1.5)
    shot("p02_intro")
    # The intro is a 12.5 s Track View flight (Cinematics.h) that ends in the player's eyes.
    for _ in range(40):
        st = status()
        if "phase Playing" in st:
            break
        time.sleep(0.5)
    expect("new game -> playing", "phase Playing" in st, st[:120])
    shot("p03_start")

    t = talk("Hilda", [1, 1, 1])
    st = status()
    expect("talk to Hilda sets met_hilda", flag(st, "met_hilda") == 1, st[:200])
    shot("p04_after_hilda")

    t = talk("Oswin", [1, 1])
    st = status()
    expect("Oswin gives bread", re.search(r"bread (\d+)", st) and int(re.search(r"bread (\d+)", st).group(1)) >= 2, st[:160])

    t = talk("Tom", [1, 1, 1])
    st = status()
    expect("Tom sets found_tom", flag(st, "found_tom") == 1, st[:200])

    t = talk("Garrick", [1])
    st = status()
    expect("Garrick lends the blade", flag(st, "has_blade") == 1, st[:200])

    off = log_size()
    cmd("ash_goto TitheChest 1.3", 0.5)
    cmd("ash_use", 0.8)
    st = status()
    expect("tithe chest locked before Wat dies", flag(st, "has_tithe") == 0, tail_since(off)[-200:])

    # Do they come for him? Stand 9 m down the path, in view of the gate, and wait.
    off = log_size()
    cmd("ash_goto Tom 2", 0.3)          # from the south, so the next goto lands outside the gate
    cmd("ash_goto BanditGateW 9", 5.0)
    t = tail_since(off)
    expect("gate bandits notice and chase", re.search(r"Bandit\w*: \w+ -> Chase \(saw the player", t) is not None,
           "; ".join(l for l in t.splitlines() if "-> " in l and "Bandit" in l)[:200])
    shot("p05a_bandit_charge")
    # The sword hits whoever is nearest in its arc, so a fight "at" one bandit often
    # kills another: count the camp's dead, not per-target kills.
    deaths = 0
    fight_start = log_size()
    # the two archers behind the gate first: left alone they shoot the scripted player to death twice and the story never ends
    for bandit in ["BanditGateW", "BanditGateE", "BanditArcher1", "BanditArcher2", "BanditFire", "BanditYard", "BlackWat"]:
        for i in range(16):
            st = status()
            if "phase Dead" in st:
                deaths += 1
                shot("p_dead_%d" % deaths)
                cmd("ash_respawn", 1.0)
                cmd("ash_goto %s 6" % bandit, 0.2)
            off = log_size()
            cmd("ash_goto %s 1.5" % bandit, 0.1)
            cmd("ash_attack", 0.8)
            if re.search(r"%s: DIED" % bandit, tail_since(fight_start)):
                break
    st = status()
    expect("all five bandits killed", flag(st, "bandits_killed") >= 5, "player deaths %d, %s" % (deaths, st[-60:]))
    shot("p05_camp_after_fight")
    st = status()
    expect("wat_dead set", flag(st, "wat_dead") == 1, st[-160:])

    cmd("ash_goto TitheChest 1.3", 0.5)
    cmd("ash_use", 0.8)
    st = status()
    expect("tithe recovered", flag(st, "has_tithe") == 1, st[-160:])

    t = talk("Hilda", [1, 1])
    time.sleep(1.5)
    st = status()
    expect("tithe returned -> ending", flag(st, "tithe_returned") == 1 and "phase Ending" in st, st[:160])
    shot("p06_ending")

    E.set_game_mode(False)
    E.release_active()
    trace = tail_since(start)
    with open(os.path.join(OUT, "playtest_trace.txt"), "w", encoding="utf-8") as f:
        f.write(trace)
    print("\n%d failure(s): %s" % (len(failures), failures))


if __name__ == "__main__":
    main()
