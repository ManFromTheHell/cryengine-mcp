#!/usr/bin/env python3
"""ONE way to close the editor. Closing Sandbox with `taskkill /F /IM Sandbox.exe` loses whatever was not saved, and a graceful close SAVES the level, so a dirty level with scratch
objects in it is saved with them. Every script that closes the editor (the shader-cache refresh, the packaging, the release playthrough, the watchdog) therefore goes through this module,
which is the only place that decides.

    close_editor(reason)           save-and-close, gracefully, or REFUSE (never a taskkill of an editor whose state is unknown)
    release()                      the job is over: remove the "closed on purpose" marker so the watchdog may reopen the editor
    inspect(level)                 what the editor holds now: level, object count, scratch objects, open dialogs

REFUSES (returns closed=False with the reasons, touches nothing) when
  * the open level holds objects named zz_mcp_* / scratch* (a save would persist them);
  * the live object count differs from the latest snapshot's count for that level (+ the editor's usual offset, recorded at the last clean close) by more than `tolerance`:
    something was added or deleted that no snapshot knows about (pass accept_count_change=True after looking at diff_snapshot);
  * the editor is running but does not answer the bridge (its unsaved state is unknown): a kill is allowed only for an editor that CRASHED (error.log written after it started) or with kill_unresponsive=True.
Before saving it takes a snapshot of the files on disk (snapshot=True), so a bad save can be undone. After a graceful close fails to finish in `wait` seconds it kills the editor ONLY when the save
verifiably succeeded just before (print of general.save_level() == True and the object count unchanged); the result says so (killed_after_save).

On success it writes the "closed on purpose" marker (MARKER) and the name of the level that was open (LEVEL_FILE). bridge_watchdog.py reads both: it does not reopen an editor while the marker is fresh
(it is released by release(), by an editor that is running again, or after MAX_HOLD) and it reopens THAT level. A pause file (PAUSE_FILE) stops the watchdog altogether (see bridge_watchdog.py).

    safe_close.py status        what the editor holds, and what close_editor would do (changes nothing)
    safe_close.py close [--accept-count-change] [--no-snapshot] [--kill-unresponsive] [--hold-minutes N]
    safe_close.py release
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import base64
import json
import os
import re
import socket
import subprocess
import sys
import time

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# State folder for the marker / last-level / pause files: the shared workspace (_paths.WORKSPACE: CRYENGINE_WORKSPACE, else CRYENGINE_STATE_DIR / CLAUDE_STATE_DIR, else <repository>/state).
# CLAUDE_EDITOR_CLOSED_MARKER (also read by bridge_py) overrides the marker's full path; set the same variable (or the workspace variable) for every process that uses them.
SRC = _paths.WORKSPACE
MARKER = os.environ.get("CLAUDE_EDITOR_CLOSED_MARKER") or _paths.workspace("claude_editor_closed")
LEVEL_FILE = _paths.workspace("claude_editor_last_level")
PAUSE_FILE = _paths.workspace("claude_watchdog_pause")
BASELINE = os.path.join(HERE, "safe_close_baseline.json")
HOST, PORT = "127.0.0.1", 37778
MAX_HOLD = 6 * 3600.0
SCRATCH = re.compile(r"^(zz_mcp_|scratch)", re.I)
OBJECT = re.compile(rb"<Object\s")
NAME_ATTR = re.compile(rb'<Object\s[^>]*?\bName="([^"]*)"')

sys.path.insert(0, HERE)


class CloseRefused(RuntimeError):
    """close_editor refused; .result is the full report"""
    def __init__(self, result):
        RuntimeError.__init__(self, "; ".join(result.get("refused") or ["the editor was not closed"]))
        self.result = result


# ------------------------------------------------------------------------------------------------------------------------ the real world (replaced by tests)
class Env:
    """Everything close_editor touches outside itself. Tests subclass it and record instead of acting."""
    def running(self):
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return "Sandbox.exe" in r.stdout

    def bridge(self, cmd, timeout=20.0):
        """one bridge command -> the response dict; raises on a connection or protocol failure"""
        with socket.create_connection((HOST, PORT), timeout) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps({"id": 1, "cmd": cmd}) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))

    def python(self, source, timeout=60.0):
        """run Python source inside the editor -> its printed text"""
        resp = self.bridge("bridge.execute_python.b64 " + base64.b64encode(source.encode("utf-8")).decode("ascii"), timeout)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "bridge error"))
        out = resp.get("output", "")
        try:
            return json.loads(out).get("log", "")
        except ValueError:
            return out

    def crashed(self):
        try:
            import bridge_watchdog
            return bridge_watchdog.crash_report_after(bridge_watchdog.sandbox_started()) is not None
        except Exception:                                         # noqa: BLE001
            return False

    def snapshot(self, label):
        import snapshot
        return snapshot.snapshot(label=label, note="taken by safe_close before saving and closing the editor")["id"]

    def expected_objects(self, level):
        return snapshot_object_count(level)

    def _bridge_pid(self):
        """the PID listening on the bridge port (the editor this module talks to), or None"""
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        except OSError:
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(":%d" % PORT) and parts[4].isdigit():
                return int(parts[4])
        return None

    def kill(self):
        pid = self._bridge_pid()
        if pid:                                                   # only the editor that owns the bridge port, not every Sandbox.exe on the machine
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, stdin=subprocess.DEVNULL)
            return
        listing = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Sandbox.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        if listing.lower().count("sandbox.exe") == 1:             # no listener found: fall back to the image name only when there is exactly one editor
            subprocess.run(["taskkill", "/F", "/IM", "Sandbox.exe"], capture_output=True, stdin=subprocess.DEVNULL)

    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)


# ------------------------------------------------------------------------------------------------------------------------ what the editor holds
def inspect(env=None):
    """-> {"running", "answering", "level", "objects", "scratch": [names], "dialogs": [titles], "listed": n}. Nothing is changed."""
    env = env or Env()
    st = {"running": env.running(), "answering": False, "level": None, "objects": None, "scratch": [], "dialogs": [], "listed": 0}
    if not st["running"]:
        return st
    try:
        text = env.python("print(general.get_current_level_name())\nprint(general.get_objects_count())", 30.0).splitlines()
        st["answering"] = True
        st["level"] = text[0].strip() if text else None
        st["objects"] = int(text[1]) if len(text) > 1 and text[1].strip().lstrip("-").isdigit() else None
    except Exception as exc:                                      # noqa: BLE001
        st["error"] = str(exc)[:200]
        return st
    try:
        objs = json.loads(env.bridge("bridge.object.list", 60.0).get("output") or "[]")
        st["listed"] = len(objs)
        names = [(o.get("name") if isinstance(o, dict) else str(o)) or "" for o in objs]
        st["scratch"] = sorted(n for n in names if SCRATCH.match(n))[:20]
    except Exception as exc:                                      # noqa: BLE001 - a list that cannot be read cannot clear the level: say so
        st["list_error"] = str(exc)[:200]
    try:
        d = json.loads(env.bridge("bridge.dialog.list", 20.0).get("output") or "{}")
        st["dialogs"] = [x.get("title", "") for x in d.get("dialogs", [])]
    except Exception:                                             # noqa: BLE001
        pass
    return st


def lyr_objects(folder):
    """-> (object count, [names]) of every .lyr under a level folder (the saved state)"""
    n, names = 0, []
    for d, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".lyr"):
                with open(os.path.join(d, f), "rb") as fh:
                    data = fh.read()
                n += len(OBJECT.findall(data))
                names += [m.decode("utf-8", "replace") for m in NAME_ATTR.findall(data)]
    return n, names


def scratch_on_disk(project=PROJECT):
    """-> {level: [scratch names]} saved in the level layer files (they would ship)"""
    root = os.path.join(project, "Assets", "levels")
    out = {}
    if os.path.isdir(root):
        for lv in sorted(os.listdir(root)):
            _, names = lyr_objects(os.path.join(root, lv))
            bad = sorted(n for n in names if SCRATCH.match(n))
            if bad:
                out[lv] = bad[:20]
    return out


def snapshot_object_count(level, snap="latest"):
    """The object count of a level's layer files in a snapshot (default the latest), or None when the snapshot holds none of them"""
    import snapshot
    man = snapshot.load(snap)
    prefix = "project/assets/levels/%s/" % level.lower()
    n, seen = 0, False
    for key, v in man["files"].items():
        if key.lower().startswith(prefix) and key.lower().endswith(".lyr"):
            seen = True
            with open(snapshot._blob_path(v["sha"]), "rb") as fh:
                n += len(OBJECT.findall(fh.read()))
    return n if seen else None


