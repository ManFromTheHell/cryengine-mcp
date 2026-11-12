#!/usr/bin/env python3
"""Self-test of bt_check.py: the source and code scanners on synthetic input (offline), then, with the editor open, the engine's node registry and the villager's tree through the engine's own
loaders, with deliberately broken copies (positive controls) and the unchanged tree as the control. The copies live in engine_data/bt_scratch and are deleted. About 20 s live.

    test_bt_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import bt_check as B

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(r, text=""):
    return sorted({f["code"] for f in r["findings"] if text in f["message"]})


def main():
    if not os.path.isdir(B.ENGINE_CODE):
        print("  --   no engine source at %s (set CRYENGINE_SOURCE): the node-registry check is skipped" % B.ENGINE_CODE)
        reg = None
    else:
        reg = B.registered_in_source()
    if reg is not None:
        check("the engine's source registers 60+ node types in the three macro forms (Sequence, Wait, Basic_MoveToPosition by name, Melee in GameDll)", len(reg) >= 60 and {"Sequence", "Wait", "Basic_MoveToPosition", "Melee"} <= set(reg) and "GameSDK" in reg["Melee"] and "nodetype" not in reg, len(reg))
    k = B.bb_keys_in(ET.fromstring('<T><Basic_MoveToPosition bbDestination="Waypoint" speed="Walk"/><X bbOther="K2"/><Y name="n"/></T>'))
    check("blackboard keys are the attributes that start with bb (bbDestination, bbOther), nothing else", sorted(k) == ["K2", "Waypoint"], k)
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "a.cpp"), "w").write('void f(){ SendBehaviorTreeEvent(m_x ? "Seen" : "Heard"); BehaviorTree::Event ev("Lost"); b.SetVariable<Vec3>(BehaviorTree::BlackboardVariableId("Waypoint"), v); }\n')
        ev, keys = B.code_names(d)
        check("the code scanner reads both branches of a ternary event, an Event constructor and a blackboard key id", sorted(ev) == ["Heard", "Lost", "Seen"] and sorted(keys) == ["Waypoint"], (sorted(ev), sorted(keys)))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    ev, keys = B.code_names()
    if ev:
        check("the game's own code: it sends PlayerSeen, PlayerHeard, PlayerLost and writes the blackboard key Waypoint", {"PlayerSeen", "PlayerHeard", "PlayerLost"} <= set(ev) and "Waypoint" in keys, (sorted(ev), sorted(keys)))
    else:
        print("  --   the project's Code folder holds no behavior-tree events: the game-code check is skipped")

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live:
        rows = B.catalog(refresh=True)
        by = {r["name"]: r for r in rows}
        made = sum(1 for r in rows if r["created"])
        check("the engine's node factory creates 56 of the 65 registered types; the 9 it cannot are all GameSDK GameDll nodes (not loaded by this game)", made == 56 and all("GameSDK" in r["source"] for r in rows if not r["created"]) and not by["Melee"]["created"], (made, len(rows)))
        check("a node describes itself with its attributes and defaults: Wait duration/variation, Log message, Aim angleThreshold 90", by["Wait"]["attributes"] == {"duration": "0", "variation": "0"} and by["Log"]["attributes"] == {"message": ""} and by["Aim"]["attributes"]["angleThreshold"] == "90", by["Wait"]["attributes"])
        check("the engine cannot describe the component-safe nodes: Basic_MoveToPosition comes back as a bare <Node> (an engine gap: no attributes can be listed)", by["Basic_MoveToPosition"]["xml_tag"] == "Node" and not by["Basic_MoveToPosition"]["attributes"], by["Basic_MoveToPosition"]["xml_tag"])
        check("an unknown name is found by a search on a word of its source file", any(r["name"] == "Wait" for r in B.node_search("wait")) and not B.node_search("zzz_nothing_like_this"), "")

        base = open(os.path.join(B.TREE_DIR, "Villager_Patrol.xml"), encoding="utf-8").read()
        os.makedirs(B.SCRATCH, exist_ok=True)
        made_files = []

        def variant(name, text):
            assert text != base, name
            p = os.path.join(B.SCRATCH, "t_%s.xml" % name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            made_files.append(p)
            return B.check(p)
        try:
            good = B.check()
            check("control: the villager's tree loads strictly AND leniently, has no finding, and its 8 node kinds are all registered", good["ok"] and good["strict"]["ok"] and not good["findings"] and set(good["nodes_used"]) == {"StateMachine", "Loop", "SuppressFailure", "Sequence", "Log", "Basic_MoveToPosition", "Wait", "Halt"}, good["findings"][:2])
            r1 = variant("nosuchnode", base.replace("<Halt/>", "<NoSuchNode/>"))
            check("positive control: an unknown node type REFUSES the tree in strict mode (the game would run no behaviour) and is named by the engine and by the registry", not r1["strict"]["ok"] and r1["lenient"]["ok"] and {"B001", "B030"} <= set(codes(r1)) and any("NoSuchNode" in l for l in r1["strict"]["log"]), codes(r1))
            r2 = variant("bogusattr", base.replace('<Wait duration="1.5"/>', '<Wait duration="1.5" bogus="3"/>'))
            check("positive control: an unknown attribute is IGNORED by the strict load (it succeeds) and only the file-versus-engine comparison finds it", r2["strict"]["ok"] and any(f["code"] == "B011" and "bogus" in f["message"] for f in r2["findings"]), codes(r2))
            r3 = variant("badvalue", base.replace('<Wait duration="1.5"/>', '<Wait duration="abc"/>'))
            check("positive control: a duration that is not a number loads strictly and the engine holds 0 (reported as changed)", r3["strict"]["ok"] and any(f["code"] == "B012" and "'abc'" in f["message"] for f in r3["findings"]), codes(r3))
            r4 = variant("badtarget", base.replace('to="Watch" onEvent="PlayerSeen"', 'to="Nowhere" onEvent="PlayerSeen"'))
            check("positive control: a transition to a state that does not exist refuses the tree and the engine says which one", not r4["strict"]["ok"] and any("Nowhere" in l for l in r4["strict"]["log"]), r4["strict"]["log"][:1])
            r5 = variant("gamednode", base.replace("<Halt/>", "<Melee/>"))
            check("positive control: a GameSDK GameDll node (Melee) is registered in the source but not created here: error B031 and the strict load refuses", not r5["strict"]["ok"] and "B031" in codes(r5), codes(r5))
            r6 = variant("undeclaredvar", base.replace("<Halt/>", '<IfCondition condition="NotDeclared"><Halt/></IfCondition>'))
            check("positive control: a condition on a variable the tree does not declare refuses the tree", not r6["strict"]["ok"] and any("NotDeclared" in l for l in r6["strict"]["log"]), r6["strict"]["log"][:1])
            r7 = variant("badkey", base.replace('bbDestination="Waypoint"', 'bbDestination="Nowhere"'))
            check("positive control: a node that reads a blackboard key no C++ writes is reported (B022), though the tree loads", r7["strict"]["ok"] and any(f["code"] == "B022" and "Nowhere" in f["message"] for f in r7["findings"]), codes(r7))
            r8 = variant("ghostevent", base.replace('onEvent="PlayerLost"', 'onEvent="GhostEvent"'))
            check("positive control: an event no code sends is only a WARNING from the engine ('declared automatically'), found by the code comparison (B021) and the log (B002)", r8["strict"]["ok"] and {"B002", "B021"} <= set(codes(r8)) and any("GhostEvent" in f["message"] for f in r8["findings"]), codes(r8))
            r9 = variant("lostevent", base.replace('\t\t<Event name="PlayerHeard"/>\n', "").replace('\t\t<Signal name="PlayerHeard" variable="AwareOfPlayer" value="true"/>\n', "").replace('\t\t\t\t\t<Transition to="Watch" onEvent="PlayerHeard"/>\n', ""))
            check("positive control: an event the code sends (PlayerHeard) that the tree no longer declares or handles is reported (B020)", any(f["code"] == "B020" and "PlayerHeard" in f["message"] for f in r9["findings"]), codes(r9))
        finally:
            for p in made_files + [os.path.join(B.SCRATCH, f) for f in os.listdir(B.SCRATCH) if f.startswith("t_")]:
                if os.path.exists(p):
                    os.remove(p)
        left = [f for f in os.listdir(B.SCRATCH) if f.startswith("t_")]
        check("the scratch folder holds none of the test trees afterwards", not left, left)

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
