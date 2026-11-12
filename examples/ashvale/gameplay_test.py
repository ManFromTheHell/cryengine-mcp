#!/usr/bin/env python3
"""The economy, Martha's side quest and Black Wat's archers, on a package.

    gameplay_test.py <build> [--project <dir>]

Console-driven (ash_goto stands the player facing someone, ash_use is E, ash_choose picks a dialogue choice) with the game's own trace
as the witness:
  static    every spoken node of dialogue.xml has its voice trigger in claude_voice.xml and its OGG in the assets folder (a dialogue edit
            without a make_voices.py run leaves the lines silent or, worse, the wrong line on a node: the index is the node's position)
  Oswin     the first visit gives 2 bread and he remembers it; the second offers a loaf for 2 coins and the purse moves by exactly that
  Martha    the first visit asks for bread; a loaf given takes 1 bread, pays 5 coins and sets fed_martha; the next visit is the thankful one
  archers   (needs the level's two archers) from out of their sight nobody shoots (control); from the road below the gate they loose
            arrows, and the arrows end somewhere (in the player or in a wall), never in the air for ever
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
sys.path.insert(0, HERE)
BUILD = os.path.abspath(sys.argv[1])
sys.argv = [sys.argv[0], BUILD]
import night_test as N

checks = []


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def bandit_lines():
    """claude_bandit_status as lines: 'bandit <name> <State> at x y: player ...' for each living bandit"""
    o = N.log_size()
    N.c("claude_bandit_status", 0.6)
    return [l.strip() for l in N.log_since(o).splitlines() if l.startswith("bandit ")]


def wait_camp_calm(seconds=40):
    """Until every living bandit is on guard again: a test that wants the camp to notice the player must start from a camp that has not just
    chased somebody (a bandit walking home through the gate is neither facing the road nor on the right side of the wall)."""
    for _ in range(int(seconds / 2)):
        states = [l.split()[2] for l in bandit_lines() if len(l.split()) > 2]
        if states and all(s == "Guard" for s in states):
            return True
        time.sleep(2)
    return False


def trace(cmd, pattern, wait=0.8):
    o = N.log_size()
    N.c(cmd, wait)
    m = re.findall(pattern, N.log_since(o))
    return m[-1] if m else None


def purse():
    m = trace("ash_status", r"coins (\d+), bread (\d+)")
    return (int(m[0]), int(m[1])) if m else (-1, -1)


def talk(who, tries=4):
    """Stand in front of `who` and press E; True once the game says it is talking to them (a walking villager can step away)."""
    for _ in range(tries):
        o = N.log_since(N.log_size())
        o = N.log_size()
        N.c("ash_goto %s 1.6" % who, 0.8)
        N.c("ash_use", 1.2)
        if "talking to" in N.log_since(o) and who in N.log_since(o):
            return N.log_since(o)
    return ""


def static_voice_check():
    """Every node that has text has a trigger in claude_voice.xml and a file in the assets folder."""
    dialogue = ET.parse(os.path.join(PROJECT, "Assets", "libs", "ashvale", "dialogue.xml")).getroot()
    voice = ET.parse(os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "ace", "claude_voice.xml")).getroot()
    triggers = {t.get("name") for t in voice.iter("Trigger")}
    assets = os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "assets")
    missing = []
    count = 0
    for d in dialogue:
        for j, n in enumerate(d):
            sub = n.find("Text")
            text = ((sub.text if sub is not None else n.text) or "").strip()
            if not text:
                continue
            count += 1
            stem = "%s_%d" % (d.get("id"), j)
            if "ash_vox_%s" % stem not in triggers or "ash_vox_%s_stop" % stem not in triggers or not os.path.isfile(os.path.join(assets, "vox_%s.ogg" % stem)):
                missing.append(stem)
    check("every spoken line has its voice trigger, stop trigger and file", count > 0 and not missing, "%d lines, missing %s" % (count, missing[:6]))


def main():
    static_voice_check()
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("gameplay_test\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    subprocess.run(["taskkill", "/F", "/IM", "Sandbox.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
    time.sleep(4)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    for f in ("ashvale_slot1.xml", "ashvale_slot2.xml", "ashvale_slot3.xml", "ashvale_settings.cfg"):
        try:
            os.remove(os.path.join(BUILD, "user", f))
        except OSError:
            pass
    p = subprocess.Popen([N.LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                         cwd=os.path.dirname(N.LAUNCHER), creationflags=0x8 | 0x200)
    try:
        for _ in range(90):
            time.sleep(3)
            try:
                o = N.log_size(); N.send("ash_status", 5); time.sleep(0.5)
                if "status: ticks" in N.log_since(o):
                    break
            except Exception:
                pass
        N.c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        N.c("ash_skip_menu 1", 0.1)
        N.c("ash_newgame", 1.0)
        for _ in range(40):
            if N.status_phase() == "Playing":
                break
            time.sleep(0.5)
        N.c("claude_tod 12 0", 0.3)
        N.c("claude_npc_life 0", 0.2)         # villagers on errands (anvil, log piles, altar ...) stand where ash_goto <name> would put the player beside coins lying there: the economy checks need them at their posts
        N.c("ash_soundscape_oneshots 0", 0.2)
        time.sleep(2.0)

        print("-- Oswin: the first visit is a gift, the next is a shop", flush=True)
        start = purse()
        check("a new game starts with 3 coins and 1 bread", start == (3, 1), str(start))
        text = talk("Oswin")
        check("Oswin can be talked to", "talking to" in text, text[-80:])
        N.c("ash_choose 1", 0.8)          # "Thank you. What's the talk in the village?" -- gives the bread
        N.c("ash_choose 1", 0.8)          # the gossip ends
        after_gift = purse()
        check("the first visit gives 2 bread", after_gift == (3, 3), "%s -> %s" % (start, after_gift))
        text = talk("Oswin")
        check("the second visit is the shop (he remembers)", "Back again" in text or "two coins a loaf" in text, text[-110:])
        N.c("ash_choose 1", 0.8)          # "A loaf, please. (2 coins)"
        bought = purse()
        check("a loaf costs exactly 2 coins and adds exactly 1 bread", bought == (1, 4), "%s -> %s" % (after_gift, bought))
        N.c("ash_choose 1", 0.8)          # the shop node: with 1 coin left only "That's all" is offered
        shut = purse()
        check("with 1 coin left a second loaf is not sold (the purse does not move)", shut == (1, 4) and N.status_phase() == "Playing", "%s, phase %s" % (shut, N.status_phase()))

        print("-- Martha: bread for her children", flush=True)
        text = talk("Martha")
        check("Martha can be talked to", "talking to" in text, text[-80:])
        N.c("ash_choose 1", 0.8)          # "Take a loaf. (1 bread)"
        fed = purse()
        check("feeding Martha takes 1 bread and pays 5 coins", fed == (6, 3), "%s -> %s" % (bought, fed))
        N.c("ash_choose 1", 0.8)          # "I will. Look after them."
        o = N.log_size()
        text = talk("Martha")
        check("Martha is grateful the next time, and warns of the archers", "little ones are asleep" in text or "Two of Wat" in text, text[-130:])
        N.c("ash_choose 1", 0.4)
        check("she does not pay twice", purse() == fed, "%s" % (purse(),))

        print("-- the archers", flush=True)
        # The player stands where they can be seen: on the road 18 m south of Archer 1, in line with the gap of the ruined wall's gate
        # (the wall hides everything else: a first version stood 18 m WEST of him, behind the wall, and nobody ever saw anyone).
        # Everyone in the camp comes at once, so the window is short: the first arrow is about 2.5 s after they notice.
        N.c("ash_goto_pos 460 700 40 0", 0.5)                     # far from every bandit: nobody sees the player (the control)
        o = N.log_size()
        time.sleep(12)
        quiet = N.log_since(o)
        check("control: nobody shoots a player who is out of sight", len(re.findall(r"looses an arrow", quiet)) == 0,
              "%d arrows" % len(re.findall(r"looses an arrow", quiet)))
        calm = wait_camp_calm()
        o = N.log_size()
        N.c("ash_goto_pos 515 750 88.1 0", 0.3)
        text = ""
        for _ in range(40):                                       # up to 20 s: stop early once two arrows have ended somewhere
            time.sleep(0.5)
            text = N.log_since(o)
            if len(re.findall(r"arrow hits the player|an arrow strikes the world", text)) >= 2 or N.status_phase() == "Dead":
                break
        fired = len(re.findall(r"looses an arrow", text))
        drawn = len(re.findall(r"draws the bow", text))
        hits = re.findall(r"arrow hits the player for (\d+) \(health (\d+)\)", text)
        walls = len(re.findall(r"an arrow strikes the world", text))
        print("   drew %d, loosed %d, hit the player %d, struck the world %d" % (drawn, fired, len(hits), walls), flush=True)
        detail = "%d drawn, %d loosed" % (drawn, fired)
        if drawn < 1 or fired < 1:
            detail += " | camp calm before: %s | %s" % (calm, " ;; ".join(l[:150] for l in bandit_lines() if "Archer" in l or "Gate" in l))
        check("an archer in sight draws and looses arrows", drawn >= 1 and fired >= 1, detail)
        check("the arrows end in the player or in a wall", len(hits) + walls >= 1, "%d hits, %d wall strikes" % (len(hits), walls))
        print("RESULT gameplay_test: %d of %d" % (sum(checks), len(checks)), flush=True)
    finally:
        try:
            N.send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
        stop.set()
        try:
            os.remove(N.LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
