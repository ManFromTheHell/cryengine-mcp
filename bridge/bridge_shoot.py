"""Take viewport screenshots that are actually of the live viewport.

Two bad frames come out of Sandbox and the test that separates them is the
opposite of the obvious one.

  * A STALE frame is returned when the editor never drew. Successive captures
    are then byte-identical -- a stale viewport CONVERGES.
  * A live 3D viewport never converges. Time of day, fog, water and terrain LOD
    all keep moving, so consecutive checksums always differ.

So "keep shooting until two frames match" is exactly backwards: it stops on the
stale case and never terminates on the good one. (Tried it. It reported the grey
frames as fine and the real ones as failures.) The rule that works is: a capture
whose checksum DIFFERS from the one before it came from a viewport that is
actually drawing.

Torn frames -- terrain LOD caught mid-resolve after a big camera jump -- are a
separate problem, and the fix is just to let the editor chew for a couple of
captures before keeping one.

Usage:
    python bridge/bridge_shoot.py shots.txt

Each line: <output path>|<eye x> <eye y> <eye z>|<target x> <target y> <target z>
Blank lines and lines starting with # are ignored.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import json
import os
import socket
import sys
import time

HOST, PORT, TIMEOUT = "127.0.0.1", 37778, 120.0
MAX_TRIES = 6
SETTLE_MS = 2000
WARMUP_CAPTURES = 2
MIN_SATURATION = 8.0


def send(cmd):
    payload = json.dumps({"id": 1, "cmd": cmd}) + "\n"
    with socket.create_connection((HOST, PORT), TIMEOUT) as s:
        s.settimeout(TIMEOUT)
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "command failed"))
    out = resp.get("output", "")
    try:
        return json.loads(out)
    except Exception:
        return out


def park_cursor_over_viewport():
    """Put the pointer inside the 3D view before capturing.

    The settle jitter is a RELATIVE one-pixel nudge, so it only wakes Sandbox if
    the pointer is already over it; left anywhere else on the desktop it moves
    some other window's cursor and the viewport keeps showing its last frame.
    That is why the same run produced three good captures and three grey ones
    with no pattern in the camera positions -- it was where the mouse happened
    to be. An absolute move fixes it, and also brings the window forward."""
    try:
        info = send("bridge.window.info")
        cw, ch = info.get("client", [1000, 800])
    except Exception:
        cw, ch = 1000, 800
    send("bridge.input.move %d %d" % (int(cw * 0.62), int(ch * 0.28)))


def shoot(path, eye, target, min_saturation=None):
    send("bridge.camera.look_at %s %s" % (" ".join(eye), " ".join(target)))
    park_cursor_over_viewport()

    scratch = (os.path.dirname(path) or ".").replace("\\", "/") + "/_shoot_warmup.bmp"

    # Warm-up captures, discarded. They wake the editor and give terrain LOD
    # time to finish resolving, which is what stops the kept frame coming back
    # as ribbons of half-built geometry.
    previous = None
    for _ in range(WARMUP_CAPTURES):
        r = send("bridge.viewport.screenshot settle=%d %s" % (SETTLE_MS, scratch))
        previous = r.get("checksum")

    # Accept on SATURATION, not on the checksum changing. An undrawn viewport
    # is flat editor grey, so its mean chroma is near zero, while any real 3D
    # frame is well above it. Roughly every other capture came back grey with a
    # different checksum each time -- so "the checksum moved" passed frames that
    # were plainly blank. This measures the thing that actually distinguishes
    # them.
    for attempt in range(1, MAX_TRIES + 1):
        r = send("bridge.viewport.screenshot settle=%d %s" % (SETTLE_MS, path))
        sat = r.get("saturation")
        if sat is None:
            # Older plugin build: fall back to the weaker checksum test.
            if r.get("checksum") != previous:
                return attempt, True, r
            previous = r.get("checksum")
            continue
        if sat >= (MIN_SATURATION if min_saturation is None else min_saturation):
            return attempt, True, r
    return MAX_TRIES, False, r


def main():
    lines = [l.strip() for l in open(sys.argv[1], encoding="utf-8")]
    failures = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        fields = [p.strip() for p in line.split("|")]
        path, eye, target = fields[:3]
        min_sat = float(fields[3]) if len(fields) > 3 and fields[3] else None      # optional 4th field: a lower saturation bar for a monochrome view (wireframe); the caller then checks the picture has structure
        t0 = time.time()
        try:
            tries, stable, r = shoot(path, eye.split(), target.split(), min_sat)
        except Exception as exc:
            print("FAIL %s -- %s: %s" % (path, type(exc).__name__, exc))
            failures += 1
            continue
        print("%s %-28s %d capture(s), %5.1fs, saturation %5.1f"
              % ("ok  " if stable else "BLANK   ", os.path.basename(path), tries,
                 time.time() - t0, r.get("saturation", -1.0)))
        if not stable:
            failures += 1
    print("\n%d shot(s) never settled" % failures)
    return failures


if __name__ == "__main__":
    sys.exit(main())
