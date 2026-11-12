"""Put a flow graph into a LEVEL: a `FlowgraphEntity` object holds it as a `<FlowGraph>` child (the GameSDK sample's ab01_gameplay.lyr; EntityLoadManager.cpp:604 queues the graph until
every EntityGUID of its nodes exists; the editor reads and writes the child in EntityObject.cpp). Nothing in the project could do that before: flow_check only loads a standalone file.

    flow_graph.build(nodes, edges)            -> (standalone <Graph> text, in-level <FlowGraph> text), both from the same specification
    flow_graph.add(entity, nodes, edges)      the whole job through the open editor (see the MCP tool `flow_graph_add`)

Specification (JSON-friendly):
  nodes: [{"id": "1", "class": "entity:AreaTrigger", "entity": "zone_trig" (an entity NAME, resolved to its GUID; only for nodes that target an entity),
           "inputs": {"Enable": "1"}, "pos": [x, y]}]
  edges: [{"from": "1.Enter", "to": "2.input"}]       "<node id>.<port>"; each edge is checked against the node catalog (flow_check) before anything is written.
The standalone graph is checked by the ENGINE loader first (flow_check: F001-F008) in a throw-away file `libs/FlowGraphs/zz_mcp_flow_graph.xml` that is deleted afterwards.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

HERE = _paths.ROOT
sys.path.insert(0, HERE)


class FlowGraphError(RuntimeError):
    pass


def _split_port(text, what):
    m = re.fullmatch(r"([^.\s]+)\.(\S+)", text or "")
    if not m:
        raise FlowGraphError("%s '%s' must be '<node id>.<port name>'" % (what, text))
    return m.group(1), m.group(2)


def _validate(nodes, edges):
    if not nodes:
        raise FlowGraphError("a graph needs at least one node")
    seen = set()
    for n in nodes:
        if not n.get("id") or not str(n["id"]).isdigit():
            raise FlowGraphError("node ids are numbers (as strings): %r" % n.get("id"))
        if str(n["id"]) in seen:
            raise FlowGraphError("node id %s is used twice" % n["id"])
        seen.add(str(n["id"]))
        if not n.get("class"):
            raise FlowGraphError("node %s has no class" % n["id"])
    for e in edges:
        for key in ("from", "to"):
            node, _ = _split_port(e.get(key), "edge " + key)
            if node not in seen:
                raise FlowGraphError("edge %s -> %s names node %s, which is not in the graph" % (e.get("from"), e.get("to"), node))


def build(nodes, edges, entity_guids=None, description="", group=""):
    """-> (standalone, in_level). entity_guids: {entity name: GUID string} for nodes that name an entity."""
    _validate(nodes, edges)
    entity_guids = entity_guids or {}

    def node_xml(n, in_level):
        attrs = ['ID' if not in_level else 'Id', str(n["id"])]
        parts = ['%s=%s' % (attrs[0], quoteattr(attrs[1])), 'Class=%s' % quoteattr(n["class"])]
        if n.get("entity"):
            if n["entity"] not in entity_guids:
                raise FlowGraphError("node %s targets entity '%s' that was not resolved to a GUID" % (n["id"], n["entity"]))
            parts.append('EntityGUID=%s' % quoteattr(entity_guids[n["entity"]]))
        pos = n.get("pos") or [100 + 220 * int(n["id"]), 100]
        parts.append('pos=%s' % quoteattr("%g,%g,0" % (pos[0], pos[1])))
        inputs = "".join(" %s=%s" % (k, quoteattr(str(v))) for k, v in (n.get("inputs") or {}).items())
        return "<Node %s><Inputs%s/></Node>" % (" ".join(parts), inputs)

    def edges_xml():
        out = []
        for e in edges:
            fn, fp = _split_port(e["from"], "from")
            tn, tp = _split_port(e["to"], "to")
            out.append('<Edge nodeOut=%s portOut=%s nodeIn=%s portIn=%s enabled="1"/>' % (quoteattr(fn), quoteattr(fp), quoteattr(tn), quoteattr(tp)))
        return "<Edges>" + "".join(out) + "</Edges>"

    standalone = "<Graph><Nodes>" + "".join(node_xml(n, False) for n in nodes) + "</Nodes>" + edges_xml() + "</Graph>"
    in_level = ('<FlowGraph Description=%s Group=%s enabled="1" MultiPlayer="ServerOnly"><Nodes>' % (quoteattr(description), quoteattr(group))
                + "".join(node_xml(n, True) for n in nodes) + "</Nodes>" + edges_xml() + "<GraphTokens/></FlowGraph>")
    ET.fromstring(standalone)
    ET.fromstring(in_level)
    return standalone, in_level


def add(entity, nodes, edges, description="", group="", dry_run=False):
    """Check the graph with the engine's loader, then store it as the `<FlowGraph>` child of the FlowgraphEntity `entity`. Returns what the check and the read-back say."""
    import flow_check
    import object_props as O
    obj = O.get_xml(entity)
    if obj.get("Type") != "Entity" or (obj.get("EntityClass") or "") != "FlowgraphEntity":
        raise FlowGraphError("'%s' is %s %s, not a FlowgraphEntity (place_object kind='entity' asset='FlowgraphEntity')" % (entity, obj.get("Type"), obj.get("EntityClass") or ""))
    guids = {}
    for n in nodes:
        name = n.get("entity")
        if name and name not in guids:
            node = O.get_xml(name)
            if node.get("Type") != "Entity":
                raise FlowGraphError("node %s targets '%s', which is a %s, not an entity" % (n["id"], name, node.get("Type")))
            guids[name] = node.get("Id")
    standalone, in_level = build(nodes, edges, guids, description, group)
    rel = "libs/FlowGraphs/zz_mcp_flow_graph.xml"
    path = os.path.join(flow_check.ASSETS, "libs", "FlowGraphs", "zz_mcp_flow_graph.xml")
    with open(path, "w", encoding="utf8") as f:
        f.write(standalone)
    try:
        res = flow_check.check([rel])
    finally:
        os.remove(path)
    bad = [f for f in res["findings"] if f["severity"] == "error"]
    out = {"entity": entity, "nodes": len(nodes), "edges": len(edges), "engine_check": {"errors": res["errors"], "warnings": res["warnings"], "findings": res["findings"][:12], "graphs": res["graphs"]}}
    if bad:
        out["stored"] = False
        out["refused"] = "the engine's loader found errors in the graph; nothing was written to the level"
        return out
    if dry_run:
        out["stored"] = False
        out["dry_run"] = True
        return out
    O.set_properties(entity, None, {"FlowGraph": in_level})
    back = O.get_xml(entity).find("FlowGraph")
    n_nodes = len(back.find("Nodes")) if back is not None and back.find("Nodes") is not None else 0
    n_edges = len(back.find("Edges")) if back is not None and back.find("Edges") is not None else 0
    out["stored"] = back is not None and n_nodes == len(nodes) and n_edges == len(edges)
    out["read_back"] = {"nodes": n_nodes, "edges": n_edges}
    return out
