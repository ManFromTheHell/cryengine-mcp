#!/usr/bin/env python3
"""Behaviour trees as the ENGINE loads them (ENGINE_LEARNING_PLAN D2), without running the game.

Two plugin verbs call the engine's own behaviour tree machinery from the open editor:

  bridge.bt.nodes   asks the node factory (the registry: CryAISystem's Core, AI and Basic nodes, plus what the game plugin registers) to CREATE each candidate node type and returns the
                    node's own XML description (its tag and every attribute with its default). The candidates are read from the engine's REGISTER_BEHAVIOR_TREE_NODE lines in the source, so a
                    type the engine source registers but this editor cannot create (the GameSDK's game nodes live in GameDll) is reported as such.
  bridge.bt.load    loads a tree file with the same steps as BehaviorTreeManager::LoadBehaviorTreeTemplate, STRICT (what the game does: any error refuses the tree) and LENIENT (what the Behavior
                    Tree Editor does) and returns the engine's own XML description of the tree it kept, plus the log lines.

    python checks/bt_check.py nodes [text]            the node types with their attributes and defaults
    python checks/bt_check.py check <tree.xml|name>    a tree (default: Villager_Patrol)

The game's C++ is read too: the events it sends (SendBehaviorTreeEvent, BehaviorTree::Event) and the blackboard keys it writes (BlackboardVariableId) are compared with what the tree declares and reads.
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
SCRATCH = os.path.join(DATA, "bt_scratch")
NODES_FILE = os.path.join(DATA, "bt_nodes.json")
# CRYENGINE_SOURCE: the engine source checkout (the folder holding Code); read-only reference for the node registry
ENGINE_CODE = os.path.join(_paths.ENGINE_SOURCE, "Code") if _paths.ENGINE_SOURCE else ""   # "" when CRYENGINE_SOURCE is unset
TREE_DIR = os.path.join(ASSETS, "scripts", "AI", "BehaviorTrees")
DEFAULT_TREE = "Villager_Patrol"

sys.path.insert(0, HERE)


class BtError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


# ---------------------------------------------------------------- the node registry

REG_RES = [re.compile(r"REGISTER_BEHAVIOR_TREE_NODE\(\s*\w+\s*,\s*(\w+)\s*\)"),
           re.compile(r"REGISTER_BEHAVIOR_TREE_NODE_WITH_SERIALIZATION\(\s*\w+\s*,\s*(\w+)\s*,"),
           re.compile(r"REGISTER_BEHAVIOR_TREE_NODE_WITH_NAME\(\s*\w+\s*,\s*\w+\s*,\s*\"([^\"]+)\"")]


def registered_in_source(engine_code=None):
    """{node type name: relative source file}: every node type the engine's source registers (the three REGISTER_BEHAVIOR_TREE_NODE forms). Read-only reference; the engine is not changed."""
    root = engine_code or ENGINE_CODE
    out = {}
    if not root:   # CRYENGINE_SOURCE unset: no source to read; the catalog then relies on the editor alone
        return out
    for sub in ("CryEngine", "CryPlugins", "GameSDK"):
        for d, _, files in os.walk(os.path.join(root, sub)):
            for fn in files:
                if not fn.endswith((".cpp", ".h")):
                    continue
                path = os.path.join(d, fn)
                try:
                    text = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if "REGISTER_BEHAVIOR_TREE_NODE" not in text:
                    continue
                for rx in REG_RES:
                    for m in rx.finditer(text):
                        out.setdefault(m.group(1), os.path.relpath(path, root).replace(chr(92), "/"))
    out.pop("nodetype", None)                         # the macro definitions themselves
    return out


def _attrs(xml_text):
    try:
        el = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, {}
    return el.tag, dict(el.attrib)


