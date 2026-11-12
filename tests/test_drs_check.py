#!/usr/bin/env python3
"""Self-test of drs_check.py: the comparison on synthetic engine dumps (offline), then, with the editor open, the project's own responses and dialog lines against what the running Dynamic Response System
holds, next to deliberately broken copies (positive controls) and an unchanged copy (control). Copies get unique names, are loaded with a DRS re-initialisation (the call the game's Dialogue.cpp makes) and
deleted again; the system is re-initialised once more at the end so the editor holds only the project's files. About 10 s live.

    test_drs_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import glob
import json
import os
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import drs_check as D

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


TREE = {"BaseSegment": {"name": "s", "SegmentConditions": {"Conditions": [], "Negated": False}, "Actions": [{"Action": {"ChangeVariable": {"variable": "X", "Value": {"value": 1, "Type": "int"}}}, "Delay": 0.0}],
        "ChildSegments": [{"name": "c", "SegmentConditions": {"Conditions": [{"Condition": {"Variable equal to ": {"variable": "X"}}, "Negated": False}], "Negated": False},
                            "Actions": [{"Action": {"SpeakLine": {"lineToSpeak": "li_a", "speakerOverride": ""}}, "Delay": 0.0}], "ChildSegments": []}]}}
DUMP = {"available": True, "log": [], "actions": [{"name": n, "label": n} for n in ("ChangeVariable", "SpeakLine", "Wait")], "conditions": [{"name": n, "label": n} for n in ("Variable equal to ", "Random")],
        "lines": [{"id": "li_a", "priority": 50, "flags": 1, "queue": -1, "variations": [{"text": "Hi", "start": "trg_a", "stop": "", "lipsync": "", "pause": 4.0, "custom": ""}]}], "responses": {"sg_a": TREE}}
DIALOG = {"lineSets": [{"lineID": "li_a", "priority": 50, "flags": 1, "lineVariations": [{"text": "Hi", "audioStartTrigger": "trg_a", "audioStopTrigger": "", "pauseLength": 4.0}]}]}


def copy(o):
    return json.loads(json.dumps(o))


def main():
    r = D.compare(copy(DUMP), {"sg_a": copy(TREE)}, {"x.dialog": copy(DIALOG)}, {"sg_a": "Code/x.cpp:1"}, {"trg_a"})
    check("control: a response, a dialog line and a code signal that all match the engine have no finding", r == [], codes(r))
    t = copy(TREE)
    t["BaseSegment"]["Actions"][0]["Action"] = {"ChangeVarible": {"variable": "X"}}
    r = D.compare(copy(DUMP), {"sg_a": t}, {}, {"sg_a": "c"}, None)
    check("positive control: an action class the engine does not know is D002 and names it", "D002" in codes(r) and any("ChangeVarible" in f["message"] for f in r), codes(r))
    t = copy(TREE)
    t["BaseSegment"]["ChildSegments"][0]["SegmentConditions"]["Conditions"][0]["Condition"] = {"Variable equal to": {"variable": "X"}}
    r = D.compare(copy(DUMP), {"sg_a": t}, {}, {"sg_a": "c"}, None)
    check("positive control: a condition class spelt without its trailing space ('Variable equal to') is D002", "D002" in codes(r), codes(r))
    t = copy(TREE)
    t["BaseSegment"]["ChildSegments"][0]["Actions"][0]["Action"]["SpeakLine"]["lineToSpeak"] = "li_b"
    r = D.compare(copy(DUMP), {"sg_a": t}, {}, {"sg_a": "c"}, None)
    check("positive control: a SpeakLine for a line the engine does not hold is D003", "D003" in codes(r), codes(r))
    d = copy(DUMP)
    d["responses"]["sg_a"] = None
    check("positive control: a signal the engine holds no response for is D001", "D001" in codes(D.compare(d, {"sg_a": copy(TREE)}, {}, {"sg_a": "c"}, None)), "")
    d = copy(DUMP)
    d["responses"]["sg_a"]["BaseSegment"]["ChildSegments"] = []
    r = D.compare(d, {"sg_a": copy(TREE)}, {}, {"sg_a": "c"}, None)
    check("positive control: a segment the engine did not keep is D006 and shows the first missing value", "D006" in codes(r) and any("ChildSegments[0]" in f["message"] for f in r), codes(r))
    r = D.compare(copy(DUMP), {"sg_a": copy(TREE)}, {}, {"sg_a": "c", "sg_b": "Code/y.cpp:2"}, None)
    check("positive control: a signal the code sends with no response file is D004", "D004" in codes(r) and any("sg_b" in f["message"] for f in r), codes(r))
    r = D.compare(copy(DUMP), {"sg_a": copy(TREE), "sg_c": copy(TREE)}, {}, {"sg_a": "c"}, None)
    check("a response nobody in the code sends is only information (D004 info: a flow graph or data may send it)", [f["severity"] for f in r if f["code"] == "D004"] == ["info"], codes(r))
    dd = copy(DIALOG)
    dd["lineSets"][0]["lineVariations"][0]["audioStartTrigger"] = "trg_missing"
    r = D.compare(copy(DUMP), {}, {"x.dialog": dd}, {}, {"trg_a"})
    check("positive control: a dialog line whose audio trigger is in no audio library is D005; and its difference from the engine's copy is D007", {"D005", "D007"} <= set(codes(r)), codes(r))
    dd = copy(DIALOG)
    dd["lineSets"].append({"lineID": "li_new", "priority": 1, "flags": 1, "lineVariations": []})
    check("positive control: a line in the file the engine does not hold is D007, an error", [f["severity"] for f in D.compare(copy(DUMP), {}, {"x.dialog": dd}, {}, None) if f["code"] == "D007"] == ["error"], "")
    d = copy(DUMP)
    d["log"] = ["$6[Warning] something could not be loaded", "$3an ordinary line"]
    check("positive control: a problem the engine logged while re-initialising is D008 and an ordinary line is not", codes(D.compare(d, {}, {}, {}, None)) == ["D008"], "")
    check("the signal name is the file's base name", D.signal_of("a/b/sg_villager_sees_player.response") == "sg_villager_sees_player", "")
    # was: sorted(D.code_signals()) == the Ashvale game's two sg_villager_* signals (the author's game code); a project-independent self-consistency check instead
    sigs = D.code_signals()
    check("the code scanner returns a list of distinct signal names without a path or extension", len(set(sigs)) == len(sigs) and all(isinstance(x, str) and x and "/" not in x and not x.endswith(".response") for x in sigs), sorted(sigs)[:3])

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        res = D.check()
        eng = res["engine"]
        check("the running engine knows 12 action types (SpeakLine, ChangeVariable, SendSignal, Wait ...) and 12 condition types (Variable equal to , Random, TimeSince ...)", len(eng["actions"]) == 12 and len(eng["conditions"]) == 12 and "SpeakLine" in eng["actions"] and "Variable equal to " in eng["conditions"], (len(eng["actions"]), len(eng["conditions"])))
        check("it holds the project's 3 dialog lines and both responses, and what it KEPT equals the file: no finding at all", res["ok"] and eng["lines"] == ["li_villager_greeting", "li_villager_wary", "li_villager_heard_something"] and not res["findings"], [f["code"] for f in res["findings"]])
        res2 = D.check(reload=True)
        check("after a re-initialisation (the game's own call) the engine holds the same: no finding, and the call returned", res2["ok"] and not res2["findings"] and res2["engine"]["reloaded"], [f["code"] + f["message"][:30] for f in res2["findings"]])

        stamp = "%d_%d" % (os.getpid(), int(time.time()))
        folder = os.path.join(D.ROOT, "Responses")
        src = open(os.path.join(folder, "sg_villager_sees_player.response"), encoding="utf-8").read()
        made = []

        def variant(tag, text):
            name = "zz_mcp_%s_%s" % (stamp, tag)
            p = os.path.join(folder, name + ".response")
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            made.append(p)
            return name
        try:
            names = {
                "copy": variant("copy", src),
                "action": variant("action", src.replace('"ChangeVariable"', '"ChangeVarible"')),
                "cond": variant("cond", src.replace('"Variable equal to "', '"Variable equal to"')),
                "line": variant("line", src.replace("li_villager_greeting", "li_villager_nonexistent")),
                "notjson": variant("notjson", "{ this is not json"),
            }
            time.sleep(1.5)
            res3 = D.check(reload=True)
            fs = res3["findings"]

            def of(tag):
                return [f for f in fs if names[tag] in f["where"] and f["code"] != "D004"]          # D004 info: nothing in the C++ sends a scratch signal
            check("control: an unchanged copy of the response is loaded under its own file name and compares equal", not of("copy"), codes(of("copy")))
            check("positive control: a misspelt action class (ChangeVarible) is D002, and the engine's copy has lost it (D006)", {"D002", "D006"} <= set(codes(of("action"))), codes(of("action")))
            check("positive control: a condition class without its trailing space is D002, and the engine's copy has lost it (D006)", {"D002", "D006"} <= set(codes(of("cond"))), codes(of("cond")))
            check("positive control: a SpeakLine for a line that does not exist loads fine and is found by the line check (D003)", "D003" in codes(of("line")), codes(of("line")))
            check("positive control: a file that is not JSON is D001, and the message says the engine made an EMPTY response from it (no log line)", any(f["code"] == "D001" and "EMPTY" in f["message"] for f in of("notjson")), [f["message"][-70:] for f in of("notjson")])
            check("measured: re-initialising wrote no error- or warning-worded log line for any of the broken files (the engine is silent: only the comparison shows it)", not [f for f in fs if f["code"] == "D008"], [f["message"][:40] for f in fs if f["code"] == "D008"])
        finally:
            time.sleep(1)
            for p in made + glob.glob(os.path.join(folder, "zz_mcp_*")):
                if os.path.exists(p):
                    os.remove(p)
            d2 = D.dump(["sg_villager_sees_player"], reload=True)
        left = glob.glob(os.path.join(folder, "zz_mcp_*"))
        check("the project is as it was: no test response is left, and the engine was re-initialised so it holds only the project's two", not left and D.check()["ok"], left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


import re  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
