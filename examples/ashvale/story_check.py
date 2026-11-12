#!/usr/bin/env python3
"""Is the story solvable? An offline check of the game's quest logic: Assets/libs/ashvale/dialogue.xml (nodes, choices, flags), the level layers (who speaks which dialogue, what a death, a chest, a
locked gate or a doorway sets or needs) and the game's own flag names (Code/Components/*.cpp). No editor, no game; about a second.

    python examples/ashvale/story_check.py [goal flag ...]       default goals: wat_dead has_tithe tithe_returned entered_undercroft saw_vault vault_key has_lordsword

It plays the story as a search over STATES (the set of flags set so far): start with no flags, let every event whose conditions hold set its flag (a death sets its DeathFlag, a chest its has_<item>, a gate its SeenFlag, a doorway
opens the level behind it), let every conversation that can be reached say everything it can say (each choice sets its flags and runs its action, the next node is the FIRST node with that id
whose `when` holds, exactly as CAshvaleGame::FindNode picks it), and keep every new state until none is left (a conversation says different things in different states, so a fixed point of the final flags is not enough). Then:

    S001 error    a flag a `when` reads that nothing can set (a typo, or a flag that was renamed)
    S002 error    a choice goes to a node that does not exist
    S003 error    a dialogue with no unconditional `start` node: in some state its NPC would say nothing
    S004 error    a goal flag the story can never reach (the quest chain is broken)
    S005 warning  a node that can never be picked: its `when` contradicts itself, or an earlier node with the same id holds whenever it does
    S006 error    an NPC names a dialogue that does not exist
    S007 info     a dialogue no talkable NPC speaks
    S008 info     a flag set but never read by a dialogue, an entity or the game's code
Limits (honest): coins>=N and bread>=N are taken as satisfiable; a flag cleared with set="x=0" or has_tithe -> 0 by return_tithe is treated as still set (the check is monotone: it
says what CAN happen, not that the player will do it in a good order); whether the NPC can be reached on foot is the navmesh's business (nav_reach).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
sys.path.insert(0, HERE)

# the story data of the game being checked: CRYENGINE_DIALOGUE = its dialogue.xml, CRYENGINE_FIRST_LEVEL = the level folder the story starts in (defaults: the Ashvale sample game)
DIALOGUE = os.environ.get("CRYENGINE_DIALOGUE") or os.path.join(PROJECT, "Assets", "libs", "ashvale", "dialogue.xml")
LEVELS = os.path.join(PROJECT, "Assets", "levels")
CODE = os.path.join(PROJECT, "Code", "Components")
FIRST_LEVEL = os.environ.get("CRYENGINE_FIRST_LEVEL", "ashvale")
MAX_STATES = 60000
DEFAULT_GOALS = ["wat_dead", "has_tithe", "tithe_returned", "entered_undercroft", "saw_vault", "vault_key", "has_lordsword"]
# CAshvaleGame::ApplyAction: the flags an action sets (the others change coins, bread or health only)
ACTION_FLAGS = {"return_tithe": {"tithe_returned"}, "give_blade": {"has_blade"}, "feed_martha": {"fed_martha"}}


class StoryError(ValueError):
    pass


def _finding(severity, code, where, message):
    return {"severity": severity, "code": code, "where": where, "message": message}


def terms(conditions):
    """'a,!b,coins>=3' -> [(name, negated, numeric)]"""
    out = []
    for t in (conditions or "").split(","):
        t = t.strip()
        if not t:
            continue
        if ">=" in t:
            out.append((t.split(">=")[0], False, True))
        else:
            out.append((t.lstrip("!"), t.startswith("!"), False))
    return out


def holds(conditions, flags):
    for name, neg, numeric in terms(conditions):
        if numeric:
            continue                                   # coins / bread: assumed available
        if (name in flags) == neg:
            return False
    return True


def read_dialogues(path=DIALOGUE):
    """-> {id: [node]}, node = {id, when, speaker, text, choices: [{text, goto, when, set, action}], index}"""
    if not os.path.isfile(path):
        raise StoryError("no dialogue file at %s: story_check needs the game's dialogue.xml (Assets/libs/<game>/dialogue.xml); set CRYENGINE_DIALOGUE to its path" % path)
    root = ET.parse(path).getroot()
    out = {}
    for d in root:
        nodes = []
        for j, n in enumerate(d):
            choices = [{"text": (c.text or "").strip(), "goto": c.get("goto", ""), "when": c.get("when", ""), "set": c.get("set", ""), "action": c.get("action", "")} for c in n if c.tag == "Choice"]
            text = " ".join((t.text or "").strip() for t in n if t.tag == "Text") or (n.text or "").strip()
            nodes.append({"id": n.get("id", ""), "when": n.get("when", ""), "speaker": n.get("speaker", ""), "text": text, "choices": choices, "index": j})
        out[d.get("id", "")] = nodes
    return out


def sets_of(spec):
    out = set()
    for t in (spec or "").split(","):
        t = t.strip()
        if not t:
            continue
        name, _, val = t.partition("=")
        if not val or int(val or 0) > 0:
            out.add(name)
    return out


def read_world(levels=(FIRST_LEVEL, "ashvale_undercroft"), levels_dir=LEVELS):
    """-> {"npcs": [{name, level, dialogue, talkable}], "events": [{kind, name, level, flag, requires}], "doors": [{name, level, target, requires}], "reads": set of flags the entities read}"""
    import entity_check as EC
    npcs, events, doors, reads = [], [], [], set()
    for level in levels:
        if not os.path.isdir(os.path.join(levels_dir, level, "layers")):
            continue
        for e in EC.read_layers(level, levels_dir):
            for c in e["components"]:
                p = c["props"].attrib if c["props"] is not None else {}
                if p.get("DialogueId"):
                    npcs.append({"name": e["name"], "level": level, "dialogue": p["DialogueId"], "talkable": bool(p.get("TalkName") or p.get("DisplayName") or "CTalkable" in c["type"])})
                if p.get("DeathFlag"):
                    events.append({"kind": "death", "name": e["name"], "level": level, "flag": p["DeathFlag"], "requires": ""})
                if p.get("Contents") is not None:
                    for item in p["Contents"].split(","):
                        name = item.strip().split(":")[0]
                        if name and name not in ("coins", "bread"):
                            events.append({"kind": "chest", "name": e["name"], "level": level, "flag": "has_" + name, "requires": p.get("RequiresFlag", "")})
                if p.get("SeenFlag"):
                    events.append({"kind": "gate", "name": e["name"], "level": level, "flag": p["SeenFlag"], "requires": ""})
                if p.get("TargetLevel"):
                    doors.append({"name": e["name"], "level": level, "target": p["TargetLevel"], "requires": p.get("RequiresFlag", "")})
                for key in ("RequiresFlag",):
                    for name, _neg, _num in terms(p.get(key, "")):
                        reads.add(name)
    return {"npcs": npcs, "events": events, "doors": doors, "reads": reads}


def code_flags(code_dir=CODE):
    """(flags the game's code sets, flags it reads) from the string literals of SetFlag("x") / GetFlag("x")"""
    sets, reads = set(), set()
    if os.path.isdir(code_dir):
        for f in os.listdir(code_dir):
            if f.endswith((".cpp", ".h")):
                text = open(os.path.join(code_dir, f), encoding="utf-8", errors="replace").read()
                sets |= set(re.findall(r'SetFlag\("([A-Za-z0-9_]+)"', text))
                reads |= set(re.findall(r'GetFlag\("([A-Za-z0-9_]+)"', text))
    return sets, reads


def entered_flag(level):
    return "entered_" + (level.split("_", 1)[1] if "_" in level else level)


def check(goals=None, dialogues=None, world=None, code=None, first_level=FIRST_LEVEL):
    goals = list(DEFAULT_GOALS if goals is None else goals)
    dialogues = read_dialogues() if dialogues is None else dialogues
    world = read_world() if world is None else world
    csets, creads = code_flags() if code is None else code
    findings = []

    def first(nodes, node_id, flags):
        for n in nodes:
            if n["id"] == node_id and holds(n["when"], flags):
                return n
        return None

    # --- structure
    can_set = set(csets) | {f for e in world["events"] for f in [e["flag"]]} | {entered_flag(d["target"]) for d in world["doors"]}
    for acts in ACTION_FLAGS.values():
        can_set |= acts
    for did, nodes in dialogues.items():
        ids = {n["id"] for n in nodes}
        if not any(n["id"] == "start" and not n["when"] for n in nodes):
            findings.append(_finding("error", "S003", did, "no `start` node without a `when`: in a state none of the others covers this NPC says nothing"))
        for n in nodes:
            for c in n["choices"]:
                can_set |= sets_of(c["set"])
    for did, nodes in dialogues.items():
        ids = {n["id"] for n in nodes}
        for n in nodes:
            where = "%s/%s#%d" % (did, n["id"], n["index"])
            for name, _neg, numeric in terms(n["when"]):
                if not numeric and name not in can_set:
                    findings.append(_finding("error", "S001", where, "`when` reads the flag %r and nothing in the dialogues, the levels or the game's code sets it" % name))
            for c in n["choices"]:
                for name, _neg, numeric in terms(c["when"]):
                    if not numeric and name not in can_set:
                        findings.append(_finding("error", "S001", where + " choice %r" % c["text"][:30], "`when` reads the flag %r and nothing sets it" % name))
                if c["goto"] not in ("", "end") and c["goto"] not in ids:
                    findings.append(_finding("error", "S002", where, "a choice goes to %r, which is not a node of this dialogue" % c["goto"]))
    for e in world["events"] + world["doors"]:
        for name, _neg, numeric in terms(e.get("requires", "")):
            if not numeric and name not in can_set:
                findings.append(_finding("error", "S001", "%s (%s)" % (e["name"], e["level"]), "requires the flag %r and nothing sets it" % name))
    known = set(dialogues)
    for npc in world["npcs"]:
        if npc["dialogue"] not in known:
            findings.append(_finding("error", "S006", "%s (%s)" % (npc["name"], npc["level"]), "names the dialogue %r, which dialogue.xml does not have" % npc["dialogue"]))
    spoken = {n["dialogue"] for n in world["npcs"] if n["talkable"]}
    for did in dialogues:
        if did not in spoken:
            findings.append(_finding("info", "S007", did, "no talkable NPC in the levels speaks this dialogue"))

    # --- play it: a search over story STATES (the set of flags set so far), because a conversation says different things in different states
    def reachable_levels(state):
        return {first_level} | {d["target"] for d in world["doors"] if entered_flag(d["target"]) in state}

    start = frozenset()
    seen_states = {start}
    queue = [start]
    capped = False
    while queue:
        state = queue.pop()
        lv = reachable_levels(state)
        successors = set()
        for d in world["doors"]:
            if d["level"] in lv and holds(d["requires"], state):
                successors.add(state | {entered_flag(d["target"])})
        for e in world["events"]:
            if e["level"] in lv and holds(e["requires"], state):
                successors.add(state | {e["flag"]})
        for did in {n["dialogue"] for n in world["npcs"] if n["level"] in lv and n["talkable"]}:
            nodes = dialogues.get(did)
            if not nodes:
                continue
            stack = [(first(nodes, "start", state), state)]
            visited = set()
            while stack:
                node, local = stack.pop()
                if node is None or (node["index"], local) in visited:
                    continue
                visited.add((node["index"], local))
                for c in node["choices"]:
                    if not holds(c["when"], local):
                        continue
                    nxt = frozenset(local | sets_of(c["set"]) | ACTION_FLAGS.get(c["action"], set()))
                    successors.add(nxt)
                    if c["goto"] not in ("", "end"):
                        stack.append((first(nodes, c["goto"], nxt), nxt))
        for s in successors:
            if s not in seen_states:
                if len(seen_states) >= MAX_STATES:
                    capped = True
                    continue
                seen_states.add(s)
                queue.append(s)
    flags = set().union(*seen_states)
    levels = set().union(*(reachable_levels(s) for s in seen_states))
    rounds = len(seen_states)
    if capped:
        findings.append(_finding("warning", "S009", "search", "the search stopped at %d story states: a flag reported unreachable may only be unexplored" % MAX_STATES))

    for g in goals:
        if g not in flags:
            findings.append(_finding("error", "S004", g, "the story never reaches this flag (over %d story states the flags reached are: %s)" % (rounds, ", ".join(sorted(flags)))))
    # a node that can never be picked: its `when` contradicts itself, or an EARLIER node with the same id holds whenever it does (FindNode takes the first match)
    for did, nodes in dialogues.items():
        for j, n in enumerate(nodes):
            tj = {(name, neg, num) for name, neg, num in terms(n["when"])}
            where = "%s/%s#%d" % (did, n["id"], n["index"])
            if any((name, not neg, num) in tj for name, neg, num in tj if not num):
                findings.append(_finding("warning", "S005", where, "its `when` %r contradicts itself: this node is never picked" % n["when"]))
                continue
            for i in range(j):
                m = nodes[i]
                if m["id"] == n["id"] and {(name, neg, num) for name, neg, num in terms(m["when"])} <= tj:
                    findings.append(_finding("warning", "S005", where, "hidden by node #%d with the same id (%r holds whenever this one does): never picked, %r" % (m["index"], m["when"], n["text"][:40])))
                    break

    read_somewhere = set(creads) | set(world["reads"])
    for nodes in dialogues.values():
        for n in nodes:
            for t in [n["when"]] + [c["when"] for c in n["choices"]]:
                read_somewhere |= {name for name, _neg, _num in terms(t)}
    for f in sorted(can_set - read_somewhere):
        if f.startswith("entered_"):
            continue                                   # a doorway marks the level it opened; only some levels' flags are read
        findings.append(_finding("info", "S008", f, "set by the story but read by no dialogue, entity or line of the game's code"))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {"ok": counts["error"] == 0, "findings": findings, "counts": counts, "flags_reached": sorted(flags), "levels_reached": sorted(levels), "rounds": rounds,
            "dialogues": len(dialogues), "nodes": sum(len(n) for n in dialogues.values())}


def main():
    r = check(sys.argv[1:] or None)
    print("%s: %d dialogues, %d nodes; the story reaches %d flags over %d story states and the levels %s" % ("OK" if r["ok"] else "PROBLEMS", r["dialogues"], r["nodes"], len(r["flags_reached"]), r["rounds"], r["levels_reached"]))
    print("flags: " + ", ".join(r["flags_reached"]))
    for f in r["findings"]:
        print("  %-7s %s %s: %s" % (f["severity"], f["code"], f["where"], f["message"]))
    print("%(error)d error(s), %(warning)d warning(s), %(info)d information" % r["counts"])
    return 1 if r["counts"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
