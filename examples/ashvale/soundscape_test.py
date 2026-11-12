#!/usr/bin/env python3
"""Do the rain, the crickets and the wind come and go with the weather and the clock?

    soundscape_test.py <build>

Measures the game's own audio session with the Windows output meter (audio_peakmeter.ps1: the sound that really leaves the
process, per 250 ms), one layer at a time -- `ash_soundscape_solo <mask>` lets only that layer through (1 bed, 2 rain, 4 night,
8 wind), and `ash_soundscape` logs what the game believes it is playing. Every layer is also measured where it must be SILENT
(the control: a meter that cannot read zero proves nothing).

  noon, dry      bed audible, wind audible; rain and crickets silent
  rain           rain audible; crickets silent; wind louder than at noon
  rain stops     rain silent again after the fade
  night, dry     crickets audible; the bed thinner than at noon; rain silent
  night + rain   crickets silent, rain audible
  volume 0       everything silent (the master volume reaches every layer)
  one-shots      each of gust / owl / nighthawk / frog is heard when played alone
  volume         a quarter volume is 10-45 % of full on the bed, volume 0 is silent (this was volume_test.py)
  footsteps      the player's steps are heard walking, with every layer muted
  scheduler      the owl / nighthawk / frog calls and the gusts fire on their own (the clocks hurried 15 times: 20 s, not 90 s)
  music          (`ash_soundscape_solo 16`) the menu track in the main menu; the day track by day, the night track at night, the danger
                 track while bandits hunt and the calm track after; music volume 0 is silent, a lower volume is quieter
  voice          (`ash_soundscape_solo 32`) a spoken line is silent with the voice layer muted (control), audible with it let through;
                 opening a conversation speaks its first line, the music ducks under it, and the line stops when the talk ends
Solo bits: 1 bed, 2 rain, 4 night, 8 wind, 16 music, 32 voice; 63 is everything.
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

HERE = _paths.ROOT
sys.path.insert(0, HERE)
BUILD = os.path.abspath(sys.argv[1])
sys.argv = [sys.argv[0], BUILD]
import night_test as N

checks = []
SILENT = 0.004        # a layer that should be silent reads below this (the meter's floor with the game idle is 0.000)


def check(name, ok, detail=""):
    checks.append(bool(ok))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def meter(pid, seconds=3):
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "audio_peakmeter.ps1"),
                          "-Pids", str(pid), "-Seconds", str(seconds)], capture_output=True, text=True, timeout=90).stdout
    # the script's own summary line has the window's true mean (4 decimals); the 250 ms list is rounded to 0.01
    m = re.search(r"max ([0-9.]+)\s+mean ([0-9.]+)", out)
    if not m:                                            # a dead instrument must not read as silence: every "should be silent" check would pass
        raise RuntimeError("audio_peakmeter produced no summary line: " + (out or "")[-200:])
    return (float(m.group(2)), float(m.group(1)))


def status():
    o = N.log_size()
    N.c("ash_soundscape", 0.8)
    text = N.log_since(o)
    m = re.findall(r"\[Ashvale\] soundscape: (.*)", text)
    return m[-1] if m else ""


def music_state():
    """The game's own account of the music: 'menu 0.00 day 1.00 ... | playing menu 0 day 1 ... | music volume 0.60 | voices started n, speaking x'."""
    o = N.log_size()
    N.c("ash_soundscape", 0.8)
    m = re.findall(r"\[Ashvale\] music: (.*)", N.log_since(o))
    return m[-1] if m else ""


def playing(state):
    m = re.search(r"playing menu (\d) day (\d) night (\d) danger (\d) end (\d)", state)
    return tuple(int(g) for g in m.groups()) if m else None      # (menu, day, night, danger, end)


def voices_started(state):
    m = re.search(r"voices started (\d+)", state)
    return int(m.group(1)) if m else -1


def diagnose(pid, solo):
    """A control that must be silent was not. Printed (not asserted): what the game believes, then the reading with nothing let through,
    then with the music volume at 0 -- which of them the leak depends on."""
    print("   DIAG game: %s" % music_state()[:170], flush=True)
    N.c("ash_soundscape_solo 0", 1.0)
    print("   DIAG solo 0:            mean %.4f peak %.4f" % meter(pid, 3), flush=True)
    N.c("ash_soundscape_solo %d" % solo, 1.0)
    N.c("ash_music 0", 1.0)
    print("   DIAG solo %d, music 0:   mean %.4f peak %.4f" % ((solo,) + meter(pid, 3)), flush=True)
    N.c("ash_music 0.6", 1.0)


