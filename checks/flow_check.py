#!/usr/bin/env python3
"""The Flow System as the ENGINE has it (ENGINE_LEARNING_PLAN E2), from the open editor, without running the game.

Two plugin verbs:
  bridge.flow.catalog   every registered node type (IFlowSystem::CreateNodeTypeIterator), each created in a scratch graph and read back: flags/category, description, input ports (type, default,
                        UI config) and output ports. Cached in engine_data/flow_nodes.json.
  bridge.flow.check     a graph XML loaded the way the game loads it (IFlowGraph::SerializeXML) into a scratch graph that is neither enabled nor initialised (no node runs); what the engine kept.

This module compares the loaded graph with the file and the catalog:

  F001 the engine could not read the XML file                        F005 an edge names a node or port the node does not have (the engine DISCARDS the graph)
  F002 the engine discarded the graph (its reason, from the log)     F006 an input value the file gives is not the one the engine holds (a failed conversion keeps the default)
  F003 a node's Class is not a registered node type                  F007 the engine holds a different number of nodes or edges than the file gives
  F004 an <Inputs> attribute is not a port of the node (ignored)     F008 the node is flagged debug or obsolete (the editor hides obsolete nodes)

    python checks/flow_check.py nodes [text]                  search the catalog (type, description, port names)
    python checks/flow_check.py node <type>                   one node type in full
    python checks/flow_check.py check [libs/flowgraphs/x.xml ...]      default: every XML under Assets/libs/FlowGraphs
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")
DATA = os.path.join(HERE, "engine_data")
CATALOG_FILE = os.path.join(DATA, "flow_nodes.json")
SCRATCH = os.path.join(DATA, "flow_scratch")

sys.path.insert(0, HERE)

TYPES = {-1: "Any", 0: "Void", 1: "Int", 2: "Float", 3: "EntityId", 4: "Vec3", 5: "String", 6: "Bool"}
CATEGORY_BITS = ((0x10, "approved"), (0x20, "advanced"), (0x40, "debug"), (0x200, "obsolete"))
CORE_BITS = ((0x1, "targets an entity"), (0x2, "hidden in the UI"), (0x4, "dynamic outputs"), (0x8, "cannot be removed"))


class FlowError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def category(flags):
    names = [n for b, n in CATEGORY_BITS if flags & b]
    return names[0] if names else "none"


def _normalise(row):
    row["category"] = category(row.get("flags", 0))
    row["group"] = row["type"].split(":")[0] if ":" in row["type"] else ""
    row["core"] = [n for b, n in CORE_BITS if row.get("flags", 0) & b]
    for p in row.get("inputs", []):
        p["data_type"] = TYPES.get(p["type"], str(p["type"])) if p.get("locked") else "Any"
    for p in row.get("outputs", []):
        p["data_type"] = TYPES.get(p["type"], str(p["type"]))
    return row


def catalog(refresh=False):
    """-> {types: [...], count, created}. The first call (or refresh) asks the editor and writes engine_data/flow_nodes.json."""
    if not refresh and os.path.isfile(CATALOG_FILE):
        with open(CATALOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    import bridge_py
    os.makedirs(DATA, exist_ok=True)
    out = os.path.join(SCRATCH, "flow_nodes_%d.json" % os.getpid())
    os.makedirs(SCRATCH, exist_ok=True)
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.flow.catalog %s" % _slash(out), 120)
    if not r.get("ok"):
        raise FlowError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        rows = [_normalise(x) for x in json.load(f)]
    os.remove(out)
    data = {"count": len(rows), "created": sum(1 for x in rows if x["created"]), "types": sorted(rows, key=lambda x: x["type"].lower())}
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def search(text="", limit=40, refresh=False):
    """Node types whose name, description or port names/descriptions contain every word. -> (rows, total)"""
    words = [w for w in re.split(r"\s+", text.lower().strip()) if w]
    out = []
    for row in catalog(refresh)["types"]:
        hay = " ".join([row["type"], row.get("description", "")] + [p["name"] + " " + p.get("description", "") for p in row.get("inputs", []) + row.get("outputs", [])]).lower()
        if all(w in hay for w in words):
            out.append({"type": row["type"], "category": row["category"], "description": row.get("description", ""),
                        "inputs": [p["name"] for p in row.get("inputs", [])], "outputs": [p["name"] for p in row.get("outputs", [])]})
    return out[:limit], len(out)


def node(type_name, refresh=False):
    """One node type in full (case-insensitive), or None."""
    for row in catalog(refresh)["types"]:
        if row["type"].lower() == type_name.lower():
            return row
    return None


# ---------------------------------------------------------------------------- the game's own nodes

def code_nodes(code_dir=None):
    """{node type: file:line} for the REGISTER_FLOW_NODE("Type", Class) lines in the game's C++."""
    found = {}
    for root, _, files in os.walk(code_dir or CODE):
        for fn in files:
            if not fn.endswith((".cpp", ".h")):
                continue
            path = os.path.join(root, fn)
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                m = re.search(r'REGISTER_FLOW_NODE(?:_SINGLETON)?\(\s*"([^"]+)"', line)
                if m:
                    found[m.group(1)] = "%s:%d" % (_slash(os.path.relpath(path, PROJECT)), n)
    return found