def load_baseline():
    try:
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_baseline(level, offset, path=None):
    data = load_baseline() if path is None else {}
    data[level] = {"offset": offset, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(path or BASELINE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


# ------------------------------------------------------------------------------------------------------------------------ the decision
def reasons_to_refuse(st, expected, offset, tolerance=0, accept_count_change=False, kill_unresponsive=False, crashed=False, will_snapshot=False):
    """-> list of reasons NOT to close (empty = the close may go on). expected: the latest snapshot's object count for the level (None: no snapshot of it); offset: live count - layer-file count at a
    clean close (None: never calibrated)."""
    why = []
    if not st["running"]:
        return why
    if not st["answering"]:
        if not (crashed or kill_unresponsive):
            why.append("the editor is running but does not answer the bridge, and no crash report says it died: its unsaved state is unknown, so it is NOT killed (a crashed editor, or kill_unresponsive=True, may be)")
        return why
    if st["scratch"]:
        why.append("the open level holds scratch objects (%s): a save would persist them; delete them first" % ", ".join(st["scratch"][:6]))
    if st.get("list_error") and st["objects"] and st["listed"] == 0:
        why.append("the object list could not be read (%s): scratch objects cannot be ruled out" % st["list_error"])
    if expected is None and will_snapshot:
        pass                                                      # close_editor takes the snapshot itself before saving; the object-count check needs an earlier one and is skipped
    elif expected is None:
        why.append("no snapshot holds the layers of level %r: take one first (snapshot_project), a close would save a level nothing can restore" % st["level"])
    elif st["objects"] is not None and not accept_count_change:
        want = expected + (offset or 0)
        if abs(st["objects"] - want) > tolerance:
            why.append("the editor holds %d objects, the latest snapshot says %d%s (%+d): objects were added or deleted since, or the level is dirty. Look at diff_snapshot, take a snapshot "
                       "(snapshot_project) and retry, or pass accept_count_change=True"
                       % (st["objects"], want, " (%d layer objects %+d usual offset)" % (expected, offset) if offset else "", st["objects"] - want))
    return why


def close_editor(reason="", snapshot=True, accept_count_change=False, kill_unresponsive=False, hold_minutes=240, tolerance=0, wait=120.0, env=None, out=print,
                 marker=MARKER, level_file=LEVEL_FILE, baseline_path=None, owned=True):
    """Save the open level and close the editor, or refuse. -> {"ok", "closed", "was_running", "refused": [...], "level", "objects", "snapshot", "killed_after_save", "saved"}"""
    env = env or Env()
    res = {"ok": False, "closed": False, "was_running": False, "refused": [], "level": None, "objects": None, "snapshot": None, "killed_after_save": False, "saved": None, "reason": reason}
    st = inspect(env)
    res.update(was_running=st["running"], level=st["level"], objects=st["objects"])
    if not st["running"]:
        out("safe_close: the editor is not running")
        res.update(ok=True, closed=True)
        return res
    baseline_all = load_baseline() if baseline_path is None else _load(baseline_path)
    expected = None
    if st["level"]:
        try:
            expected = env.expected_objects(st["level"])
        except Exception as exc:                                  # noqa: BLE001
            out("safe_close: could not read the latest snapshot: %s" % exc)
    offset = (baseline_all.get(st["level"] or "", {}) or {}).get("offset")
    why = reasons_to_refuse(st, expected, offset, tolerance, accept_count_change, kill_unresponsive, env.crashed() if not st["answering"] else False, will_snapshot=snapshot)
    if expected is None and snapshot and not why:
        out("safe_close: no earlier snapshot of the level: the object-count check is skipped (a snapshot is taken before the save)")
    if why:
        res["refused"] = why
        for w in why:
            out("safe_close REFUSED: " + w)
        return res
    if not st["answering"]:                                       # crashed / explicitly allowed: nothing can be saved, the process is gone or hopeless
        out("safe_close: the editor does not answer (%s): killing it" % ("crashed" if env.crashed() else "kill_unresponsive"))
        env.kill()
        env.sleep(3)
        res.update(ok=True, closed=not env.running(), killed_after_save=False)
        res["marker_written"] = _write_marker(env, marker, level_file, st, reason, hold_minutes, owned)
        if not res["marker_written"]:
            out("safe_close WARNING: the closed-on-purpose marker could not be written to %s: a watchdog may reopen the editor" % marker)
        return res
    if snapshot:
        try:
            res["snapshot"] = env.snapshot("before_safe_close")
            out("safe_close: snapshot %s taken (files on disk, before the save)" % res["snapshot"])
        except Exception as exc:                                  # noqa: BLE001 - no snapshot, no save: the whole point is being able to undo it
            res["refused"] = ["the snapshot before the save failed (%s): not saving or closing" % str(exc)[:200]]
            out("safe_close REFUSED: " + res["refused"][0])
            return res
    try:
        saved = env.python("print(general.save_level())", 180.0).strip().splitlines()[-1:] == ["True"]
    except Exception as exc:                                      # noqa: BLE001
        saved = False
        out("safe_close: save failed: %s" % exc)
    res["saved"] = saved
    out("safe_close: save_level -> %s (%s, %s objects)" % (saved, st["level"], st["objects"]))
    if not saved:
        res["refused"] = ["general.save_level() did not return True: the level is not known to be saved, so the editor is not closed"]
        return res
    try:
        r = env.bridge("bridge.window.close", 30.0)
        out("safe_close: window.close ok=%s" % r.get("ok"))
    except Exception as exc:                                      # noqa: BLE001
        out("safe_close: window.close failed: %s" % exc)
    t0 = env.now()
    while env.running() and env.now() - t0 < wait:
        env.sleep(3)
        if env.running():
            try:
                lst = json.loads(env.bridge("bridge.dialog.list", 15.0).get("output") or "{}")
                for d in lst.get("dialogs", []):
                    r = env.bridge("bridge.dialog.click default %s" % d["title"][:40], 20.0)
                    out("   answered '%s': %s" % (d["title"], str(r.get("output") or r.get("error"))[:120]))
            except Exception:                                     # noqa: BLE001
                pass
    if env.running():
        # the level was saved a moment ago and the count is unchanged: nothing unsaved is lost by ending a hung process
        try:
            again = int(env.python("print(general.get_objects_count())", 15.0).strip().splitlines()[-1])
        except Exception:                                         # noqa: BLE001
            again = None
        if again is not None and again == st["objects"]:
            out("safe_close: still running after %.0f s; the save was verified and the object count is unchanged: ending the process" % (env.now() - t0))
            env.kill()
            env.sleep(5)
            res["killed_after_save"] = True
        else:
            res["refused"] = ["the editor did not close in %.0f s and the object count is %s (was %s): NOT killed (it may hold unsaved work). Look at list_dialogs" % (wait, again, st["objects"])]
            out("safe_close: " + res["refused"][0])
            return res
    res["closed"] = not env.running()
    res["ok"] = res["closed"]
    if res["ok"]:
        res["marker_written"] = _write_marker(env, marker, level_file, st, reason, hold_minutes, owned)
        if not res["marker_written"]:
            out("safe_close WARNING: the closed-on-purpose marker could not be written to %s: a watchdog may reopen the editor" % marker)
        try:                                                      # the editor's usual offset between its count and the layer files: recorded at a clean close, used by the next one
            folder = os.path.join(PROJECT, "Assets", "levels", st["level"]) if st["level"] else None
            n = lyr_objects(folder)[0] if folder and os.path.isdir(folder) else None      # no layer files = nothing to calibrate against (a count of 0 would record the whole level as "offset")
            if n is not None and st["objects"] is not None:
                save_baseline(st["level"], st["objects"] - n, baseline_path)
        except Exception:                                         # noqa: BLE001
            pass
        out("safe_close: editor closed (level %s, %s objects); marker written, the watchdog will not reopen it until release()" % (st["level"], st["objects"]))
    return res


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_marker(env, marker, level_file, st, reason, hold_minutes, owned):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(marker)), exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(level_file)), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"closed_at": env.now(), "until": env.now() + min(float(hold_minutes) * 60.0, MAX_HOLD), "level": st.get("level"), "objects": st.get("objects"),
                       "reason": reason, "pid": os.getpid(), "owned": bool(owned)}, f)
        if st.get("level"):
            with open(level_file, "w", encoding="utf-8") as f:
                f.write(st["level"])
        return True
    except OSError:
        return False


