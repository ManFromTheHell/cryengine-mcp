#!/usr/bin/env python3
"""Self-test of flow_check.py: the comparison on synthetic input (offline), then, with the editor open, the engine's whole node catalog and the project's graph through the engine's own
loader, next to deliberately broken copies (positive controls) and an unchanged copy (control). Copies get unique names (CryPak caches a loose file by name) and are deleted again. About 15 s live.

    test_flow_check.py        # exit 1 on any failure
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
import flow_check as F

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text=""):
    return sorted({f["code"] for f in findings if text in f["message"] or text in f["where"]})


CAT = [{"type": "Time:Timer", "category": "approved", "inputs": [{"name": "period", "data_type": "Float"}, {"name": "paused", "data_type": "Bool"}], "outputs": [{"name": "out", "data_type": "Any"}]},
       {"type": "Debug:Log", "category": "debug", "inputs": [{"name": "input", "data_type": "Any"}, {"name": "vec3_Pos", "data_type": "Vec3"}], "outputs": []},
       {"type": "Old:Thing", "category": "obsolete", "inputs": [], "outputs": []}]


def row(nodes=None, edges=None, ok=True, loaded=True, log=None):
    return {"xml_loaded": loaded, "ok": ok, "nodes": nodes if nodes is not None else [
        {"id": 0, "name": "1", "type": "Time:Timer", "inputs": [{"name": "period", "value": "8"}, {"name": "paused", "value": "false"}], "outputs": ["out"]},
        {"id": 1, "name": "2", "type": "Debug:Log", "inputs": [{"name": "input", "value": ""}, {"name": "vec3_Pos", "value": "0,0,0"}], "outputs": []}],
        "edges": edges if edges is not None else [{"from": "1", "out": "out", "to": "2", "in": "input"}], "log": log or []}


FNODES = {"1": {"class": "Time:Timer", "inputs": {"period": "8", "paused": "0"}}, "2": {"class": "Debug:Log", "inputs": {"Pos": "0,0,0"}}}
FEDGES = [("1", "out", "2", "input")]


def main():
    # ---- the comparison, offline
    r = F.compare("g.xml", row(), FNODES, FEDGES, 0, CAT)
    check("control: a graph that matches its file has only the information finding F008 (Debug:Log is a debug node); '0' for a Bool and a stripped port name 'Pos' are accepted", codes(r) == ["F008"], codes(r))
    check("positive control: an XML the engine could not read is F001", codes(F.compare("g.xml", row(loaded=False, ok=False, nodes=[], edges=[]), FNODES, FEDGES, 0, CAT)) == ["F001"], "")
    r = F.compare("g.xml", row(ok=False, nodes=[], edges=[], log=["$6[Warning] [flow] <noname> : Failed to create node '1' of type 'Time:Timr' - FLOWGRAPH DISCARDED"]), {"1": {"class": "Time:Timr", "inputs": {}}}, [], 0, CAT)
    check("positive control: an unregistered Class is F003, and the discarded graph is F002 with the engine's own reason", {"F002", "F003"} <= set(codes(r)) and any("Time:Timr" in f["message"] and f["code"] == "F002" for f in r), codes(r))
    r = F.compare("g.xml", row(), {"1": {"class": "Time:Timer", "inputs": {"perood": "8"}}, "2": FNODES["2"]}, FEDGES, 0, CAT)
    check("positive control: an <Inputs> attribute that is not a port is F004 (the engine ignores it) and names the real ports", "F004" in codes(r) and any("period" in f["message"] for f in r if f["code"] == "F004"), codes(r))
    check("positive control: an edge from a port the node lacks is F005 (the engine would discard the graph)", "F005" in codes(F.compare("g.xml", row(), FNODES, [("1", "nope", "2", "input")], 0, CAT)), "")
    check("positive control: an edge into a node that is not in the file is F005", "F005" in codes(F.compare("g.xml", row(), FNODES, [("1", "out", "9", "input")], 0, CAT)), "")
    bad = row()
    bad["nodes"][0]["inputs"][0]["value"] = "0"
    r = F.compare("g.xml", bad, FNODES, FEDGES, 0, CAT)
    check("positive control: <Inputs period=\"8\"> held as \"0\" is F006 (a value that did not convert keeps the default)", "F006" in codes(r), codes(r))
    r = F.compare("g.xml", row(edges=[]), FNODES, FEDGES, 0, CAT)
    check("positive control: fewer edges in the engine than in the file is F007", "F007" in codes(r), codes(r))
    r = F.compare("g.xml", row(), {"1": {"class": "Old:Thing", "inputs": {}}, "2": FNODES["2"]}, [], 0, CAT)
    check("an obsolete node is reported (F008) and the editor-hidden status is stated", any("obsolete" in f["message"] for f in r if f["code"] == "F008"), codes(r))
    check("values are compared the way each type prints: Bool 1/true, Float 8/8.0, Vec3 with spaces", F.same_value("1", "true", "Bool") and F.same_value("8", "8.000000", "Float") and F.same_value("0, 0, 1", "0,0,1", "Vec3") and not F.same_value("abc", "0", "Int"), "")
    check("a stripped port name is the text after the first underscore", F._stripped("vec3_Pos") == "Pos" and F._stripped("period") == "period", "")
    # These two checks, and the live half below, assume the Ashvale project (its claude_farm.xml graph and its C++ flow nodes): skipped on any other project.
    have_farm = os.path.isfile(os.path.join(F.ASSETS, "libs", "FlowGraphs", "claude_farm.xml"))
    if have_farm:
        fn, fe, dis = F.file_graph("libs/FlowGraphs/claude_farm.xml")
        check("the file reader returns the project's graph: 4 nodes (Time:Timer, Claude:Lamp, Claude:VillagerSays, Debug:Log) and 3 edges", sorted(v["class"] for v in fn.values()) == ["Claude:Lamp", "Claude:VillagerSays", "Debug:Log", "Time:Timer"] and len(fe) == 3 and dis == 0, len(fn))
    else:
        print("  --   SKIPPED: libs/FlowGraphs/claude_farm.xml is not in this project (Ashvale-specific graph checks)")
    game = F.code_nodes()
    if game:
        check("the game's C++ registers Claude:AreaEvents, Claude:Lamp and Claude:VillagerSays", sorted(game) == ["Claude:AreaEvents", "Claude:Lamp", "Claude:VillagerSays"], sorted(game))
    else:
        print("  --   SKIPPED: no REGISTER_FLOW_NODE found in this project's C++ (Ashvale-specific node check)")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live and not have_farm:
        print("  --   SKIPPED: the live checks use the Ashvale graph libs/FlowGraphs/claude_farm.xml, which this project does not have")
        live = False
    if live:
        d = F.catalog(refresh=True)
        types = {t["type"]: t for t in d["types"]}
        check("the engine lists 600+ node types and every one can be created in a scratch graph", d["count"] >= 600 and d["created"] == d["count"], (d["count"], d["created"]))
        t = types.get("Time:Timer")
        check("Time:Timer has the ports the manual's node reference gives: inputs period/min/max/paused, output out", t and [p["name"] for p in t["inputs"]] == ["period", "min", "max", "paused"] and [p["name"] for p in t["outputs"]] == ["out"], t and [p["name"] for p in t["inputs"]])
        check("the port types are told apart: period is Float, paused is Bool", t and {p["name"]: p["data_type"] for p in t["inputs"]}["period"] == "Float" and {p["name"]: p["data_type"] for p in t["inputs"]}["paused"] == "Bool", "")
        check("the game's own nodes are in the engine's list (the editor hands them over)", "Claude:Lamp" in types and "Claude:VillagerSays" in types, "")
        check("categories come from the engine's flags: approved, advanced, debug and obsolete nodes all exist", {"approved", "advanced", "debug", "obsolete"} <= {x["category"] for x in d["types"]}, sorted({x["category"] for x in d["types"]}))
        rows, total = F.search("timer")
        check("the search finds nodes by name, description or port", total >= 2 and any(r["type"] == "Time:Timer" for r in rows), total)

        res = F.check()
        g = res["graphs"]
        check("the project's graph loads through the engine: 4 nodes, 3 edges, no error or warning", res["ok"] and not res["warnings"] and g and g[0]["loaded"] and g[0]["nodes"] == 4 and g[0]["edges"] == 3, g)
        out_file = os.path.join(F.SCRATCH, "t_farm.json")
        bridge_py.send("bridge.flow.check %s | libs/FlowGraphs/claude_farm.xml" % out_file.replace(chr(92), "/"), 60)
        farm = json.load(open(out_file, encoding="utf-8"))
        held = {n["type"]: {p["name"]: p["value"] for p in n["inputs"]} for n in farm["nodes"]}
        check("the engine holds the values the file gave: Time:Timer period 8 (a number it printed itself) and Claude:Lamp Name ClaudeLamp, and the edges are by name",
              held["Time:Timer"]["period"] in ("8", "8.000000", "8.0") and held["Claude:Lamp"]["Name"] == "ClaudeLamp" and {(e["from"], e["out"], e["to"], e["in"]) for e in farm["edges"]} == {("1", "out", "2", "Toggle"), ("2", "State", "4", "input"), ("2", "State", "3", "Trigger")},
              held["Time:Timer"])
        os.remove(out_file)

        stamp = "%d_%d" % (os.getpid(), int(time.time()))
        folder = os.path.join(F.ASSETS, "libs", "FlowGraphs")
        src = open(os.path.join(folder, "claude_farm.xml"), encoding="utf-8").read()
        made = []

        def variant(tag, text):
            rel = "libs/FlowGraphs/zz_mcp_%s_%s.xml" % (stamp, tag)
            p = os.path.join(F.ASSETS, rel.replace("/", os.sep))
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            made.append(p)
            return rel
        try:
            rels = {
                "copy": variant("copy", src),
                "class": variant("class", src.replace('Class="Time:Timer"', 'Class="Time:Timr"')),
                "port": variant("port", src.replace('portIn="Toggle"', 'portIn="Togle"')),
                "attr": variant("attr", src.replace('period="8"', 'perood="8"')),
                "value": variant("value", src.replace('period="8"', 'period="eight"')),
                "notxml": variant("notxml", "<Graph"),
                "disabled": variant("disabled", src.replace('nodeIn="4" portIn="input"   enabled="1"', 'nodeIn="4" portIn="input"   enabled="0"')),
            }
            time.sleep(1.5)
            res = F.check(list(rels.values()))
            fs = res["findings"]
            gr = {x["file"]: x for x in res["graphs"]}

            def of(tag):
                return [f for f in fs if rels[tag] in f["where"]]
            check("control: an unchanged copy of the graph loads with 4 nodes and 3 edges and nothing but the debug-node information", gr[rels["copy"]]["loaded"] and gr[rels["copy"]]["nodes"] == 4 and gr[rels["copy"]]["edges"] == 3 and codes(of("copy")) == ["F008"], codes(of("copy")))
            check("positive control: a misspelt node Class is F003 and the engine DISCARDS the graph (F002, 0 nodes kept)", {"F002", "F003"} <= set(codes(of("class"))) and not gr[rels["class"]]["loaded"], codes(of("class")))
            check("positive control: an edge into a port that does not exist is F005 and the engine discards the graph (F002)", {"F002", "F005"} <= set(codes(of("port"))) and not gr[rels["port"]]["loaded"], codes(of("port")))
            check("positive control: a misspelt <Inputs> attribute is F004 and the graph still LOADS (the silent case)", "F004" in codes(of("attr")) and gr[rels["attr"]]["loaded"], codes(of("attr")))
            check("positive control: a value that is not a number for a Float port (period=\"eight\") is F006: the engine keeps another value", "F006" in codes(of("value")) and gr[rels["value"]]["loaded"], [f["message"][:70] for f in of("value") if f["code"] == "F006"])
            check("positive control: a file that is not well-formed XML is F001", "F001" in codes(of("notxml")), codes(of("notxml")))
            check("a disabled edge (enabled=\"0\") is skipped by the engine and by the comparison: 2 edges, no F007", gr[rels["disabled"]]["loaded"] and gr[rels["disabled"]]["edges"] == 2 and "F007" not in codes(of("disabled")), (gr[rels["disabled"]]["edges"], codes(of("disabled"))))
        finally:
            time.sleep(1)
            for p in made + glob.glob(os.path.join(folder, "zz_mcp_*")):
                if os.path.exists(p):
                    os.remove(p)
        left = glob.glob(os.path.join(folder, "zz_mcp_*"))
        check("the project is as it was: no test graph is left", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