# ---------------------------------------------------------------------------- a graph file

def project_graphs():
    out = []
    base = os.path.join(ASSETS, "libs", "FlowGraphs")
    for root, _, files in os.walk(base):
        for f in files:
            if f.lower().endswith(".xml"):
                out.append(_slash(os.path.relpath(os.path.join(root, f), ASSETS)))
    return sorted(out)


def file_graph(path):
    """What the FILE says: ({node id: {class, inputs: {attr: value}}}, [(nodeOut, portOut, nodeIn, portIn)], disabled edge count). Comment/box nodes (Class starting with '_') are skipped."""
    root = ET.parse(os.path.join(ASSETS, path.replace("/", os.sep))).getroot()
    nodes, edges, disabled = {}, [], 0
    nd = root.find("Nodes")
    for n in (nd if nd is not None else []):
        cls = n.get("Class") or ""
        if cls in ("_comment", "_commentbox", "_blackbox"):
            continue
        inputs = n.find("Inputs")
        nodes[n.get("ID") or ""] = {"class": cls, "inputs": dict(inputs.attrib) if inputs is not None else {}}
    ed = root.find("Edges")
    for e in (ed if ed is not None else []):
        if e.tag != "Edge":
            continue
        if (e.get("enabled") or "1").lower() in ("0", "false"):
            disabled += 1
            continue
        edges.append((e.get("nodeOut") or "", e.get("portOut") or "", e.get("nodeIn") or "", e.get("portIn") or ""))
    return nodes, edges, disabled


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _stripped(name):
    """FG_ALLOW_STRIPPED_PORTNAMES: the text after the first '_' (the special-type prefix) is accepted for an input value."""
    return name.split("_", 1)[1] if "_" in name else name


def same_value(file_value, engine_value, data_type):
    """Is the engine's text the value the file asked for, allowing for how each type prints?"""
    a, b = str(file_value).strip(), str(engine_value).strip()
    if a == b:
        return True
    try:
        if data_type == "Bool":
            truth = {"1": True, "true": True, "0": False, "false": False}
            return truth.get(a.lower()) is not None and truth.get(a.lower()) == truth.get(b.lower())
        if data_type in ("Int", "Float", "EntityId"):
            return abs(float(a) - float(b)) < 1e-6
        if data_type == "Vec3":
            x = [float(v) for v in a.split(",")]
            y = [float(v) for v in b.split(",")]
            return len(x) == len(y) and all(abs(p - q) < 1e-5 for p, q in zip(x, y))
    except ValueError:
        return False
    return False


