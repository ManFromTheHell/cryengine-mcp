#!/usr/bin/env python3
"""Self-test of on-demand editor recovery (bridge_watchdog.recover, the run lock, bridge_py.send's retry). Editor-free: every call that would start or kill Sandbox is replaced by a recorder, and the clock is
fake, so it never starts, kills or waits for anything.

    test_watchdog_recover.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_watchdog as W
import bridge_py as B

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


class FakeTime:
    """a clock that only moves when somebody sleeps"""
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def strftime(self, fmt, t=None):
        return "00:00:00"

    def localtime(self, t=None):
        return None


def scenario(**kw):
    """run recover() with the world described by kw -> (result, calls). healthy_after: poll count at which the bridge answers (None = never)"""
    calls = []
    polls = {"n": 0}
    healthy_after = kw.get("healthy_after")
    state = {"running": kw.get("running", True), "answering": kw.get("answering", False)}

    def healthy():
        polls["n"] += 1
        if healthy_after is not None and polls["n"] > healthy_after:
            state["answering"] = True                          # a slow load finishes
        return state["answering"]

    saved = {k: getattr(W, k) for k in ("bridge_healthy", "sandbox_running", "launch", "kill", "crash_report_after", "sandbox_started", "deploy_in_progress", "editor_closing_job_running", "log", "time", "_trigger_watchdog_task", "level_loaded", "deliberately_closed", "paused")}

    def launch():
        calls.append("launch")
        state["running"] = True
        state["answering"] = True                               # a launched editor answers on the next probe

    def kill():
        calls.append("kill")
        state["running"] = False
        state["answering"] = False

    W.bridge_healthy = healthy
    W.sandbox_running = lambda: state["running"]
    W.launch = launch
    W.kill = kill
    levels = {"n": 0}

    def level():
        levels["n"] += 1
        return levels["n"] > kw.get("level_after", 0)
    W.level_loaded = level

    def trigger():
        calls.append("task")
        if kw.get("task_ok", True) and kw.get("task_effect", True):
            state["running"] = True                             # the scheduled task starts (or replaces) Sandbox outside the caller's process
            state["answering"] = True
        return kw.get("task_ok", True)
    W._trigger_watchdog_task = trigger
    W.crash_report_after = lambda started: 1234.0 if kw.get("crash") else None
    W.sandbox_started = lambda: 1.0
    W.deploy_in_progress = lambda: kw.get("deploy", False)
    W.editor_closing_job_running = lambda: kw.get("job", False)
    W.log = lambda m: None
    W.deliberately_closed = lambda: kw.get("marker")
    W.paused = lambda: kw.get("pause")
    W.time = FakeTime()
    try:
        res = W.recover(timeout=kw.get("timeout", 60.0))
    finally:
        for k, v in saved.items():
            setattr(W, k, v)
    return res, calls


def main():
    tmp = tempfile.mkdtemp(prefix="zz_mcp_rec_test_")
    old_lock = W.RUN_LOCK
    try:
        W.RUN_LOCK = os.path.join(tmp, "lock")
        check("lock: the first taker gets it, a second does not, after a drop the lock can be taken again", W._take_lock() and not W._take_lock() and (W._drop_lock() or True) and W._take_lock(), "")
        W._drop_lock()
        with open(W.RUN_LOCK, "w") as f:
            f.write("999")
        os.utime(W.RUN_LOCK, (1.0, 1.0))
        check("control: a lock older than 10 minutes belongs to a run that died and is taken over", W._take_lock(), "")
        W._drop_lock()

        res, calls = scenario(answering=True)
        check("recover: an editor that answers is left alone (state healthy, nothing launched or killed)", res["state"] == "healthy" and calls == [] and res["action"] == "none", (res, calls))
        res, calls = scenario(running=False)
        check("recover: no Sandbox process -> the SCHEDULED watchdog task is run once (never a direct launch from the tool's own process)", res["state"] == "recovered" and calls == ["task"] and "scheduled watchdog" in res["action"], (res, calls))
        res, calls = scenario(running=True, crash=True)
        check("recover: Sandbox running but silent AND an error.log written since it started (a crash) -> the scheduled task is run (it kills and replaces it); no direct kill", calls == ["task"] and res["state"] == "recovered", (res, calls))
        res, calls = scenario(running=False, level_after=3)
        check("recover: an editor that answers the bridge but has NO LEVEL yet is not 'recovered': it waits for the level (a tool that asked would find an empty editor)", res["state"] == "recovered" and res["seconds"] >= 10 and calls == ["task"], (res, calls))
        res, calls = scenario(running=False, level_after=10 ** 6, timeout=40.0)
        check("control: an editor that never gets a level is reported 'failed', not 'recovered'", res["state"] == "failed" and calls == ["task"], (res, calls))
        res, calls = scenario(running=False, task_ok=False)
        check("fallback: when the task cannot be run (disabled, deleted) the editor is launched directly and the answer says it lives only as long as the caller", calls == ["task", "launch"] and "directly" in res["action"] and res["state"] == "recovered", (res, calls))
        res, calls = scenario(running=True, crash=True, task_ok=False)
        check("fallback: a crashed editor and no task -> killed, then launched directly", calls == ["task", "kill", "launch"], (res, calls))
        res, calls = scenario(running=False, task_effect=False, timeout=130.0)
        check("control: a task that does not bring the editor up is re-run at most once a minute (2 or 3 runs in 130 s, never one per poll), then the answer is 'failed'", calls and set(calls) == {"task"} and 2 <= len(calls) <= 3 and res["state"] == "failed", (res, calls))
        res, calls = scenario(running=True, crash=False, healthy_after=3)
        check("control: running, silent, no crash report (a long load): NEVER killed or launched; recover waits and reports healthy (action none) when it answers by itself", calls == [] and res["state"] == "healthy" and res["action"] == "none" and res["seconds"] > 0, (res, calls))
        res, calls = scenario(running=True, crash=False, healthy_after=None, timeout=30.0)
        check("control: running and silent for the whole timeout, no crash: nothing is killed, the answer is 'failed' (the scheduled strikes decide a hang)", calls == [] and res["state"] == "failed", (res, calls))
        res, calls = scenario(running=False, deploy=True, healthy_after=None, timeout=30.0)
        check("control: a deploy in progress -> the editor is NOT launched under it", calls == [] and res["state"] == "failed" and "deploy" in res["action"], (res, calls))
        res, calls = scenario(running=False, job=True, healthy_after=None, timeout=30.0)
        check("control: a job that closes the editor on purpose is running -> the editor is NOT launched under it", calls == [] and res["state"] == "failed", (res, calls))

        # the lock held by someone else: wait, do not launch twice
        W._take_lock()
        res, calls = scenario(running=False, task_ok=False, timeout=20.0)
        W._drop_lock()
        check("control: while another recovery holds the lock the direct-launch fallback launches nothing (only the task was tried)", calls == ["task"] and res["state"] == "failed", (res, calls))

        # bridge_py.send retry
        seen = {"sends": 0, "recovers": 0}
        orig_once, orig_rec = B._send_once, W.recover

        def once(cmd, timeout):
            seen["sends"] += 1
            if seen["sends"] == 1:
                raise ConnectionRefusedError("refused")
            return {"ok": True, "output": "x"}

        def rec(timeout=300.0):
            seen["recovers"] += 1
            return {"state": "recovered", "action": "fake", "seconds": 1.0}
        B._send_once, W.recover = once, rec
        try:
            os.environ["CLAUDE_AUTO_RECOVER"] = "1"
            r = B.send("bridge.cvar_get x")
            check("send: with auto-recovery on, a refused connection triggers ONE recovery and ONE retry, and the retry's answer is returned", r == {"ok": True, "output": "x"} and seen == {"sends": 2, "recovers": 1}, seen)
            seen.update(sends=0, recovers=0)
            os.environ["CLAUDE_AUTO_RECOVER"] = "0"
            try:
                B.send("bridge.cvar_get x")
                raised = False
            except ConnectionRefusedError:
                raised = True
            check("control: with it off (scripts and tests) the refusal is raised unchanged and nothing is recovered", raised and seen == {"sends": 1, "recovers": 0}, seen)
            seen.update(sends=0, recovers=0)
            os.environ["CLAUDE_AUTO_RECOVER"] = "1"
            W.recover = lambda timeout=300.0: {"state": "failed", "action": "x", "seconds": 1.0}
            try:
                B.send("bridge.cvar_get x")
                raised = False
            except ConnectionRefusedError:
                raised = True
            check("control: when recovery fails the original refusal is raised (no endless retry)", raised and seen["sends"] == 1, seen)
        finally:
            B._send_once, W.recover = orig_once, orig_rec
            os.environ.pop("CLAUDE_AUTO_RECOVER", None)
        src = open(_paths.script("server.py"), encoding="utf-8").read()
        check("the MCP server reads the opt-in CLAUDE_AUTO_RECOVER (default off) and its own socket client retries after recover()", 'os.environ.setdefault("CLAUDE_AUTO_RECOVER", "0")' in src and ("bridge_watchdog.recover" in src or "recover_if_allowed" in src), "")
    finally:
        W.RUN_LOCK = old_lock
        shutil.rmtree(tmp, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
