"""flow_graph.build: the specification becomes a standalone graph (what flow_check loads) and an in-level <FlowGraph> child; bad specifications are refused (offline part; the editor part is run by hand)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import sys
import xml.etree.ElementTree as ET

import flow_graph as F

failed = []
N = [0]


def check(name, ok, detail=""):
    N[0] += 1
    print("  %s %s %s" % ("ok  " if ok else "FAIL", name.ljust(100), "" if ok else detail))
    if not ok:
        failed.append(name)


def refused(nodes, edges):
    try:
        F.build(nodes, edges, {"zone_trig": "{AAAA}"})
        return False
    except F.FlowGraphError:
        return True


nodes = [{"id": "1", "class": "entity:AreaTrigger", "entity": "zone_trig"}, {"id": "2", "class": "Debug:Log", "inputs": {"message": "x_enter"}}]
edges = [{"from": "1.Enter", "to": "2.input"}]
standalone, level = F.build(nodes, edges, {"zone_trig": "{AAAA}"}, "d", "g")
s, l = ET.fromstring(standalone), ET.fromstring(level)
check("control: standalone graph has nodes with ID, the edge and the class", [n.get("ID") for n in s.find("Nodes")] == ["1", "2"] and s.find("Edges")[0].get("portOut") == "Enter", standalone)
check("in-level graph is a <FlowGraph> with Id attributes, EntityGUID on the entity node only, GraphTokens",
      l.tag == "FlowGraph" and [n.get("Id") for n in l.find("Nodes")] == ["1", "2"] and l.find("Nodes")[0].get("EntityGUID") == "{AAAA}" and l.find("Nodes")[1].get("EntityGUID") is None and l.find("GraphTokens") is not None, level)
check("inputs are written as attributes of <Inputs>", l.find("Nodes")[1].find("Inputs").get("message") == "x_enter", level)
check("an empty graph is refused", refused([], []))
check("a duplicate node id is refused", refused([nodes[0], dict(nodes[0])], []))
check("a non-numeric node id is refused", refused([{"id": "a", "class": "Debug:Log"}], []))
check("an edge to a node that is not there is refused", refused(nodes, [{"from": "1.Enter", "to": "9.input"}]))
check("an edge without a port is refused", refused(nodes, [{"from": "1", "to": "2.input"}]))
try:
    F.build(nodes, edges, {})
    check("an entity name that was not resolved to a GUID is refused", False)
except F.FlowGraphError:
    check("an entity name that was not resolved to a GUID is refused", True)
try:
    a, b = F.build([{"id": "1", "class": "Debug:Log", "inputs": {"message": 'say "hi" <&>'}}], [])
    check("quotes and angle brackets in input values survive as valid XML", ET.fromstring(b).find("Nodes")[0].find("Inputs").get("message") == 'say "hi" <&>')
except Exception as exc:  # noqa: BLE001
    check("quotes and angle brackets in input values survive as valid XML", False, exc)
print("\n%d checks, %d failed" % (N[0], len(failed)))
sys.exit(1 if failed else 0)