def compare(path, row, fnodes, fedges, disabled, cat):
    """The engine's row for one graph against the file and the catalog -> [finding]."""
    by_type = {t["type"].lower(): t for t in cat}
    findings = []
    if not row.get("xml_loaded"):
        return [_finding("error", "F001", path, "the engine could not read this file as XML (missing, or not well-formed)", "check the path (it is a CryPak path: libs/flowgraphs/...) and the XML")]
    for nid, fn in fnodes.items():
        t = by_type.get(fn["class"].lower())
        if t is None:
            findings.append(_finding("error", "F003", "%s node %s" % (path, nid), "Class '%s' is not a registered node type: the engine discards the whole graph" % fn["class"], "search the catalog (flow_nodes)"))
            continue
        if t["category"] in ("debug", "obsolete"):
            findings.append(_finding("info", "F008", "%s node %s" % (path, nid), "'%s' is flagged %s%s" % (fn["class"], t["category"], " (the editor does not offer obsolete nodes)" if t["category"] == "obsolete" else "")))
        ports = {p["name"] for p in t.get("inputs", [])} | {_stripped(p["name"]) for p in t.get("inputs", [])}
        for attr in fn["inputs"]:
            if attr not in ports:
                findings.append(_finding("warning", "F004", "%s node %s" % (path, nid), "<Inputs %s=...> is not an input port of '%s' (ports: %s): the engine ignores it" % (attr, fn["class"], ", ".join(p["name"] for p in t.get("inputs", []))[:120]),
                                         "use a port name from the catalog"))
    for a, ap, b, bp in fedges:
        fa, fb = fnodes.get(a), fnodes.get(b)
        ta = by_type.get(fa["class"].lower()) if fa else None
        tb = by_type.get(fb["class"].lower()) if fb else None
        if fa is None or fb is None:
            findings.append(_finding("error", "F005", path, "edge <%s,%s> -> <%s,%s> names a node that is not in the file: the engine discards the graph" % (a, ap, b, bp)))
        else:
            if ta and ap not in {p["name"] for p in ta.get("outputs", [])} | {_stripped(p["name"]) for p in ta.get("outputs", [])}:
                findings.append(_finding("error", "F005", path, "edge from node %s: '%s' has no output port '%s' (outputs: %s): the engine discards the graph" % (a, fa["class"], ap, ", ".join(p["name"] for p in ta.get("outputs", []))[:120])))
            if tb and bp not in {p["name"] for p in tb.get("inputs", [])} | {_stripped(p["name"]) for p in tb.get("inputs", [])}:
                findings.append(_finding("error", "F005", path, "edge into node %s: '%s' has no input port '%s' (inputs: %s): the engine discards the graph" % (b, fb["class"], bp, ", ".join(p["name"] for p in tb.get("inputs", []))[:120])))
    if not row.get("ok"):
        why = [re.sub(r"\$\d", "", l).strip() for l in row.get("log", []) if "[flow]" in l or "FLOWGRAPH" in l]
        findings.append(_finding("error", "F002", path, "the engine discarded this graph%s" % (": " + "; ".join(why)[:300] if why else " (no reason logged)"), "fix what the reason names; the loader gives up at the first bad node or edge"))
        return findings
    eng = {n["name"]: n for n in row.get("nodes", [])}
    for nid, fn in fnodes.items():
        en = eng.get(nid)
        t = by_type.get(fn["class"].lower())
        if en is None or t is None:
            continue
        types = {p["name"]: p["data_type"] for p in t.get("inputs", [])}
        values = {p["name"]: p["value"] for p in en["inputs"]}
        for attr, val in fn["inputs"].items():
            port = attr if attr in values else next((p["name"] for p in t.get("inputs", []) if _stripped(p["name"]) == attr), None)
            if port is None or not str(val).strip():
                continue
            if not same_value(val, values.get(port, ""), types.get(port, "String")):
                findings.append(_finding("warning", "F006", "%s node %s" % (path, nid), "<Inputs %s=\"%s\"> is held as \"%s\" (port type %s): the value did not convert" % (attr, val, values.get(port, ""), types.get(port, "?")),
                                         "write the value in the port's type"))
    if len(row.get("nodes", [])) != len(fnodes) or len(row.get("edges", [])) != len(fedges):
        findings.append(_finding("warning", "F007", path, "the file gives %d nodes and %d enabled edges (%d disabled), the engine holds %d and %d" % (len(fnodes), len(fedges), disabled, len(row.get("nodes", [])), len(row.get("edges", [])))))
    return findings


def check(paths=None, code_dir=None, refresh=False):
    """paths: graph files relative to Assets (default libs/FlowGraphs/*.xml). -> {ok, errors, warnings, findings, graphs: [{file, loaded, nodes, edges}], game_nodes}"""
    import bridge_py
    cat = catalog(refresh)["types"]
    paths = [p.replace(chr(92), "/") for p in (paths or project_graphs())]
    os.makedirs(SCRATCH, exist_ok=True)
    findings, graphs = [], []
    for path in paths:
        out = os.path.join(SCRATCH, "graph_%d.json" % os.getpid())
        if os.path.exists(out):
            os.remove(out)
        r = bridge_py.send("bridge.flow.check %s | %s" % (_slash(out), path), 120)
        if not r.get("ok"):
            raise FlowError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
        with open(out, encoding="utf-8") as f:
            row = json.load(f)
        os.remove(out)
        try:
            fnodes, fedges, disabled = file_graph(path)
        except (ET.ParseError, OSError):
            fnodes, fedges, disabled = {}, [], 0
        findings.extend(compare(path, row, fnodes, fedges, disabled, cat))
        graphs.append({"file": path, "loaded": bool(row.get("ok")), "nodes": len(row.get("nodes", [])), "edges": len(row.get("edges", []))})
    names = {t["type"].lower() for t in cat}
    game = code_nodes(code_dir)
    for t, where in sorted(game.items()):
        if t.lower() not in names:
            findings.append(_finding("error", "F009", where, "the game registers flow node '%s' and the engine's flow system does not list it (in the editor the game's nodes are handed over by RegisterGameFlowNodes)" % t))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "graphs": graphs, "game_nodes": game}


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("nodes", "node", "check"):
        print(__doc__)
        return
    if argv[0] == "nodes":
        rows, total = search(" ".join(argv[1:]))
        print("%d match(es)" % total)
        for r in rows:
            print("  %-44s %-9s in: %s | out: %s" % (r["type"], r["category"], ",".join(r["inputs"])[:60], ",".join(r["outputs"])[:40]))
    elif argv[0] == "node":
        row = node(" ".join(argv[1:]))
        print(json.dumps(row, indent=1) if row else "no such node type")
    else:
        r = check(argv[1:] or None)
        print("%s: %d error(s), %d warning(s); graphs %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["graphs"]))
        for f in r["findings"]:
            print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"][:260]))


if __name__ == "__main__":
    main()
