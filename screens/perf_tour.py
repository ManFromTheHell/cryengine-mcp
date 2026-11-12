#!/usr/bin/env python3
"""Measure a packaged Ashvale build's performance, place by place, with the engine's own tools.

    perf_tour.py <build> [--label name] [--windowed] [--functions] [--specs 1,2,3,4] [--set cvar=value,...]

Fullscreen at the desktop's native 2560x1440 (what a player gets), vsync off (it pins every sample,
CLAUDE.md). At each stop: an Statoscope user marker (Profile builds; Release has no Statoscope), then
the game's own ash_perf sampler (both configurations) for 5 s. Profile also writes
<build>/user/statoscope/perflog*.bin -- read it with statoscope_read.py, or open it in
Tools/Statoscope/Statoscope.exe. --functions adds Statoscope's function profiler ('r'), which costs
frame time itself: use it for a separate run, not for the numbers.

Stops: main menu, village green (four headings), tavern and chapel inside, the bridge, the forest road,
the camp with the bandits alive, a fight, the village at night, in rain, walking, and a cutscene.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

BUILD = os.path.abspath(sys.argv[1])
LABEL = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else os.path.basename(BUILD)
WINDOWED = "--windowed" in sys.argv
FUNCTIONS = "--functions" in sys.argv
SPECS = sys.argv[sys.argv.index("--specs") + 1].split(",") if "--specs" in sys.argv else []
# --set e_MergedMeshes=0,...: extra CVars set live after boot (an A/B lever, for example to test whether a hitch comes from the grass)
EXTRA = [kv.split("=", 1) for kv in sys.argv[sys.argv.index("--set") + 1].split(",")] if "--set" in sys.argv else []
LAUNCHER = next(p for p in (os.path.join(BUILD, "bin", d, "GameLauncher.exe") for d in ("win_x64_release", "win_x64"))
                if os.path.isfile(p))
RELEASE = "win_x64_release" in LAUNCHER
LOG = _paths.workspace("claude_npc.log").replace(os.sep, "/")             # the game's trace log (the workspace folder, state/ by default)
LOCK = _paths.workspace("claude_deploy_in_progress").replace(os.sep, "/")  # the deploy lock shared with the other tools
os.makedirs(_paths.WORKSPACE, exist_ok=True)
PORT = 37777


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
        r = send(cmd)
    except Exception as e:
        print("   !", cmd, e, flush=True)
        r = {}
    time.sleep(wait)
    return r


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


results = []


def measure(name, seconds=5.0, during=None):
    c("e_StatoscopeAddUserMarker Ashvale %s" % name, 0.1)
    time.sleep(1.0)                       # let a teleport's streaming settle into the first second, not the sample
    off = log_size()
    c("ash_perf %.1f %s" % (seconds, name), 0.0)
    if during:
        during()
    time.sleep(seconds + 1.0)
    m = re.search(r"perf '%s': (\d+) frames \| mean ([\d.]+) ms \(([\d.]+) fps\) \| median ([\d.]+) ms \(([\d.]+) fps\) \| "
                  r"p95 ([\d.]+) ms \| worst ([\d.]+) ms" % re.escape(name), log_since(off))
    if m:
        row = (name, int(m.group(1)), float(m.group(2)), float(m.group(4)), float(m.group(6)), float(m.group(7)))
        results.append(row)
        print("PERF|%-16s|%5d frames|mean %6.2f ms|median %6.2f ms (%5.0f fps)|p95 %6.2f|worst %6.2f" % (
            name, row[1], row[2], row[3], 1000.0 / row[3], row[4], row[5]), flush=True)
    else:
        print("PERF|%-16s|no result" % name, flush=True)


stop = threading.Event()


def keep_lock():
    while not stop.is_set():
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        with open(LOCK, "w") as f:
            f.write("perf_tour %s\n" % LABEL)
        stop.wait(60)


threading.Thread(target=keep_lock, daemon=True).start()
import safe_close  # noqa: E402  (saves the level and closes the editor, or refuses)
_closed = safe_close.close_editor("perf_tour", hold_minutes=120)
if not _closed["ok"]:
    raise SystemExit("editor not closed: " + "; ".join(_closed["refused"] or ["it did not close"]))
time.sleep(3)
# NO display keep-awake request here (no SetThreadExecutionState call): the display's idle timer belongs to the machine's owner.
res =["-claude_test", "+r_Fullscreen", "0", "+r_Width", "1280", "+r_Height", "720"] if WINDOWED else \
      ["-claude_test", "+r_Fullscreen", "1", "+r_Width", "2560", "+r_Height", "1440"]
launcher = subprocess.Popen([LAUNCHER] + res + ["+s_IgnoreWindowFocus", "1"], cwd=os.path.dirname(LAUNCHER),
                            creationflags=0x8 | 0x200)
before = set(glob.glob(os.path.join(BUILD, "user", "statoscope", "*.bin")))
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
    print("booted: %s (%s, pid %d)" % (ready, "Release" if RELEASE else "Profile", launcher.pid), flush=True)
    if not ready:
        raise SystemExit(1)
    # Set live: Release drops non-whitelisted +cvar launch arguments (CVARS_WHITELIST).
    for cvar, value in [("s_IgnoreWindowFocus", "1"), ("r_VSync", "0"), ("sys_MaxFPS", "0")] + [tuple(kv) for kv in EXTRA]:
        r = c("bridge.cvar_set %s %s" % (cvar, value), 0.2)
        print("   %s -> %s" % (cvar, r.get("output", r.get("error", r))), flush=True)
    if not RELEASE:
        c("e_StatoscopeLogDestination 0", 0.2)
        c("e_StatoscopeDataGroups fgmtijOu%s" % ("r" if FUNCTIONS else ""), 0.2)
        c("e_StatoscopeEnabled 1", 1.0)
    if SPECS:
        # --specs 1,2,3,4: the same three heavy stops at each system spec, set live (sys_spec applies its
        # CVar groups on change; settings that need a restart -- texture resolution -- only partly apply),
        # with a screenshot at each so quality can be compared with cost.
        c("ash_skip_menu 1", 0.1)
        c("ash_newgame", 12.0)
        shots = os.path.join(BUILD, "spec_shots")
        os.makedirs(shots, exist_ok=True)
        for spec in SPECS:
            r = c("bridge.cvar_set sys_spec %s" % spec, 4.0)
            print("   sys_spec -> %s" % r.get("output", r.get("error", r)), flush=True)
            for name, pos, rain in (("green_0", "495.5 364 36.6 0", False), ("forest_road", "503.4 607.5 44.6 0", False),
                                    ("rain_green", "495.5 364 36.6 0", True)):
                c("ash_goto_pos %s" % pos, 0.5)
                if rain:
                    c("claude_rain 1", 3.0)
                measure("spec%s_%s" % (spec, name))
                c("bridge.screenshot %s" % os.path.join(shots, "spec%s_%s.jpg" % (spec, name)).replace("\\", "/"), 1.5)
                if rain:
                    c("claude_rain 0", 1.0)
        raise SystemExit(0)
    c("ash_skip_menu 0", 0.1)
    c("ash_mainmenu", 4.0)
    measure("main_menu")

    c("ash_newgame", 1.0)
    for _ in range(40):                  # the intro is a Track View flight; wait for play to begin
        if "phase Playing" in status():
            break
        time.sleep(0.5)
    for yaw in (0, 90, 180, 270):
        c("ash_goto_pos 495.5 364 36.6 %d" % yaw, 0.5)
        measure("green_%d" % yaw, 4.0)
    for name, pos in (("tavern_inside", "524 374 36.2 180"), ("chapel_inside", "500 398 36.2 0"),
                      ("bridge_dry", "512 471 27.0 270"), ("forest_road", "503.4 607.5 44.6 0"),
                      ("camp_edge", "519 745 88.0 0")):
        c("ash_goto_pos %s" % pos, 0.5)
        measure(name)
    c("ash_goto BanditFire 6", 0.5)
    measure("camp_alive")
    c("ash_goto BanditGateW 1.5", 0.2)
    measure("fight", 5.0, lambda: [c("ash_attack", 0.6) for _ in range(6)])
    c("ash_respawn", 2.0)
    c("ash_goto_pos 495.5 364 36.6 0", 0.5)
    c("claude_tod 22", 2.0)
    measure("night_green")
    c("claude_tod 9.5", 1.0)
    c("claude_rain 1", 3.0)
    measure("rain_green")
    # Water@WaterPS with dynamic ripples (%_RT_SAMPLE4) does not compile in stock 5.7.1 (X4500, overlapping
    # register t16) and is re-requested every frame while ripples last: does that cost frame time?
    # (the dry control is the earlier bridge_dry stop, same spot and heading: ripples outlast the rain by 20 s+)
    c("ash_goto_pos 512 471 27.0 270", 3.0)
    measure("bridge_rain")
    c("claude_rain 0", 1.0)
    c("ash_goto_pos 508.4 427.5 36.6 180", 1.0)      # the road between bridge and village, facing south: walks into the village
    measure("walk", 5.0, lambda: c("ash_hold w 5", 0.0))
    c("claude_cutscene Hilda 8", 0.5)
    measure("cutscene")
finally:
    if not RELEASE:
        c("e_StatoscopeEnabled 0", 1.0)
    try:
        send("quit", 5)
    except Exception:
        pass
    time.sleep(4)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(launcher.pid)], capture_output=True)   # only the game this script started
    stop.set()
    try:
        os.remove(LOCK)
    except OSError:
        pass

print("RESULT %s (%s): %d stops, median of medians %.2f ms" % (
    LABEL, "Release" if RELEASE else "Profile", len(results),
    sorted(r[3] for r in results)[len(results) // 2] if results else float("nan")))
# The budget (the numbers are gated, not only printed; a one-off worst frame right after a teleport is not a failure). Gated: every stop was measured; the median of medians stays under 5 ms and no stop's 95th
# percentile goes over 10 ms (Release at 2560x1440 measured 2.5 ms and 3.6 ms). The worst frame is printed, not
# gated: teleports between stops cause one-off hitches that are the test's, not the game's.
if RELEASE:
    _med = sorted(r[3] for r in results)[len(results) // 2] if results else float("inf")
    _p95 = max((r[4] for r in results), default=float("inf"))
    _checks = [("every stop was measured (17)", len(results) == 17, "%d stops" % len(results)),
               ("median of medians under 5 ms", _med < 5.0, "%.2f ms" % _med),
               ("no stop's 95th percentile over 10 ms", _p95 < 10.0, "%.2f ms" % _p95)]
    for _n, _ok, _d in _checks:
        print("CHECK|%s|%s|%s" % ("PASS" if _ok else "FAIL", _n, _d))
    print("RESULT perf_budget: %d of %d" % (sum(1 for _, ok, _ in _checks if ok), len(_checks)))
new_logs = sorted(set(glob.glob(os.path.join(BUILD, "user", "statoscope", "*.bin"))) - before, key=os.path.getmtime)
if new_logs:
    print("statoscope log:", new_logs[-1], os.path.getsize(new_logs[-1]), "bytes")
