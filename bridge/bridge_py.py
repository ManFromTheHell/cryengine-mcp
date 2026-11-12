#!/usr/bin/env python3
"""Run a real multi-line Python file inside Sandbox, from outside Sandbox.

The MCP execute_python tool collapses runs of whitespace, which destroys
indentation. This sends the file through bridge.execute_python.b64 instead, so
the source arrives byte-for-byte, and prints the script's own stdout.

    bridge_py.py script.py                 print the script's output
    bridge_py.py script.py --engine-log    also print C++ log lines captured during the run
    bridge_py.py - < script.py             read the source from stdin

Run it from a normal shell (PowerShell / the real python.exe), never from inside
Sandbox's interpreter: a blocking socket round-trip back into the bridge from
the editor's own main thread deadlocks it.

Inside the script, Sandbox modules (general, object, level, terrain, ...) are
already in globals. There is no `ai` Python module even though the reference
lists ai.* commands; call those through ICommandManager from outside instead
(bridge.execute_command ai.<command>, i.e. the MCP execute_editor_command tool).

Auto-recovery (opt-in: only when CLAUDE_AUTO_RECOVER=1 is set in the environment; the MCP server defaults it to 0): a call that finds the editor GONE brings it back and retries once, and says so
(`recoveries_since(t)` is what the MCP tools turn into `editor_relaunched: true`). It does NOT bring it back when the editor was closed on purpose:
the marker file (default <workspace>/claude_editor_closed, the workspace being state/ unless CRYENGINE_WORKSPACE says otherwise; written by whatever closes the editor deliberately) means "leave it closed".
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import json
import os
import socket
import sys
import time

HOST = os.environ.get("CRYENGINE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CRYENGINE_BRIDGE_PORT", "37778"))

# A deliberate close leaves this file behind (another agent/tool writes it; this module only READS it): recovery then stands down.
EDITOR_CLOSED_MARKER = os.environ.get("CLAUDE_EDITOR_CLOSED_MARKER") or _paths.workspace("claude_editor_closed")

RECOVERY_EVENTS = []                 # [{at, state, action, seconds}] of every time THIS process brought the editor back


def _auto_recover():
    """Opt-in: with CLAUDE_AUTO_RECOVER=1 in the environment, a tool that finds the editor GONE (connection refused) brings it back (bridge_watchdog.recover) and retries once. The MCP server sets it to 0 unless you set it; scripts and tests leave it unset."""
    return os.environ.get("CLAUDE_AUTO_RECOVER") == "1"


def closed_on_purpose():
    """The marker file's path when the editor was closed deliberately (do not relaunch it), else None."""
    return EDITOR_CLOSED_MARKER if EDITOR_CLOSED_MARKER and os.path.exists(EDITOR_CLOSED_MARKER) else None


def refused_message(host=None, port=None):
    """What a refused connection means and what to do: the caller reads this in a tool error."""
    return ("could not reach the CryEngine bridge at %s:%s: connection refused, so the editor is not running (or the ClaudeBridge plugin did not load). "
            "Next: wait_for_editor (waits for the editor to come back; the watchdog restarts it) or health_check (says why). Do not retry in a loop."
            % (host or HOST, port or PORT))


def dialog_hint(dialogs=None):
    """The 'what to do' half of a timeout: a modal dialog may be open (read out-of-band), answer it by name."""
    msg = ("Next: a modal dialog may be open and block the editor's main thread: list_dialogs (what is open) and click_dialog_button('<yes|no|ok|cancel>'), "
           "or wait_for_editor if it is still loading a level.")
    if dialogs:
        msg += " Open dialogs right now: " + "; ".join(dialogs)
    return msg


def timeout_message(timeout, dialogs=None, host=None, port=None):
    """What a bridge timeout usually is (a modal dialog blocks the editor's main thread) and what to do; `dialogs` = the open dialogs read out-of-band, if any."""
    return ("the CryEngine bridge at %s:%s did not respond within %ss: the editor is busy or hung. " % (host or HOST, port or PORT, timeout)) + dialog_hint(dialogs)


class EditorClosedOnPurpose(ConnectionRefusedError):
    """The editor is not running and a deliberate-close marker file says to leave it alone."""


def closed_message(marker):
    return ("the editor is not running and was closed ON PURPOSE (marker file %s exists), so it is not relaunched. Start it yourself or ask the user, "
            "delete the marker, then wait_for_editor." % marker)


def record_recovery(report):
    """Remember that this process brought the editor back (state 'recovered')."""
    RECOVERY_EVENTS.append({"at": time.time(), "state": report.get("state"), "action": report.get("action"), "seconds": report.get("seconds")})


def recoveries_since(t):
    """The editor relaunches this process caused since epoch time t."""
    return [e for e in RECOVERY_EVENTS if e["at"] >= t and e["state"] == "recovered"]


def recover_if_allowed(timeout=300.0):
    """Called when a connection was refused. True: the editor is back (or was never gone), retry. False: recovery was tried and failed.
    Raises ConnectionRefusedError (with a clear message) when the editor was closed on purpose."""
    marker = closed_on_purpose()
    if marker:
        raise EditorClosedOnPurpose(closed_message(marker))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bridge_watchdog
    rep = bridge_watchdog.recover(timeout)
    if rep["state"] == "failed":
        return False
    if rep["state"] == "recovered":
        record_recovery(rep)
    return True


def send(cmd, timeout=120.0):
    try:
        return _send_once(cmd, timeout)
    except EditorClosedOnPurpose:
        raise
    except ConnectionRefusedError as exc:
        if not _auto_recover():
            raise ConnectionRefusedError(refused_message()) from exc
        if not recover_if_allowed():
            raise ConnectionRefusedError(refused_message() + " (an automatic restart was tried and failed)") from exc
        return _send_once(cmd, timeout)
    except socket.timeout as exc:
        raise socket.timeout(timeout_message(timeout, _open_dialogs())) from exc


def _open_dialogs():
    """The open dialogs as short strings, read out-of-band (they have their own queue and answer while the main thread is blocked); [] when unknown."""
    try:
        out = json.loads(_send_once("bridge.dialog.list", 3.0).get("output") or "{}")
        return ["'%s' [%s]" % (d.get("title", "?"), "/".join(str(b.get("standard") or b.get("text")) for b in d.get("buttons", []))) for d in out.get("dialogs", [])][:5]
    except Exception:                                           # noqa: BLE001 - the editor may be too stuck to say
        return []


def _send_once(cmd, timeout):
    payload = (json.dumps({"id": 1, "cmd": cmd}) + "\n").encode("utf-8")
    with socket.create_connection((HOST, PORT), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(payload)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))


def run_source(source, timeout=120.0):
    """Returns {"ok", "log", "engine_log"}; "log" is the script's stdout+tracebacks."""
    b64 = base64.b64encode(source.encode("utf-8")).decode("ascii")
    resp = send("bridge.execute_python.b64 " + b64, timeout)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "bridge error"))
    out = resp.get("output", "")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": True, "log": out, "engine_log": ""}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    src = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    res = run_source(src)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(res.get("log", ""))
    if "--engine-log" in sys.argv:
        sys.stdout.write("\n--- engine_log ---\n" + res.get("engine_log", ""))


if __name__ == "__main__":
    main()