def release(marker=MARKER):
    """The job that closed the editor is over: remove the marker, so the watchdog's next cycle reopens the editor (and the level that was open)"""
    try:
        os.remove(marker)
        return True
    except OSError:
        return False


def marker_state(marker=MARKER, now=None):
    """-> None (no marker or an expired one) or the marker's dict. Used by bridge_watchdog."""
    now = time.time() if now is None else now
    try:
        with open(marker, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        if os.path.exists(marker):                                # an unreadable marker still means "closed on purpose": honour it for MAX_HOLD from its mtime
            return {"until": os.path.getmtime(marker) + MAX_HOLD, "level": None} if os.path.getmtime(marker) + MAX_HOLD > now else None
        return None
    until = float(data.get("until") or 0)
    void = until <= now
    # a marker written by a script that has since ended (owned=True: final_packages, a test) must not keep the editor closed: two minutes after its owner is gone it is void
    if not void and data.get("owned") and data.get("pid") and now - float(data.get("closed_at") or 0) > 120 and not pid_alive(int(data["pid"])):
        void = True
    if void:
        try:                                                      # other readers (bridge_py.closed_on_purpose) only test that the file exists: a void marker is removed, not just ignored
            os.remove(marker)
        except OSError:
            pass
        return None
    return data


def pid_alive(pid):
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
        return ('"%d"' % pid) in r.stdout
    except Exception:                                             # noqa: BLE001 - cannot tell: assume alive (the marker is then honoured, the safe side)
        return True


def last_level(level_file=LEVEL_FILE):
    try:
        with open(level_file, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def pause_state(pause_file=PAUSE_FILE, now=None):
    """-> None (watchdog not paused) or a text saying why/until when. The file may be empty (paused until deleted), hold an epoch time, or JSON {"until": epoch, "why": text}."""
    now = time.time() if now is None else now
    if not os.path.exists(pause_file):
        return None
    try:
        text = open(pause_file, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return "paused (file unreadable)"
    until, why = None, text[:80]
    if text.startswith("{"):
        try:
            d = json.loads(text)
            until, why = d.get("until"), str(d.get("why", ""))[:80]
        except ValueError:
            pass
    elif re.match(r"^\d+(\.\d+)?$", text):
        until, why = float(text), ""
    if until is not None and float(until) <= now:
        return None
    return "paused%s%s" % (" until %s" % time.strftime("%H:%M:%S", time.localtime(float(until))) if until else " until the file is deleted", (": " + why) if why else "")


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "release":
        print("marker removed" if release() else "no marker")
        return 0
    if cmd == "status":
        env = Env()
        st = inspect(env)
        exp = None
        try:
            exp = env.expected_objects(st["level"]) if st["level"] else None
        except Exception as exc:                                  # noqa: BLE001
            print("snapshot count unavailable: %s" % exc)
        off = (load_baseline().get(st["level"] or "", {}) or {}).get("offset")
        print(json.dumps({"editor": st, "latest_snapshot_objects": exp, "usual_offset": off, "would_refuse": reasons_to_refuse(st, exp, off), "marker": marker_state(), "pause": pause_state(),
                          "scratch_saved_on_disk": scratch_on_disk()}, indent=1))
        return 0
    if cmd == "close":
        hold = int(a[a.index("--hold-minutes") + 1]) if "--hold-minutes" in a else 240
        r = close_editor("cli", snapshot="--no-snapshot" not in a, accept_count_change="--accept-count-change" in a, kill_unresponsive="--kill-unresponsive" in a, hold_minutes=hold, owned=False)
        print(json.dumps(r, indent=1))
        return 0 if r["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