def measure(pid, solo, settle, label):
    N.c("ash_soundscape_solo %d" % solo, settle)
    mean, peak = meter(pid)
    print("   %-34s solo %2d: mean %.4f peak %.4f" % (label, solo, mean, peak), flush=True)
    return mean


def main():
    stop = threading.Event()

    def keep():
        while not stop.is_set():
            open(N.LOCK, "w").write("soundscape_test\n")
            stop.wait(10)

    threading.Thread(target=keep, daemon=True).start()
    subprocess.run(["taskkill", "/F", "/IM", "Sandbox.exe"], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
    time.sleep(4)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    cfg = os.path.join(BUILD, "user", "ashvale_settings.cfg")
    try:
        os.remove(cfg)
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
        pid = p.pid
        print("-- the menu music, in the main menu", flush=True)
        N.c("ash_skip_menu 0", 0.1)
        N.c("ash_mainmenu", 2.0)
        N.c("ash_soundscape_solo 16", 5.0)                 # the music alone; its fade-in takes 3 s
        menu_mean, _ = meter(pid, 2)
        st = music_state()
        print("   mean %.4f | %s" % (menu_mean, st), flush=True)
        check("the menu music is audible in the main menu (and only the menu track)", menu_mean > SILENT and playing(st) == (1, 0, 0, 0, 0),
              "%.4f, playing %s" % (menu_mean, playing(st)))
        N.c("ash_soundscape_solo 63", 0.1)
        N.c("ash_skip_menu 1", 0.1)
        N.c("ash_newgame", 1.0)
        for _ in range(40):
            if N.status_phase() == "Playing":
                break
            time.sleep(0.5)
        pid = p.pid
        N.c("ash_soundscape_oneshots 0", 0.2)        # the scheduled calls off: the loops are measured alone
        N.c("claude_rain 0", 0.3)
        N.c("claude_tod 12 0", 0.5)                  # noon, the clock held
        N.c("ash_soundscape_day -1", 0.2)
        time.sleep(6.0)

        print("-- noon, dry", flush=True)
        print("   " + status(), flush=True)
        bed_noon = measure(pid, 1, 1.0, "bed")
        # the master volume, on the bed alone (this was volume_test.py: a quarter volume is between 10 % and 45 % of full)
        N.c("ash_volume 0.25", 1.5)
        bed_quarter, _ = meter(pid)
        N.c("ash_volume 1", 1.5)
        check("a quarter volume is quieter, between 10 % and 45 % of full", bed_noon > 0 and 0.10 * bed_noon < bed_quarter < 0.45 * bed_noon,
              "%.0f %%" % (100 * bed_quarter / max(bed_noon, 1e-9)))
        wind_noon = measure(pid, 8, 1.0, "wind")
        rain_noon = measure(pid, 2, 1.0, "rain (control: silent)")
        night_noon = measure(pid, 4, 1.0, "crickets (control: silent)")
        check("the village bed is audible at noon", bed_noon > SILENT, "%.4f" % bed_noon)
        check("the wind bed is audible", wind_noon > SILENT, "%.4f" % wind_noon)
        check("control: no rain in fair weather", rain_noon < SILENT, "%.4f" % rain_noon)
        check("control: no crickets at noon", night_noon < SILENT, "%.4f" % night_noon)

        print("-- rain", flush=True)
        N.c("claude_rain 1", 8.0)
        print("   " + status(), flush=True)
        rain_on = measure(pid, 2, 1.0, "rain")
        night_rain = measure(pid, 4, 1.0, "crickets (control: silent)")
        wind_rain = measure(pid, 8, 1.0, "wind")
        check("the rain is audible while it rains", rain_on > SILENT * 2, "%.4f" % rain_on)
        check("the wind swells in the rain", wind_rain > 1.25 * wind_noon, "%.4f vs %.4f at noon" % (wind_rain, wind_noon))

        print("-- rain stops", flush=True)
        N.c("claude_rain 0", 10.0)
        print("   " + status(), flush=True)
        rain_off = measure(pid, 2, 1.0, "rain (control: silent again)")
        check("the rain fades away when it stops", rain_off < SILENT, "%.4f" % rain_off)
        if rain_off >= SILENT:
            diagnose(pid, 2)

        print("-- night, dry", flush=True)
        N.c("claude_tod 1 0", 10.0)
        print("   " + status(), flush=True)
        crickets = measure(pid, 4, 1.0, "crickets")
        bed_night = measure(pid, 1, 1.0, "bed")
        rain_night = measure(pid, 2, 1.0, "rain (control: silent)")
        check("the crickets are audible at night", crickets > SILENT, "%.4f" % crickets)
        check("the village bed is thinner at night", bed_night < 0.75 * bed_noon, "%.4f vs %.4f at noon" % (bed_night, bed_noon))
        check("control: still no rain", rain_night < SILENT, "%.4f" % rain_night)
        if rain_night >= SILENT:
            diagnose(pid, 2)

        print("-- night + rain", flush=True)
        N.c("claude_rain 1", 10.0)
        print("   " + status(), flush=True)
        crickets_rain = measure(pid, 4, 1.0, "crickets (control: silent in rain)")
        rain_night_on = measure(pid, 2, 1.0, "rain")
        check("the crickets stop when it rains", crickets_rain < SILENT, "%.4f" % crickets_rain)
        if crickets_rain >= SILENT:
            diagnose(pid, 4)
        check("the rain is audible at night", rain_night_on > SILENT * 2, "%.4f" % rain_night_on)

        print("-- master volume 0", flush=True)
        N.c("ash_volume 0", 3.0)
        N.c("ash_soundscape_solo 63", 1.0)               # everything, the music (the night track by now) and the voice layer included
        silent_all, silent_peak = meter(pid)
        print("   all layers at volume 0: mean %.4f peak %.4f" % (silent_all, silent_peak), flush=True)
        check("volume 0 silences every layer", silent_all < SILENT, "%.4f" % silent_all)
        N.c("ash_volume 1", 3.0)
        back, _ = meter(pid)
        check("control: back at volume 1 the sound returns", back > 4 * SILENT, "%.4f" % back)

        print("-- one-shots", flush=True)
        N.c("claude_rain 0", 0.2)
        N.c("ash_soundscape_solo 0", 1.0)
        quiet, _ = meter(pid, 3)
        check("control: with every layer muted nothing plays", quiet < SILENT, "%.4f" % quiet)
        for shot in ("gust", "owl", "nighthawk", "frog"):
            results = []
            done = threading.Event()

            def listen(results=results, done=done):
                results.append(meter(pid, 4)[1])
                done.set()
            t = threading.Thread(target=listen)
            t.start()
            time.sleep(1.2)
            N.c("ash_soundscape_shot %s" % shot, 0.1)
            done.wait(20)
            t.join()
            peak = results[0] if results else 0.0
            print("   one-shot %-10s peak %.4f" % (shot, peak), flush=True)
            check("the %s is heard" % shot, peak > SILENT, "%.4f" % peak)
            time.sleep(1.0)
        print("-- the player's own footsteps (the master volume reaches them through ash_vol_step2d)", flush=True)
        N.c("claude_rain 0", 0.2)
        N.c("claude_tod 12 0", 0.2)
        N.c("ash_soundscape_solo 0", 1.0)
        N.c("ash_audio_stats", 0.3)
        o = N.log_size()
        walker = threading.Thread(target=lambda: (time.sleep(0.6), N.c("ash_hold w 2.0", 0.0)))
        walker.start()
        walk_mean, walk_peak = meter(pid, 3)
        walker.join()
        N.c("ash_audio_stats", 0.6)
        steps = re.findall(r"player steps (\d+)", N.log_since(o))
        check("footsteps are heard while walking (everything else muted)", walk_peak > 0.03 and bool(steps) and int(steps[-1]) > 2,
              "peak %.3f, %s steps" % (walk_peak, steps[-1] if steps else "?"))

        print("-- the music in a run: day, night, danger, calm again, the music volume", flush=True)
        N.c("claude_rain 0", 0.2)
        N.c("claude_tod 12 0", 0.2)
        N.c("ash_soundscape_solo 16", 7.0)
        day_mean, _ = meter(pid, 2)
        st = music_state()
        check("the day track plays by day, alone", day_mean > SILENT and playing(st) == (0, 1, 0, 0, 0), "%.4f, playing %s" % (day_mean, playing(st)))
        N.c("claude_tod 1 0", 8.0)
        night_mean, _ = meter(pid, 2)
        st = music_state()
        check("the night track replaces it at night", night_mean > SILENT and playing(st) == (0, 0, 1, 0, 0), "%.4f, playing %s" % (night_mean, playing(st)))
        N.c("ash_soundscape_danger 1", 6.0)
        danger_mean, _ = meter(pid, 2)
        st = music_state()
        check("the danger track takes over while bandits hunt", danger_mean > SILENT and playing(st) == (0, 0, 0, 1, 0), "%.4f, playing %s" % (danger_mean, playing(st)))
        N.c("ash_soundscape_danger 0", 7.0)
        st = music_state()
        check("the calm night track comes back when the danger is over", playing(st) == (0, 0, 1, 0, 0), "playing %s" % (playing(st),))
        N.c("ash_soundscape_danger -1", 0.1)
        full, _ = meter(pid, 2)
        N.c("ash_music 0", 1.5)
        none, _ = meter(pid, 2)
        N.c("ash_music 0.3", 1.5)
        half, _ = meter(pid, 2)
        N.c("ash_music 0.6", 1.5)
        check("control: music volume 0 silences the music while it plays", full > SILENT and none < SILENT, "%.4f -> %.4f" % (full, none))
        check("a lower music volume is quieter", 0.15 * full < half < 0.85 * full, "half setting %.4f vs full %.4f (%.0f %%)" % (half, full, 100 * half / max(full, 1e-9)))

        print("-- the voice: silent when its layer is muted (control), audible when let through", flush=True)
        for solo, want in ((0, False), (32, True)):
            N.c("ash_soundscape_solo %d" % solo, 0.5)
            results = []
            done = threading.Event()

            def listen(results=results, done=done):
                results.append(meter(pid, 4))
                done.set()
            t = threading.Thread(target=listen)
            t.start()
            time.sleep(1.2)
            N.c("ash_soundscape_say ash_vox_hilda_0", 0.1)
            done.wait(20)
            t.join()
            mean, peak = results[0] if results else (0.0, 0.0)
            print("   voice, solo %2d: mean %.4f peak %.4f" % (solo, mean, peak), flush=True)
            if want:
                check("a spoken line is audible with the voice layer let through", peak > SILENT, "%.4f" % peak)
            else:
                check("control: the same line is silent with the voice layer muted", peak < SILENT, "%.4f" % peak)
            time.sleep(4.0)                                   # the line (about 6 s) finishes before the next one starts
        print("-- a conversation speaks its lines and the music ducks under it", flush=True)
        N.c("claude_tod 12 0", 0.2)
        N.c("ash_soundscape_solo 63", 6.0)
        before = voices_started(music_state())
        N.c("ash_goto Hilda 1.6", 0.8)
        N.c("ash_use", 4.0)
        st = music_state()
        ducked = re.match(r"menu [\d.]+ day ([\d.]+)", st)
        check("opening a conversation speaks a line", voices_started(st) == before + 1 and "speaking ash_vox_" in st,
              "%d -> %d, %s" % (before, voices_started(st), st[-60:]))
        check("the music ducks under the conversation", bool(ducked) and float(ducked.group(1)) < 0.75,
              "day level %s (1.00 before)" % (ducked.group(1) if ducked else "?"))
        for _ in range(12):
            if N.status_phase() == "Playing":
                break
            N.c("ash_choose 1", 0.6)
        st = music_state()
        check("the line stops when the conversation ends", N.status_phase() == "Playing" and "speaking none" in st, st[-40:])

        print("-- the scheduler (night, dry, scheduled calls on, run 15 times faster for 20 s)", flush=True)
        N.c("claude_tod 1 0", 0.3)
        N.c("ash_soundscape_solo 63", 0.2)
        N.c("ash_soundscape_hurry 15", 0.1)
        N.c("ash_soundscape_oneshots 1", 0.2)
        time.sleep(20)
        N.c("ash_soundscape_hurry 1", 0.1)
        st = status()
        print("   " + st, flush=True)
        m = re.search(r"owl (\d+) nighthawk (\d+) frog (\d+) gust (\d+)", st)
        calls = (int(m.group(1)) + int(m.group(2)) + int(m.group(3))) if m else 0
        check("night calls (owl, nighthawk or frog) are scheduled on their own", calls >= 3 + 3, "%d calls counted (the 3 test shots included)" % calls)
        check("gusts are scheduled on their own", (int(m.group(4)) if m else 0) >= 1 + 2, st[-90:])
        print("RESULT soundscape_test: %d of %d" % (sum(checks), len(checks)), flush=True)
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
