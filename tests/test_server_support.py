#!/usr/bin/env python3
"""Self-test of server_support.py, catalog_stamps.py and the bridge_py / server.py parts built on them (2026-10-23, tasks T-03 T-04 T-05 T-10 T-11 T-12 T-13 T-14 T-15 T-16 T-20 T-33).
Editor-free: the editor is a fake object or a local socket; nothing is launched, closed, loaded or saved. About 10 s.

    CLAUDE_AUTO_RECOVER=0 python tests/test_server_support.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import asyncio
import base64
import inspect
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

os.environ["CLAUDE_AUTO_RECOVER"] = "0"                       # this test must never be able to relaunch the editor
HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bridge_py
import catalog_stamps as CS
import editor_api
import server_support as SS

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("  %s %-100s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:
        return str(e)
    return None


class FakeIO:
    """The editor, as load_level_logic and export_logic see it."""
    def __init__(self, **kw):
        self.t = 0.0
        self.levels_ = kw.get("levels", ["ashvale", "other"])
        self.open_ = kw.get("open", "ashvale")
        self.save_ok = kw.get("save_ok", True)
        self.dialog_after = kw.get("dialog", None)
        self.load_after = kw.get("load_after", 2)               # polls before the level is open
        self.log = kw.get("log", "")
        self.polls = 0
        self.sent = []
        self.nav_blocks = kw.get("nav_blocks", 0)                # export: attempts that raise the navigation dialog
        self.attempt = 0
        self.dlg = []
        self.clicked = []

    clock = lambda self: self.t

    def sleep(self, s):
        self.t += s

    def level_exists(self, n):
        return n in self.levels_

    def levels(self):
        return list(self.levels_)

    def current(self):
        self.polls += 1
        if self.sent and self.polls > self.load_after:
            self.open_ = self.target
        return {"name": self.open_, "objects": 12}

    def save(self):
        return {"saved": self.save_ok}

    def send(self, cmd, timeout):
        self.sent.append(cmd)
        if cmd.startswith("bridge.execute_command asset.edit"):
            self.target = cmd.split("levels/")[1].split("/")[0]
            return {"ok": True, "output": json.dumps({"ok": True, "log": self.log})}
        if cmd.endswith("exporter.export_to_engine"):
            self.attempt += 1
            if self.attempt <= self.nav_blocks:
                self.dlg = [{"title": "Navigation System Warning", "buttons": ["Yes", "Cancel"]}]
                self.t += 4                                      # the dialog is up for a while, then Cancel (the default) closes it
                self.dlg_closes_at = self.t + 2
                return {"ok": True, "output": "export cancelled"}
            return {"ok": True, "output": "Export successful"}
        return {"ok": True, "output": "{}"}

    def dialogs(self):
        if self.dialog_after is not None and self.polls >= self.dialog_after:
            return [{"title": "Save Level?", "buttons": ["Yes", "No"]}]
        return list(self.dlg)

    def click_default(self, title):
        self.clicked.append(title)
        self.dlg = []

    def start(self, fn):
        fn()                                                     # synchronous: the fake export answers at once, then "runs" for two more polls so the dialog can be seen
        left = [2]

        def alive():
            left[0] -= 1
            return left[0] >= 0

        class H:
            pass
        H.alive = staticmethod(alive)
        H.join = staticmethod(lambda t=None: None)
        return H()


def main():
    tmp = tempfile.mkdtemp(prefix="ss_test_")
    try:
        # --- bounded output (T-03)
        small = {"ok": True, "n": 3}
        check("a small result is returned as it is", SS.bound("t", small, out_dir=tmp) is small and not os.listdir(tmp))
        big = {"ok": True, "summary": "all good", "rows": [{"name": "row%d" % i, "pad": "x" * 100} for i in range(400)], "text": "line\n" * 6000}
        b = SS.bound("big_tool", big, out_dir=tmp)
        meta = b["_output"]
        check("a big dict becomes a summary: small values kept, big ones described, counts, id and path", b["ok"] is True and b["summary"] == "all good" and b["rows"]["count"] == 400
              and meta["output_truncated"] and meta["counts"]["rows"] == 400 and os.path.isfile(meta["path"]) and SS.json_size(b) < 9500, (SS.json_size(b), list(meta)))
        check("the summary shows what a row looks like (its scalar fields), not just that it exists", b["rows"]["first"]["name"] == "row0", b["rows"]["first"])
        oid = meta["output_id"]
        p1 = SS.read_output(oid, "rows", 0, 50, out_dir=tmp)
        check("read_output pages a list: 50 items, next_offset 50, total 400", p1["returned"] == 50 and p1["next_offset"] == 50 and p1["total"] == 400 and p1["items"][0]["name"] == "row0", (p1["returned"], p1["next_offset"]))
        last = SS.read_output(oid, "rows", 390, 50, out_dir=tmp)
        check("the last page ends: 10 items, next_offset null", last["returned"] == 10 and last["next_offset"] is None)
        check("a deeper key reaches one item", SS.read_output(oid, "rows.7.name", out_dir=tmp)["value"] == "row7")
        tp = SS.read_output(oid, "text", 0, 100, out_dir=tmp)
        check("a long text is paged by lines", tp["type"] == "text" and tp["returned"] == 100 and tp["total"] == 6000 and tp["next_offset"] == 100)
        check("a page never exceeds the budget even when the items are large", SS.json_size(SS.read_output(SS.store_output("w", {"v": [{"k": "y" * 3000}] * 100}, tmp)[0], "v", 0, 100, out_dir=tmp)) < 20000)
        check("a missing key names the keys that exist", "keys are" in (raises(lambda: SS.read_output(oid, "nope", out_dir=tmp)) or ""))
        check("an id that is a path is refused", raises(lambda: SS.read_output("../../x", out_dir=tmp)) is not None and raises(lambda: SS.read_output("out_20260101_000000_x_abcdef", out_dir=tmp)) is not None)
        check("limit 0 and a negative offset are refused", raises(lambda: SS.read_output(oid, "", 0, 0, out_dir=tmp)) and raises(lambda: SS.read_output(oid, "", -1, 5, out_dir=tmp)))
        lst = SS.bound("l", [{"id": i, "pad": "z" * 500} for i in range(100)], out_dir=tmp)
        check("a big list becomes a one-item list with the stand-in (same kind of answer)", isinstance(lst, list) and len(lst) == 1 and lst[0]["_output"]["count"] == 100 and lst[0]["first_items"][0]["scalars"]["id"] == 0)
        txt = SS.bound("s", "abc\n" * 10000, out_dir=tmp)
        check("a big text keeps its head and names the saved file", isinstance(txt, str) and txt.startswith("abc") and "read_output(id='out_" in txt and len(txt) < 3600)
        class Image:
            pass
        pics = [Image()] * 3 + ["x" * 50000]
        check("a result with pictures is left alone", SS.bound("p", pics, out_dir=tmp) is pics)
        check("the budget is per tool: tool_guide may be bigger (a map must be seen whole), the others are bounded at 20,000", SS.BUDGET_OVERRIDE["tool_guide"] > 20000 and SS.OUTPUT_BUDGET == 20000)
        # --- paging of list tools (T-13)
        rows = list(range(200))
        pg = SS.page(rows, 100, 0, cap=200)
        check("page: 200 rows from a bridge capped at 200: truncated, total is a lower bound, next_offset 100, the cap is explained", pg["truncated"] and pg["total"] == 200 and pg["total_is_lower_bound"] and pg["next_offset"] == 100 and "at most 200" in pg["note"], pg["note"])
        pg2 = SS.page(rows, 100, 100, cap=200)
        check("page: second page, no more pages but still truncated by the bridge's cap", pg2["shown"] == 100 and pg2["next_offset"] is None and pg2["truncated"])
        pg3 = SS.page(list(range(30)), 100, 0, cap=200)
        check("page: 30 rows under the cap are complete (truncated false, exact total)", not pg3["truncated"] and pg3["total"] == 30 and not pg3["total_is_lower_bound"] and pg3["next_offset"] is None)
        pg4 = SS.page(list(range(500)), 100, 0, cap=500, total=500)
        check("page: a known total equal to the cap is not truncated by the cap", pg4["next_offset"] == 100 and pg4["total"] == 500 and not pg4["total_is_lower_bound"])
        check("page: a known total beyond the cap says truncated", SS.page(list(range(500)), 500, 0, cap=500, total=900)["truncated"])
        check("page: limit 0 / offset -1 are refused", raises(lambda: SS.page(rows, 0), ValueError) is not None and raises(lambda: SS.page(rows, 10, -1), ValueError) is not None)

        # --- the danger deny-list (T-10) and the modal flags
        if os.path.isfile(editor_api.DUMP):
            den = editor_api.denied()
            modal_rows = [r for r in editor_api.catalog() if r["modal"]]
            check("every function the catalog marks modal is refused by check_python without really (%d of them)" % len(modal_rows), all(SS.check_python("%s()" % r["name"]) for r in modal_rows), len(modal_rows))
            check("every one is refused as an editor command line too", all(SS.check_editor_command(r["name"] + " a b") for r in modal_rows))
            boxes = [r["name"] for r in editor_api.catalog() if "box" in r["description"].lower() and r["description"].lower().startswith("shows")]
            check("the box functions (message_box*, edit_box*, combo_box, open_file_box) are flagged modal (they were not)", len(boxes) >= 7 and all(next(r for r in editor_api.catalog() if r["name"] == n)["modal"] for n in boxes), boxes)
            raw = json.load(open(editor_api.DUMP, encoding="utf-8"))
            check("every DANGEROUS and KNOWN_MODAL name exists in the editor dump (no typo)", all(n.split(".")[1] in raw.get(n.split(".")[0], {}) for n in list(editor_api.DANGEROUS) + sorted(editor_api.KNOWN_MODAL)), [n for n in list(editor_api.DANGEROUS) + sorted(editor_api.KNOWN_MODAL) if n.split(".")[1] not in raw.get(n.split(".")[0], {})])
            check("general.exit, unload_all_plugins, run_lua are refused; the dangerous flag is on the catalog rows", all(SS.check_python("general.%s()" % f) for f in ("exit", "unload_all_plugins", "run_lua")) and next(r for r in editor_api.catalog() if r["name"] == "general.exit")["dangerous"])
        else:
            print("  --   SKIPPED: %s is not generated (the editor_api dump: run it with the editor open), so the catalog-based deny-list checks are not run" % editor_api.DUMP)
        check("control: ordinary calls pass (save_level, get_current_level_name, a loop, open_pane)", not SS.check_python("print(general.get_current_level_name())\nfor n in names:\n    object.get_position(n)\ngeneral.save_level()\ngeneral.open_pane('Track View')"))
        check("a program run / file delete / interpreter exit is refused", all(SS.check_python(c) for c in ("import os; os.system('x')", "subprocess.run(['a'])", "shutil.rmtree('x')", "os.remove('f')", "sys.exit(1)", "getattr(general, 'exit')()")))
        check("console: quit, exit, bridge.window.close are refused; cvar and goto are not", all(SS.check_console(c) for c in ("quit", "exit", "bridge.window.close", "bridge.window.close Sandbox")) and not SS.check_console("r_DisplayInfo 1") and not SS.check_console("bridge.cvar_get x"))
        check("console: the passthroughs are looked into (a command, a script, a base64 script)", SS.check_console("bridge.execute_command general.exit") and SS.check_console("bridge.execute_python general.run_lua('x')")
              and SS.check_console("bridge.execute_python.b64 " + base64.b64encode(b"general.exit()").decode()) and not SS.check_console("bridge.execute_command asset.edit levels/a/a.level"))
        ref = SS.refuse_danger(["general.exit (quits the editor)"], "this Python")
        check("the refusal is a refusal dict that says really=true", ref["ok"] is False and "really=true" in ref["refused"])

        # --- messages that say what to do (T-12) and the deliberate-close marker (T-14)
        check("connection refused says wait_for_editor / health_check", "wait_for_editor" in bridge_py.refused_message() and "health_check" in bridge_py.refused_message())
        tm = bridge_py.timeout_message(25, ["'Save Level?' [Yes/No]"])
        check("a timeout says a modal dialog may be open, names list_dialogs / click_dialog_button, and attaches the open dialogs", "list_dialogs" in tm and "click_dialog_button" in tm and "Save Level?" in tm)
        marker = os.path.join(tmp, "claude_editor_closed")
        old_marker = bridge_py.EDITOR_CLOSED_MARKER
        bridge_py.EDITOR_CLOSED_MARKER = marker
        try:
            check("no marker: closed_on_purpose is None", bridge_py.closed_on_purpose() is None)
            open(marker, "w").write("x")
            check("marker present: closed_on_purpose names it", bridge_py.closed_on_purpose() == marker)
            fake = type(sys)("bridge_watchdog")
            called = []
            fake.recover = lambda timeout=300: called.append(1) or {"state": "recovered", "action": "fake", "seconds": 0}
            saved_mod = sys.modules.get("bridge_watchdog")
            sys.modules["bridge_watchdog"] = fake
            try:
                msg = raises(bridge_py.recover_if_allowed, bridge_py.EditorClosedOnPurpose)
                check("with the marker, recovery REFUSES and the watchdog's recover() is never called", msg and "ON PURPOSE" in msg and not called, msg)
                os.remove(marker)
                n0 = len(bridge_py.RECOVERY_EVENTS)
                check("without the marker, recovery runs and a 'recovered' state is recorded", bridge_py.recover_if_allowed() is True and called == [1] and len(bridge_py.RECOVERY_EVENTS) == n0 + 1)
            finally:
                if saved_mod is not None:
                    sys.modules["bridge_watchdog"] = saved_mod
                else:
                    sys.modules.pop("bridge_watchdog", None)
            t0 = time.time() - 1
            check("recoveries_since sees it, and attach_recovery adds editor_relaunched to a dict, a list and a string",
                  len(bridge_py.recoveries_since(t0)) >= 1 and SS.attach_recovery({"a": 1}, t0)["editor_relaunched"] is True and SS.attach_recovery([1], t0)[-1]["editor_relaunched"]
                  and "editor_relaunched: true" in SS.attach_recovery("x", t0) and SS.attach_recovery({"a": 1}, time.time() + 5) == {"a": 1})
        finally:
            bridge_py.EDITOR_CLOSED_MARKER = old_marker
            bridge_py.RECOVERY_EVENTS.clear()

        # --- load_level logic (T-11)
        io = FakeIO(load_after=4, log="loading\nWarning: a thing\nwarning two\nok")
        r = SS.load_level_logic("other", True, io)
        check("load_level: waits for the level, returns ok, level, seconds, n_warnings (2) and the warning lines", r["ok"] and r["level"] == "other" and r["n_warnings"] == 2 and r["seconds"] >= 2 and r["saved_previous"]["saved"] and "asset.edit levels/other/other.level" in io.sent[0], r)
        check("load_level: a bad name and a missing level are refused (the missing one lists the real levels)", SS.load_level_logic("../x", True, FakeIO())["ok"] is False and "ashvale, other" in SS.load_level_logic("nolevel", True, FakeIO())["refused"])
        io = FakeIO(save_ok=False)
        r = SS.load_level_logic("other", True, io)
        check("load_level: a FAILED save is reported (save_failed) and the level is not left; nothing was sent", r["ok"] is False and r["save_failed"] and not io.sent, r.get("refused", "")[:60])
        io = FakeIO(save_ok=False)
        check("load_level: save_current=false skips the save and loads", SS.load_level_logic("other", False, io)["ok"] is True and not io.sent == [])
        io = FakeIO(dialog=1)
        r = SS.load_level_logic("other", True, io)
        check("load_level: an open dialog is reported with what to click, not waited out", r["ok"] is False and r["dialog_open"][0]["title"] == "Save Level?" and "click_dialog_button" in r["next"], r.get("seconds"))
        io = FakeIO(load_after=10 ** 6)
        r = SS.load_level_logic("other", True, io, wait_s=20)
        check("load_level: a level that never opens times out with ok false and says what is open", r["ok"] is False and r["now_open"] == "ashvale" and r["seconds"] >= 20, r.get("refused", "")[:70])
        check("load_level: the level that is already open is not reloaded", SS.load_level_logic("ashvale", True, FakeIO())["already_open"] is True)

        # --- export logic (T-16)
        io = FakeIO()
        r = SS.export_logic(io, 120, True, 3)
        check("export: ai.regenerate_ignored first, then the export; success on attempt 1", r["ok"] and r["attempts"] == 1 and io.sent[0].endswith("ai.regenerate_ignored") and io.sent[1].endswith("exporter.export_to_engine"), io.sent)
        io = FakeIO(nav_blocks=1)
        r = SS.export_logic(io, 120, True, 3)
        check("export: a Navigation System Warning is answered with its default button and the export retried", r["ok"] and r["attempts"] == 2 and io.clicked == ["Navigation System Warning"] and r["dialogs_seen"][0]["title"] == "Navigation System Warning", (r["attempts"], io.clicked))
        io = FakeIO(nav_blocks=5)
        r = SS.export_logic(io, 120, True, 2)
        check("export: navigation still processing after the attempts is ok false with a next step", r["ok"] is False and r["attempts"] == 2 and "next" in r)
        io = FakeIO(nav_blocks=1)
        r = SS.export_logic(io, 120, False, 3)
        check("export with answer_dialogs=false answers nothing and reports the dialog", io.clicked == [] and r["dialogs_seen"] and r["ok"] is False)

        # --- component_properties persist (T-15)
        def ent(props_xml):
            return [{"name": "Villager1", "cls": "X", "layer": "Main", "file": "main.lyr", "legacy": False,
                     "components": [{"type": "CNpcComponent", "props": ET.fromstring(props_xml)}]}]
        e1 = ent('<properties PatrolPath="VillagerRoute" Speed="1.5"><Movement Inertia="10"/></properties>')
        ok = SS.verify_persisted(e1, "Villager1", "cnpccomponent", {"PatrolPath": "VillagerRoute", "Speed": 1.5, "Movement": {"Inertia": 10}})
        check("persist: values present in the layer file verify (case-insensitive component, numbers compared as numbers, nested)", ok["ok"] and ok["checked"] == 3 and ok["where"] == "main.lyr", ok)
        bad = SS.verify_persisted(e1, "Villager1", "CNpcComponent", {"PatrolPath": "Other", "Nope": 1})
        check("persist: a different value and a missing one are listed", not bad["ok"] and bad["mismatched"] and bad["missing"] == ["Nope"], bad)
        check("persist: an unknown entity or component is explained", "no entity" in SS.verify_persisted(e1, "Zed", "C", {"a": 1})["problem"] and "lists no" in SS.verify_persisted(e1, "Villager1", "COther", {"a": 1})["problem"])

        # --- server_info / stale modules (T-05)
        mod = os.path.join(tmp, "m1.py")
        open(mod, "w").write("x=1\n")
        time.sleep(0.05)      # the file system's timestamp is coarser than time.time(): without this the file could look newer than `start` (flaked once, 2026-10-25)
        start = time.time()
        snap = {mod: ("m1", os.path.getmtime(mod))}
        check("stale_report: nothing edited since the start -> no restart note", SS.stale_report(start, snap)["stale"] == [] and SS.stale_report(start, snap)["restart_note"] == "")
        os.utime(mod, (start + 60, start + 60))
        rep = SS.stale_report(start, snap)
        check("stale_report: a module edited after the start is named, with 'restart the MCP connector to load: ...'", rep["stale"] == [os.path.relpath(mod, HERE).replace("\\", "/")] and rep["restart_note"].startswith("restart the MCP connector to load: "), rep["restart_note"][:90])
        check("stale_report: a process that is not the server (no start time) reports nothing", (SS.stale_report(None, {}) is None) == (SS.SERVER_START is None))

        # --- install: bounded output + worker threads on a real FastMCP (T-03, T-20)
        from mcp.server.fastmcp import FastMCP
        m = FastMCP("t")

        @m.tool()
        def sync_big() -> dict:
            return {"rows": [{"a": "x" * 50} for _ in range(1000)], "ok": True}

        @m.tool()
        async def async_big() -> list:
            return [{"a": "y" * 100} for _ in range(500)]

        @m.tool()
        def sync_name() -> str:
            return threading.current_thread().name

        @m.tool()
        def sync_text() -> str:
            return "t" * 90000

        real_out = SS.OUT_DIR
        SS.OUT_DIR = os.path.join(tmp, "out")                    # the install test must not write into the real job_runs/out
        SS.install(m)
        got = asyncio.run(m.call_tool("sync_big", {}))
        d = got[1] if isinstance(got, tuple) else json.loads(got[0].text)
        d = d if isinstance(d, dict) and "_output" in d else json.loads(got[0][0].text) if isinstance(got, tuple) else d
        check("install: a sync tool's big dict is bounded through the real tool call", "_output" in d and d["rows"]["count"] == 1000, list(d)[:4])
        got = asyncio.run(m.call_tool("async_big", {}))
        check("install: an async tool's big list is bounded too", "output_truncated" in json.dumps(got[0] if isinstance(got, tuple) else got, default=str)[:3000])
        got = asyncio.run(m.call_tool("sync_name", {}))
        text = got[0][0].text if isinstance(got, tuple) else got[0].text
        tool = {t.name: t for t in m._tool_manager.list_tools()}["sync_name"]
        check("install: a sync tool now runs in a worker thread (not the event loop's) and is registered as async", tool.is_async and "MainThread" not in text, text[:40])
        got = asyncio.run(m.call_tool("sync_text", {}))
        text = got[1]["result"] if isinstance(got, tuple) else got[0].text
        check("install: a 90,000-character string is cut to a head and a pointer", len(text) < 4000 and "read_output" in text, len(text))
        SS.OUT_DIR = real_out

        # --- server.py wiring
        import server as SV
        tools = {t.name: t for t in SV.mcp._tool_manager.list_tools()}
        check("server_info and read_output are registered", "server_info" in tools and "read_output" in tools)
        sig = inspect.signature(SV.mannequin_check)
        check("T-04: mannequin_check defaults to reload=False and has an explicit confirm_reload", sig.parameters["reload"].default is False and sig.parameters["confirm_reload"].default is False)
        r = asyncio.run(SV.mannequin_check(reload=True))
        check("T-04: reload=True without confirm_reload is refused (nothing is sent to the editor)", r.get("ok") is False and "confirm_reload" in r["refused"])
        offenders = []
        for name, t in tools.items():
            for pn, p in inspect.signature(t.fn).parameters.items():
                if pn in ("reload", "refresh") and p.default is not False:
                    offenders.append((name, pn, p.default))
        check("T-04: every tool's reload/refresh argument defaults to False", not offenders, offenders)
        # the bridge points at a CLOSED port while the deny-list tests run: if the deny-list ever regressed, the command would fail to connect instead of reaching a real editor (quit, a modal box)
        _s = socket.socket()
        _s.bind(("127.0.0.1", 0))
        _dead = _s.getsockname()[1]
        _s.close()
        _old_ports = (SV.PORT, bridge_py.PORT)
        SV.PORT = bridge_py.PORT = _dead
        try:
            check("T-10: execute_python, execute_python_script, execute_editor_command and run_console_command take really and refuse the deny-list",
                  all(inspect.signature(getattr(SV, f)).parameters["really"].default is False for f in ("execute_python", "execute_python_script", "execute_editor_command", "run_console_command"))
                  and SV.execute_python("general.exit()")["ok"] is False and SV.execute_editor_command("general.exit")["ok"] is False and SV.execute_python_script("message_box_ok_x = general.message_box_ok('a')")["ok"] is False
                  and "really=true" in (raises(lambda: SV.run_console_command("quit"), RuntimeError) or ""))
            _refused = raises(lambda: SV._send_command("bridge.cvar_get x", 1.0), RuntimeError)
        finally:
            SV.PORT, bridge_py.PORT = _old_ports
        sync_tools = [n for n, t in tools.items() if n not in SS.NOT_THREADED and not t.is_async]
        check("T-20: every blocking plain-def tool now runs in a worker thread (is_async set by install)", not sync_tools, sync_tools[:5])
        check("T-12: a refused connection in the server says what to do", "wait_for_editor" in (_refused or ""))

        # a local fake bridge: never answers (timeout) -> the message names list_dialogs; answers ok False 'timed out waiting' -> hint appended; a marker -> no relaunch
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        port = srv.getsockname()[1]
        mode = {"m": "silent"}

        def serve():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                data = c.recv(4096)
                if mode["m"] == "silent" and b"dialog.list" not in data:
                    time.sleep(2.5)
                elif b"dialog.list" in data:
                    c.sendall((json.dumps({"id": 1, "ok": True, "output": json.dumps({"dialogs": [{"title": "Save Level?", "buttons": [{"standard": "Yes"}, {"standard": "No"}]}]})}) + "\n").encode())
                elif mode["m"] == "blocked":
                    c.sendall((json.dumps({"id": 1, "ok": False, "error": "timed out waiting for the editor main thread ... genuinely blocked"}) + "\n").encode())
                c.close()
        threading.Thread(target=serve, daemon=True).start()
        old = (SV.PORT, bridge_py.PORT)
        SV.PORT = bridge_py.PORT = port
        try:
            msg = raises(lambda: SV._send_command("bridge.cvar_get x", 1.0), RuntimeError) or ""
            check("T-12: a timeout in the server names list_dialogs and attaches the open dialogs read out-of-band", "list_dialogs" in msg and "Save Level?" in msg and "did not respond within 1.0s" in msg, msg[:80])
            mode["m"] = "blocked"
            msg = raises(lambda: SV._send_command("bridge.cvar_get x", 5.0), RuntimeError) or ""
            check("T-12: the plugin's own 'timed out waiting for the editor main thread' error gets the same hint", "genuinely blocked" in msg and "click_dialog_button" in msg and "Save Level?" in msg, msg[-80:])
        finally:
            SV.PORT, bridge_py.PORT = old
            srv.close()
        os.environ["CLAUDE_AUTO_RECOVER"] = "1"
        bridge_py.EDITOR_CLOSED_MARKER = marker
        open(marker, "w").write("x")
        old_port = (SV.PORT, bridge_py.PORT)
        s2 = socket.socket()
        s2.bind(("127.0.0.1", 0))
        dead = s2.getsockname()[1]
        s2.close()
        SV.PORT = bridge_py.PORT = dead
        fake = type(sys)("bridge_watchdog")
        fake.recover = lambda timeout=300: (_ for _ in ()).throw(AssertionError("relaunched although the marker exists"))
        saved_mod = sys.modules.get("bridge_watchdog")
        sys.modules["bridge_watchdog"] = fake
        try:
            msg = raises(lambda: SV._send_command("bridge.cvar_get x", 8.0), RuntimeError) or ""
            check("T-14: with CLAUDE_AUTO_RECOVER=1 and the marker file, a dead bridge is NOT relaunched and the error says it was closed on purpose", "ON PURPOSE" in msg and "relaunched although" not in msg, msg[:80])
            msg = raises(lambda: bridge_py.send("bridge.cvar_get x", 8.0)) or ""
            check("T-14: bridge_py.send (scripts, world_build) honours the marker too", "ON PURPOSE" in msg, msg[:60])
        finally:
            os.environ["CLAUDE_AUTO_RECOVER"] = "0"
            SV.PORT, bridge_py.PORT = old_port
            bridge_py.EDITOR_CLOSED_MARKER = old_marker
            if saved_mod is not None:
                sys.modules["bridge_watchdog"] = saved_mod
            else:
                sys.modules.pop("bridge_watchdog", None)

        # --- catalog stamps (T-33), on a fake engine folder
        eng = os.path.join(tmp, "engine")
        os.makedirs(os.path.join(eng, "bin", "win_x64", "EditorPlugins"))
        open(os.path.join(eng, "bin", "win_x64", "EditorPlugins", "ClaudeBridgePlugin.dll"), "wb").write(b"plugin v1")
        open(os.path.join(eng, "bin", "win_x64", "Sandbox.exe"), "wb").write(b"sandbox")
        json.dump({"info": {"version": "5.7.1"}}, open(os.path.join(eng, "cryengine.cryengine"), "w"))
        built = time.time() - 3600
        for f in ("EditorPlugins/ClaudeBridgePlugin.dll", "Sandbox.exe"):
            p = os.path.join(eng, "bin", "win_x64", *f.split("/"))
            os.utime(p, (built, built))
        cat_old, cat_new = os.path.join(tmp, "old.json"), os.path.join(tmp, "new.json")
        open(cat_old, "w").write("{}")
        open(cat_new, "w").write("{}")
        os.utime(cat_old, (built - 86400, built - 86400))
        paths = {"old.json": cat_old, "new.json": cat_new}
        sp = os.path.join(tmp, "stamps.json")
        kw = dict(stamps_path=sp, paths=paths, engine=eng)
        st = CS.status(**kw)
        check("catalog_stamps: an unstamped dump older than the build is flagged, a newer one is 'probably current'", st["old.json"]["state"] == "unstamped-old" and st["new.json"]["state"] == "unstamped-current", {k: v["state"] for k, v in st.items()})
        check("catalog_stamps.note warns for the old one only", CS.note("old.json", **kw) and CS.note("new.json", **kw) is None)
        res = CS.stamp(["old.json", "new.json"], **kw)
        check("stamping refuses a dump older than the build (it would make the warning lie) and stamps the newer one", res["stamped"] == ["new.json"] and "older than the installed build" in res["refused"][0], res)
        check("a stamped dump is ok", CS.status(**kw)["new.json"]["state"] == "ok" and CS.note("new.json", **kw) is None)
        open(os.path.join(eng, "bin", "win_x64", "EditorPlugins", "ClaudeBridgePlugin.dll"), "wb").write(b"plugin v2 rebuilt")
        CS._sha_cache.clear()
        st = CS.status(**kw)
        check("catalog_stamps: after the plugin is rebuilt the stamped dump is STALE and the note names the plugin", st["new.json"]["state"] == "stale" and "ClaudeBridge plugin" in st["new.json"]["detail"], st["new.json"]["detail"][:80])
        real = CS.status()
        _names = {"cvars.json", "editor_commands.json", "editor_api.json"}
        if set(real) == _names and any(v["state"] == "missing" for v in real.values()):
            print("  --   SKIPPED: catalogs not generated (%s): run the dump step with the editor open" % ", ".join(sorted(k for k, v in real.items() if v["state"] == "missing")))
        else:
            check("catalog_stamps on the real machine answers for all three catalogs", set(real) == {"cvars.json", "editor_commands.json", "editor_api.json"} and all(v["state"] != "missing" for v in real.values()), {k: v["state"] for k, v in real.items()})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = results.count(False)
    print("server support test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


def _bridge_up():
    try:
        socket.create_connection(("127.0.0.1", int(os.environ.get("CRYENGINE_BRIDGE_PORT", "37778"))), 0.5).close()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main())