def catalog(refresh=False, extra=()):
    """[{name, created, source, attributes: {name: default}}]: what the engine's node factory can create. Cached in engine_data/bt_nodes.json; refresh=True asks the editor again. extra: more
    type names to probe (a tag from a tree file, for instance)."""
    if not refresh and not extra and os.path.isfile(NODES_FILE):
        with open(NODES_FILE, encoding="utf-8") as f:
            return json.load(f)
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    src = registered_in_source()
    names = sorted(set(src) | set(extra))
    names_file, out = os.path.join(SCRATCH, "names.txt"), os.path.join(SCRATCH, "nodes_raw.json")
    with open(names_file, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(names) + "\n")
    r = bridge_py.send("bridge.bt.nodes %s | %s" % (_slash(out), _slash(names_file)), 120)
    if not r.get("ok"):
        raise BtError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        raw = json.load(f)
    rows = []
    for it in raw:
        tag, attrs = _attrs(it["xml"]) if it["xml"] else (None, {})
        rows.append({"name": it["name"], "created": it["created"], "source": src.get(it["name"], ""), "xml_tag": tag, "attributes": attrs})
    rows.sort(key=lambda r: r["name"].lower())
    if not extra and src:                             # never cache a registry built without the engine source (it would stick until refresh=True)
        os.makedirs(DATA, exist_ok=True)
        with open(NODES_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    return rows


def node_search(text="", refresh=False):
    t = (text or "").lower()
    out = []
    for r in catalog(refresh):
        hay = r["name"].lower() + " " + " ".join(r["attributes"]).lower() + " " + r["source"].lower()
        if t in hay:
            out.append(r)
    return out


# ---------------------------------------------------------------- the code's side

EVENT_RES = [re.compile(r'SendBehaviorTreeEvent\(\s*"([^"]+)"'), re.compile(r'BehaviorTree::Event\s+\w+\(\s*"([^"]+)"'), re.compile(r'BehaviorTree::Event\(\s*"([^"]+)"'),
             re.compile(r'\?\s*"([A-Za-z0-9_]+)"\s*:\s*"([A-Za-z0-9_]+)"\s*\)\s*;')]
KEY_RES = [re.compile(r'BlackboardVariableId\(\s*"([^"]+)"')]


def code_names(code_dir=None):
    """({event: [file:line]}, {blackboard key: [file:line]}) from the game's C++ string literals. The ternary form SendBehaviorTreeEvent(cond ? "A" : "B") is read too."""
    code_dir = code_dir or CODE
    events, keys = {}, {}
    for root, _, files in os.walk(code_dir):
        for fn in files:
            if not fn.endswith((".cpp", ".h")):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, PROJECT).replace(chr(92), "/")
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                if "SendBehaviorTreeEvent" in line and "::" not in line.split("SendBehaviorTreeEvent")[0][-3:]:
                    call = line.split("SendBehaviorTreeEvent", 1)[1]
                    call = call[:call.find(")") + 1] if ")" in call else call            # only the call's own arguments, not a key id further along the line
                    for q in re.findall(r'"([A-Za-z0-9_]+)"', call):
                        events.setdefault(q, []).append("%s:%d" % (rel, n))
                for rx in EVENT_RES[:3]:
                    for m in rx.finditer(line):
                        events.setdefault(m.group(1), []).append("%s:%d" % (rel, n))
                for rx in KEY_RES:
                    for m in rx.finditer(line):
                        keys.setdefault(m.group(1), []).append("%s:%d" % (rel, n))
    return events, keys


# ---------------------------------------------------------------- loading a tree


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _resolve(tree):
    if os.path.isfile(tree):
        return os.path.abspath(tree)
    for cand in (os.path.join(PROJECT, tree), os.path.join(TREE_DIR, tree), os.path.join(TREE_DIR, tree + ".xml")):
        if os.path.isfile(cand):
            return cand
    raise BtError("no behaviour tree file %r (looked in the project and Assets/scripts/AI/BehaviorTrees)" % tree)


def bb_keys_in(tree_root):
    """{key: [node tag]}: attributes that name a blackboard key (bbDestination, bbKey, ... : the attribute name starts with 'bb')."""
    out = {}
    for el in tree_root.iter():
        for a, v in el.attrib.items():
            if a.lower().startswith("bb") and v:
                out.setdefault(v, []).append("%s.%s" % (el.tag, a))
    return out


