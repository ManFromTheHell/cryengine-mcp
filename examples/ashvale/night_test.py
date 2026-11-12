#!/usr/bin/env python3
"""Playable nights, measured on a package.

    night_test.py <build> [--only clock,lamps,shots,home,sweep,campfire]     (sweep is not in the default set)

clock  The day runs through the engine's own time-of-day playback: after New Game the hour advances at
       24/(ash_day_minutes*60) h/s (1/60 at the default 24); 'claude_tod 12 0' freezes it; the pause menu stops it.
lamps  ash_lamp_status: 0 of 8 lamps lit at noon, 8 of 8 at 21:00; ash_lamps 0 forces all off (the A/B lever).
shots  Engine screenshots at 21:00 at a door lamp, the well lantern and the road: lamps on vs off (ash_lamps -1/0),
       player torch on vs off, with mean luminance, clipped fraction and flame pixels; no flame with every lamp out
       (control: the torch's fire registers), under 1.5 % burnt white; a contact sheet ../night_test/night_sheet.bmp.
sweep  ash_lamp_scale 0.5..2 at the Cottage B door (prints mean and clipping per step).
home   At dusk (17.3 h, clock at 0.02 h/s) every resident logs 'inside for the night', and at dawn
       (5.3 h) 'left home'; Will lights a fire at dusk. Control: the same wall-clock time at 12:00 frozen --
       nobody goes home, so the hour is what sends them.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = _paths.ROOT
# The packaged build is argv[1] of the script that runs (this module is imported by ~10 sibling tests); without one the module still imports (BUILD and LAUNCHER are None) and run() refuses.
BUILD = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
ONLY = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else ["clock", "lamps", "shots", "campfire", "home"]
# CRYENGINE_TEST_OUT: where the shots go (default: next to the project folder); CRYENGINE_GAME_LOG / CRYENGINE_DEPLOY_LOCK: the game's log and the watchdog lock
# (defaults: the workspace folder, state/)
OUT = os.environ.get("CRYENGINE_TEST_OUT") or os.path.join(os.path.dirname(__import__("project_root").PROJECT), "night_test")
LAUNCHER = next((p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                 if os.path.isfile(p)), None) if BUILD else None
LOG = os.environ.get("CRYENGINE_GAME_LOG") or _paths.workspace("claude_npc.log")
LOCK = os.environ.get("CRYENGINE_DEPLOY_LOCK") or _paths.workspace("claude_deploy_in_progress")
RESIDENTS = ["Oswin", "Edmund", "Hilda", "Tom", "Martha", "Will", "Garrick"]
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok)))
    print("CHECK|%s|%s|%s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def send(cmd, timeout=30.0):
    with socket.create_connection(("127.0.0.1", 37777), timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode())
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


def lamp_status():
    off = log_size()
    c("ash_lamp_status", 0.6)
    m = re.findall(r"lamps: (\d+) of (\d+) lit at ([\d.]+) h \((\w+)\)", log_since(off))
    return (int(m[-1][0]), int(m[-1][1]), float(m[-1][2]), m[-1][3]) if m else (None, None, None, None)


def status_phase():
    off = log_size()
    c("ash_status", 0.6)
    m = re.findall(r"phase (\w+)", log_since(off))
    return m[-1] if m else ""


# ------------------------------------------------------------------ screenshots
def read_tga(path):
    data = open(path, "rb").read()
    idlen = data[0]
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    step = bpp // 8
    return w, h, step, data[18 + idlen:18 + idlen + w * h * step], bool(desc & 0x20)


def mean_luma(path):
    return frame_stats(path)[0]


def frame_stats(path, s=2):
    """(mean luminance, fraction clipped to white, warm-bright 'flame' pixels) on every s-th pixel. The flame count
    also takes in a wall lit warm and bright, so it is 'no flame' only when it is near zero: typical baseline, lamps
    off 0..4 except the Tavern's orphaned flame (36); anything lit 900..5800. Clipped: lamps on 4.7..7.4 %, torch
    5.9..7.7 % (the washed-out facades), lamps off 0."""
    w, h, step, px, _ = read_tga(path)
    n = clip = flame = 0
    total = 0.0
    for y in range(0, h, s):
        base = y * w * step
        for x in range(0, w, s):
            i = base + x * step
            b, g, r = px[i], px[i + 1], px[i + 2]
            l = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
            total += l
            n += 1
            clip += l > 0.97
            flame += r > 200 and g > 90 and b < r * 0.6 and l > 0.45
    return total / max(1, n), clip / max(1, n), flame


def thumbnail(path, tw=320):
    w, h, step, px, top_down = read_tga(path)
    th = tw * h // w
    rows = []
    for ty in range(th):
        y = ty * h // th
        src_y = y if top_down else h - 1 - y
        base = src_y * w * step
        row = bytearray()
        for tx in range(tw):
            i = base + (tx * w // tw) * step
            row += bytes((px[i], px[i + 1], px[i + 2]))
        rows.append(row)
    return tw, th, rows


def write_sheet(path, grid):
    tw, th = grid[0][0][0], grid[0][0][1]
    cols, nrows = max(len(r) for r in grid), len(grid)
    W, H = cols * (tw + 4), nrows * (th + 4)
    row_bytes = (W * 3 + 3) & ~3
    img = [bytearray(row_bytes) for _ in range(H)]
    for gy, row in enumerate(grid):
        for gx, (w, h, rows) in enumerate(row):
            for y in range(h):
                img[gy * (th + 4) + y][gx * (tw + 4) * 3:gx * (tw + 4) * 3 + w * 3] = rows[y]
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", 54 + row_bytes * H, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, W, H, 1, 24, 0, row_bytes * H, 2835, 2835, 0, 0))
        for y in range(H - 1, -1, -1):
            f.write(bytes(img[y]))


def shot(name):
    path = os.path.join(OUT, "night_%s.tga" % name).replace("\\", "/")
    send("bridge.screenshot %s" % path, 40)
    time.sleep(1.0)
    return path if os.path.isfile(path) else None


# ------------------------------------------------------------------ parts
def part_clock():
    c("ash_day_minutes 24", 0.2)
    c("ash_newgame", 1.0)
    for _ in range(40):
        if status_phase() == "Playing":
            break
        time.sleep(0.5)
    # 15 s, not 60: the hour is read to 0.01 h, the expected change is 0.25 h. The pause and freeze checks are 6 s (the clock
    # would move 0.1 h in that time, 50 times the tolerance), so they still catch a clock that runs.
    _, _, h0, _ = lamp_status()
    time.sleep(15)
    _, _, h1, _ = lamp_status()
    slope = (h1 - h0) / 15.0 if h0 is not None and h1 is not None else 0.0
    check("clock runs at 1/60 h/s after New Game (24 min/day)", abs(slope - 1 / 60.0) < 0.0015,
          "%.2f -> %.2f h in 15 s = %.5f h/s" % (h0 or -1, h1 or -1, slope))
    c("ash_pause", 1.0)
    _, _, p0, _ = lamp_status()
    time.sleep(6)
    _, _, p1, _ = lamp_status()
    c("ash_pause", 1.0)
    check("pause menu stops the clock", p0 is not None and abs(p1 - p0) < 0.002, "%.3f -> %.3f h over 6 s paused" % (p0 or -1, p1 or -1))
    c("claude_tod 12 0", 1.0)
    _, _, f0, _ = lamp_status()
    time.sleep(6)
    _, _, f1, _ = lamp_status()
    check("claude_tod <h> 0 freezes it (the tests' instrument)", f0 is not None and abs(f1 - f0) < 0.002,
          "%.3f -> %.3f h over 6 s" % (f0 or -1, f1 or -1))


def part_lamps():
    c("ash_lamps -1", 0.2)
    c("claude_tod 12 0", 2.5)
    lit, total, h, ph = lamp_status()
    check("no lamp lit at noon", total and lit == 0, "%s of %s at %s h" % (lit, total, h))
    c("claude_tod 21 0", 2.5)
    lit, total, h, ph = lamp_status()
    check("every lamp lit at 21:00", total and lit == total and total >= 8, "%s of %s at %s h" % (lit, total, h))
    c("ash_lamps 0", 2.5)
    lit, total, h, ph = lamp_status()
    check("ash_lamps 0 puts them all out (A/B lever)", total and lit == 0, "%s of %s" % (lit, total))
    c("ash_lamps -1", 2.5)


VIEWS = [("door_cottageB", "484.9 336.9 36.3 180"), ("well", "500 352.5 36.4 0"), ("tavern", "514.5 372.9 36.3 -90"),
         ("road", "503.4 607.5 44.6 0")]


def part_shots():
    os.makedirs(OUT, exist_ok=True)
    c("claude_tod 21 0", 1.0)
    grid, rows = [], []
    for view, pos in VIEWS:
        row = []
        for label, lamps, torch in (("lamps_on", -1, 0), ("lamps_off", 0, 0), ("torch", 0, 1)):
            c("ash_goto_pos %s" % pos, 0.5)
            c("ash_lamps %d" % lamps, 0.2)
            c("ash_torch %d" % torch, 6.0)          # lamps check once a second; eye adaptation settles
            p = shot("%s_%s" % (view, label))
            if p is None:
                print("NO SHOT", view, label, flush=True)
                continue
            luma, clipped, flame = frame_stats(p)
            row.append(thumbnail(p))
            rows.append((view, label, luma, clipped, flame))
            print("FRAME|%-14s|%-9s|mean %.3f clipped %.4f flame px %d" % (view, label, luma, clipped, flame), flush=True)
        grid.append(row)
    c("ash_torch 0", 0.2)
    c("ash_lamps -1", 0.2)
    if grid and grid[0]:
        write_sheet(os.path.join(OUT, "night_sheet.bmp"), grid)
    get = lambda v, l, k=2: next((x[k] for x in rows if x[0] == v and x[1] == l), 0.0)
    for view, _ in VIEWS[:3]:
        check("%s: the lamps light the scene" % view, get(view, "lamps_on") > get(view, "lamps_off") * 1.15,
              "on %.3f vs off %.3f" % (get(view, "lamps_on"), get(view, "lamps_off")))
    check("the torch lights the road at night", get("road", "torch") > get("road", "lamps_off") * 1.3,
          "torch %.3f vs none %.3f" % (get("road", "torch"), get("road", "lamps_off")))
    # Flames: none may burn with every lamp out (the Tavern's orphaned one did) -- and the same count must see the
    # carried torch's fire, or a zero proves nothing.
    check("control: the flame count sees the torch's fire", min(get(v, "torch", 4) for v, _ in VIEWS) > 150,
          ", ".join("%s %d" % (v, get(v, "torch", 4)) for v, _ in VIEWS))
    worst = max(VIEWS, key=lambda v: get(v[0], "lamps_off", 4))[0]
    check("no flame burns with the lamps out", get(worst, "lamps_off", 4) <= 10,
          "most: %s %d flame px" % (worst, get(worst, "lamps_off", 4)))
    # Washout: a lamp or the torch must not burn the scene white (the first version clipped 4.7..7.7 %).
    for view, _ in VIEWS[:3]:
        check("%s: the lamps do not burn the wall white" % view, get(view, "lamps_on", 3) < 0.015,
              "%.2f %% clipped" % (100 * get(view, "lamps_on", 3)))
    check("the torch does not burn the view white", max(get(v, "torch", 3) for v, _ in VIEWS) < 0.015,
          ", ".join("%s %.2f %%" % (v, 100 * get(v, "torch", 3)) for v, _ in VIEWS))


def part_campfire():
    """GreenFire lit at 21:00, from 3 m: a picture of the campfire's flames (for the eye -- whether they rise).
    Legacy effects emit along the emitter's +Y, which on a component is the entity's forward axis (ParticleProp.h
    ParticleUpRotation)."""
    c("claude_tod 21 0", 1.0)
    c("ash_torch 0", 0.2)
    c("ash_lamps 0", 0.2)
    c("ash_goto GreenFire 3", 1.0)
    for _ in range(2):
        off = log_size()
        c("claude_interact_nearest", 1.0)
        states = re.findall(r"GreenFire \([^)]*\) switched (ON|OFF)", log_since(off))
        if states and states[-1] == "ON":
            break
    time.sleep(5.0)
    p = shot("campfire")
    if p:
        luma, clipped, flame = frame_stats(p)
        print("FRAME|campfire      |lit      |mean %.3f clipped %.4f flame px %d" % (luma, clipped, flame), flush=True)
    # Put it out again: left burning, it is the fire the villagers would light at dusk, and 'home' then found
    # nobody with a fire to light (TendFire stays 0.00 for every villager).
    for _ in range(2):
        off = log_size()
        c("claude_interact_nearest", 1.0)
        states = re.findall(r"GreenFire \([^)]*\) switched (ON|OFF)", log_since(off))
        if states and states[-1] == "OFF":
            break
    c("ash_lamps -1", 0.2)


def part_sweep():
    """Lamps on, at the Cottage B torch and the Tavern lantern: mean and clipping against (1) Brightness
    (ash_lamp_scale) and (2) the bulb size (ash_lamp_bulb, Brightness scaled by (0.4/b)^2 so the far light stays the
    same: beyond the bulb the engine's falloff is (b/d)^2, shadeLib.cfi:193). Prints only; the checks are in shots.
    Brightness 0.5..2 moves clipping 3.8..7 % with no trend -- the night exposure re-meters on the lamp --
    so the hot disc on the wall is the falloff's shape, not the light's strength."""
    c("claude_tod 21 0", 1.0)
    c("ash_torch 0", 0.2)
    c("ash_lamps 1", 0.2)
    for view in (VIEWS[0], VIEWS[2]):
        c("ash_goto_pos %s" % view[1], 0.5)
        for scale in (0.5, 1.0, 2.0):
            c("ash_lamp_scale %s" % scale, 4.0)
            p = shot("sweep_%s_scale_%s" % (view[0], str(scale).replace(".", "_")))
            if p:
                luma, clipped, flame = frame_stats(p)
                print("SWEEP|%-13s|scale %.2f bulb 0.40|mean %.3f clipped %.4f" % (view[0], scale, luma, clipped), flush=True)
        for bulb in (0.4, 0.6, 0.8, 1.0, 1.4):
            scale = (0.4 / bulb) ** 2
            c("ash_lamp_bulb %s" % bulb, 0.1)
            c("ash_lamp_scale %.4f" % scale, 4.0)
            p = shot("sweep_%s_bulb_%s" % (view[0], str(bulb).replace(".", "_")))
            if p:
                luma, clipped, flame = frame_stats(p)
                print("SWEEP|%-13s|scale %.2f bulb %.2f|mean %.3f clipped %.4f" % (view[0], scale, bulb, luma, clipped), flush=True)
        c("ash_lamp_bulb 0", 0.1)
        c("ash_lamp_scale 1", 0.2)
    c("ash_lamps -1", 0.2)


def part_home():
    # Control first: the same wall-clock time at noon, frozen -- nobody may go home.
    off = log_size()
    c("claude_tod 12 0", 1.0)
    time.sleep(40)
    text = log_since(off)
    check("control: at noon nobody goes home", "inside for the night" not in text and "home for the night" not in text,
          "%d 'home' lines" % text.count("home for the night"))
    # Dusk: 17.3 h, the clock at 0.02 h/s (1 game hour in 50 s).
    off = log_size()
    c("claude_tod 17.3 0.02", 1.0)
    deadline = time.time() + 150
    while time.time() < deadline:
        text = log_since(off)
        if all("%s: inside for the night" % r in text for r in RESIDENTS):
            break
        time.sleep(2)
    text = log_since(off)
    for r in RESIDENTS:
        failed = re.findall(r"%s: gave up GoHome \(([^)]*)\)" % r, text)
        check("%s goes home at dusk" % r, "%s: inside for the night" % r in text, "; ".join(failed[:2]))
    check("a fire is lit at dusk", re.search(r"\w+: LIT the fire", text) is not None,
          (re.findall(r"(\w+: LIT the fire)", text) or ["none"])[0])
    lit, total, h, ph = lamp_status()
    check("the lamps came on by themselves at dusk", total and lit == total, "%s of %s at %.2f h" % (lit, total, h or -1))
    # Dawn: 5.3 h at 0.02 h/s: out again by about 6.0 h.
    off = log_size()
    c("claude_tod 5.3 0.02", 1.0)
    deadline = time.time() + 120
    while time.time() < deadline:
        text = log_since(off)
        if all("%s: left home" % r in text for r in RESIDENTS):
            break
        time.sleep(2)
    text = log_since(off)
    for r in RESIDENTS:
        check("%s comes out at dawn" % r, "%s: left home" % r in text)
    c("claude_tod 12 0", 1.0)


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        open(LOCK, "w").write("night_test\n")
        stop.wait(10)


def main():
    if LAUNCHER is None:
        raise SystemExit("usage: night_test.py <packaged build folder> [--only a,b]  (no GameLauncher.exe found under %s)" % BUILD)
    threading.Thread(target=keep_lock, daemon=True).start()
    sys.path.insert(0, HERE)
    import final_packages
    final_packages.close_sandbox()
    subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
    time.sleep(3)
    # NO display keep-awake request (SetThreadExecutionState is not called): the display's idle timer is left to the machine's owner.
    launcher = subprocess.Popen([LAUNCHER, "-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720", "+s_IgnoreWindowFocus", "1"],
                                cwd=os.path.dirname(LAUNCHER), creationflags=0x8 | 0x200)
    try:
        for _ in range(90):
            time.sleep(3)
            try:
                off = log_size()
                send("ash_status", 5)
                time.sleep(0.5)
                if "status: ticks" in log_since(off):
                    break
            except Exception:
                pass
        c("bridge.cvar_set s_IgnoreWindowFocus 1", 0.2)
        c("ash_skip_menu 1", 0.1)
        if "clock" in ONLY:
            part_clock()
        else:
            c("ash_newgame", 1.0)
            for _ in range(40):
                if status_phase() == "Playing":
                    break
                time.sleep(0.5)
        if "lamps" in ONLY:
            part_lamps()
        if "shots" in ONLY:
            part_shots()
        if "sweep" in ONLY:
            part_sweep()
        if "campfire" in ONLY:
            part_campfire()
        if "home" in ONLY:
            part_home()
        passed = sum(ok for _, ok in checks)
        print("RESULT night_test: %d of %d" % (passed, len(checks)), flush=True)
    finally:
        try:
            send("quit", 5)
        except Exception:
            pass
        time.sleep(3)
        subprocess.run(["taskkill", "/F", "/IM", "GameLauncher.exe"], capture_output=True)
        stop.set()
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
