"""Run a list of bridge commands from a file, one per line.

Exists to collapse round trips. Driving a multi-step editor change one command
at a time is fine interactively and wasteful otherwise -- a retexture pass is
eight commands whose results only matter in aggregate. It also has to run
OUTSIDE Sandbox: a script inside the editor's own interpreter cannot call the
bridge, because it is occupying the main thread the bridge needs to answer on.

Lines starting with # are comments. A line of the form `sleep <seconds>` waits
instead of sending, which is how you give the editor time to finish a reload
before screenshotting it.

Usage:  python bridge/bridge_run.py commands.txt [--full]
        --full prints whole responses instead of the first 300 characters.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import json
import socket
import sys
import time

HOST, PORT, TIMEOUT = "127.0.0.1", 37778, 180.0


def send(cmd, timeout=TIMEOUT):
    payload = json.dumps({"id": 1, "cmd": cmd}) + "\n"
    with socket.create_connection((HOST, PORT), timeout) as s:
        s.settimeout(timeout)
        s.sendall(payload.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def main():
    path = sys.argv[1]
    full = "--full" in sys.argv[2:]
    lines = [l.strip() for l in open(path, encoding="utf-8")]
    failures = 0
    stalled = 0

    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line.startswith("sleep "):
            time.sleep(float(line.split(None, 1)[1]))
            print("--- slept %s s" % line.split(None, 1)[1])
            continue

        t0 = time.time()
        try:
            resp = send(line)
        except Exception as exc:
            print("FAIL %s\n     transport: %s: %s" % (line, type(exc).__name__, exc))
            failures += 1
            continue

        ms = int((time.time() - t0) * 1000)
        out = resp.get("output", resp.get("error", ""))
        if isinstance(out, str) and not full:
            # Engine log noise dwarfs the actual answer on anything that
            # reloads a level; keep the head, which is where the result is.
            try:
                parsed = json.loads(out)
                if isinstance(parsed, dict):
                    # Drop engine_log (hundreds of lines of level-load spam) but
                    # KEEP log -- that is the script's own stdout, i.e. the
                    # answer. Trimming both hid a pak-integrity check's result
                    # behind an "ok" and cost a whole diagnostic round.
                    parsed.pop("engine_log", None)
                    out = json.dumps(parsed)
            except Exception:
                pass
            out = out[:600]
        status = "ok  " if resp.get("ok") else "FAIL"
        if not resp.get("ok"):
            failures += 1
        print("%s %5dms  %s\n       %s" % (status, ms, line[:110], out))

        # If the editor's main thread is blocked, every remaining command will
        # sit through the same 20-second server-side timeout and get nowhere.
        # The usual cause is this script having been started with a BLOCKING
        # call from inside Sandbox's own Python: the interpreter then holds the
        # very thread the bridge answers on, and waits for a reply that cannot
        # come until it lets go. Launch it detached (subprocess.Popen with
        # DETACHED_PROCESS) instead. Bail out rather than freezing the editor
        # for another list-length times twenty seconds.
        if "main thread is genuinely blocked" in str(out):
            stalled += 1
            if stalled >= 2:
                print("\nAborting: the editor main thread is blocked, so nothing here can run.\n"
                      "If this was launched from inside Sandbox's Python, that is the deadlock --\n"
                      "start it detached instead.")
                return failures
        else:
            stalled = 0

    print("\n%d command(s) failed" % failures)
    return failures


if __name__ == "__main__":
    sys.exit(main())