def check(tree=DEFAULT_TREE, code_dir=None):
    """-> {ok, errors, warnings, findings: [{severity, code, where, message, fix}], strict, lenient, nodes_used: {type: count}, events: {...}, engine_xml: path}"""
    import bridge_py
    from mannequin_check import diff_documents, merge_fragment_elements          # noqa: F401 (the same file-versus-engine comparison)
    path = _resolve(tree)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise BtError("%s is not well-formed XML (%s): the engine skips such a file" % (path, exc))
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "load_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.bt.load %s | %s" % (_slash(out), _slash(path)), 90)
    if not r.get("ok"):
        raise BtError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        eng = json.load(f)
    os.remove(out)
    name = os.path.basename(path)
    findings = []
    clean = lambda lines: [re.sub(r"\$\d", "", l).strip() for l in lines if l.strip()]
    strict_log, lenient_log = clean(eng["strict"]["log"]), clean(eng["lenient"]["log"])
    if not eng["strict"]["ok"]:
        findings.append(_finding("error", "B001", name, "the engine REFUSES this tree when the game loads it (strict mode): the entity gets no tree and the behaviour never runs", "read the engine's lines below"))
    for line in strict_log:
        if re.search(r"error|fail|invalid|unknown|missing|unexpected|expected", line, re.I):
            findings.append(_finding("error" if re.search(r"error|fail", line, re.I) else "warning", "B002", name, line))
    if eng["strict"]["ok"] and not eng["lenient"]["ok"]:
        findings.append(_finding("warning", "B003", name, "strict load succeeded but the lenient (editor) load failed: unexpected, look at the lenient log"))

    # the registry first: a node type the engine describes under another tag (Basic_MoveToPosition -> <Node>, SendTransitionSignal -> <Signal>, AnimationTagWrapper -> <Decorator>) has no
    # faithful description to compare with, so a difference on it says nothing about the file
    try:
        reg = {c["name"]: c for c in catalog()}
    except (BtError, OSError):
        reg = {}
    undescribed = {n for n, c in reg.items() if c["created"] and c["xml_tag"] != n}
    skipped = []

    def _undescribed(path):
        last = re.sub(r"#\d+$", "", path.rsplit("/", 1)[-1])
        if last in undescribed:
            skipped.append(last)
            return True
        return False

    # what the engine kept of the tree, against the file
    eng_xml = eng["lenient"].get("xml") or ""
    saved = os.path.join(SCRATCH, os.path.splitext(name)[0] + ".engine.xml")
    if eng_xml:
        with open(saved, "w", encoding="utf-8", newline="") as f:
            f.write(eng_xml)
        file_root = root.find("Root")
        first = file_root[0] if file_root is not None and len(file_root) else None
        if first is not None:
            d = diff_documents(first, ET.fromstring(eng_xml))
            for p in d["dropped"]:
                if not _undescribed(p):
                    findings.append(_finding("warning", "B010", name, "in the file, not in what the engine kept: %s" % re.sub(r"#\d+", "", p)))
            for p, attr, v in d["dropped_attrs"]:
                if not _undescribed(p):
                    findings.append(_finding("warning", "B011", name, "%s: attribute %s=%r is not in what the engine kept (an unknown attribute: ignored silently)" % (re.sub(r"#\d+", "", p), attr, v)))
            for p, attr, was, now in d["changed"]:
                if not _undescribed(p):
                    findings.append(_finding("warning", "B012", name, "%s: %s is %r in the file and %r in the engine (a value that is not a number or a name is read as 0 or empty)" % (re.sub(r"#\d+", "", p), attr, was, now)))

    # events and blackboard keys against the game's code
    sent, written = code_names(code_dir)
    declared = {e.get("name") for e in root.iter("Event") if e.get("name")}
    handled = {t.get("onEvent") for t in root.iter("Transition") if t.get("onEvent")} | {s.get("name") for s in root.iter("Signal") if s.get("name")}
    for ev in sorted(sent):
        if ev not in declared and ev not in handled:
            findings.append(_finding("warning", "B020", sent[ev][0], "the code sends event '%s' to the tree, which neither declares nor handles it (the event is ignored)" % ev, "declare it under <Events><GameEvents> and use it in a Transition or a signal"))
    for ev in sorted(declared | handled):
        if ev not in sent and sent:
            findings.append(_finding("info", "B021", name, "the tree declares or handles event '%s' and no C++ literal sends it" % ev))
    used_keys = bb_keys_in(root)
    for key, where in sorted(used_keys.items()):
        if key not in written:
            findings.append(_finding("warning", "B022", name, "%s reads blackboard key '%s', which no C++ writes (BlackboardVariableId literal): the node fails" % (", ".join(where), key), "write it with Blackboard::SetVariable before the node runs"))

    # which node types the tree uses, against the engine's registry
    used = {}
    for el in root.iter():
        used[el.tag] = used.get(el.tag, 0) + 1
    structural = {"BehaviorTree", "Variables", "Variable", "Events", "GameEvents", "Event", "SignalVariables", "Signal", "Timestamps", "Timestamp", "Root", "MetaExtensions", "Transitions", "Transition", "State"}
    nodes_used = {t: n for t, n in used.items() if t not in structural}
    for t in sorted(nodes_used):
        if reg and t not in reg:
            findings.append(_finding("error", "B030", name, "'%s' is not a node type the engine registers (checked against the source's REGISTER_BEHAVIOR_TREE_NODE lines)" % t))
        elif reg and not reg[t]["created"]:
            findings.append(_finding("error", "B031", name, "'%s' is registered by %s, which this engine process does not load: the game would refuse the tree" % (t, reg[t]["source"] or "the game plugin")))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "strict": {"ok": eng["strict"]["ok"], "log": strict_log}, "lenient": {"ok": eng["lenient"]["ok"], "log": lenient_log},
            "nodes_used": nodes_used, "events": {"sent_by_code": sorted(sent), "declared": sorted(declared), "handled": sorted(handled)}, "blackboard_keys": {"read_by_tree": sorted(used_keys), "written_by_code": sorted(written)},
            "engine_xml": _slash(saved) if eng_xml else "", "not_comparable": sorted(set(skipped))}


def main():
    a = sys.argv[1:]
    if not a or a[0] == "check":
        r = check(a[1] if len(a) > 1 else DEFAULT_TREE)
        print("%s: %d error(s), %d warning(s); strict load %s, lenient load %s; nodes %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], "ok" if r["strict"]["ok"] else "REFUSED",
                                                                                          "ok" if r["lenient"]["ok"] else "failed", r["nodes_used"]))
        for f in r["findings"]:
            print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"]))
    elif a[0] == "nodes":
        rows = node_search(a[1] if len(a) > 1 else "")
        print("%d node types (%d the engine can create)" % (len(rows), sum(1 for r in rows if r["created"])))
        for r in rows:
            print("%-40s %-9s %s" % (r["name"], "" if r["created"] else "NOT HERE", ", ".join("%s=%s" % (k, v) for k, v in r["attributes"].items())[:120]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
