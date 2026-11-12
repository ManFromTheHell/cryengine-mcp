#!/usr/bin/env python3
"""The Universal Query System as the ENGINE has it registered, and query files checked by the engine's own validator (ENGINE_LEARNING_PLAN D3), without running the game.

Two plugin verbs use the UQS hub (UQS::Core::IHubPlugin::GetHub) of the open editor:

  bridge.uqs.catalog   every item type, function, generator, instant/deferred evaluator, score transform and query factory the engine has registered, with description and, for functions,
                       generators and evaluators, the input parameters (name, type, description) from the engine's own input parameter registry.
  bridge.uqs.check     a query file through the hub's EDITOR SERVICE (what the Query Editor uses): LoadTextualQueryBlueprint reads libs/ai/uqs/<name>.uqs with the XML data source,
                       ValidateTextualQueryBlueprint resolves it against the registry and reports each problem on the element it belongs to. Nothing is stored in the library and nothing runs.

    python checks/uqs_check.py catalog [text]
    python checks/uqs_check.py check [query name]      (default BestSpotToStand)

The game's C++ is read too: the query name it asks for (SetQueryBlueprint) and the runtime parameters it sets (GetRuntimeParamsStorage().AddOrReplace) against the file's <RuntimeParam> entries.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
CODE = os.path.join(PROJECT, "Code")
DATA = os.path.join(HERE, "engine_data")
SCRATCH = os.path.join(DATA, "uqs_scratch")
CATALOG_FILE = os.path.join(DATA, "uqs_catalog.json")
QUERY_DIR = os.path.join(ASSETS, "libs", "ai", "uqs")
DEFAULT_QUERY = "BestSpotToStand"

sys.path.insert(0, HERE)

KINDS = ("items", "functions", "generators", "instant_evaluators", "deferred_evaluators", "score_transforms", "query_factories")
SCORE_TRANSFORMS = ["Linear", "LinearInverse", "Sine180", "Sine180Inverse", "Square", "SquareInverse"]       # EScoreTransformType order (IScoreTransformFactory.h)
LEAF_KINDS = ["None", "GlobalParam", "IteratedItem", "Literal", "ShuttledItems"]                              # ELeafFunctionKind order (IFunctionFactory.h)


class UqsError(RuntimeError):
    pass


def _slash(p):
    return p.replace(chr(92), "/")


def catalog(refresh=False):
    """{items, functions, generators, instant_evaluators, deferred_evaluators, score_transforms, query_factories, queries}: what the UQS hub has registered (cached in engine_data/uqs_catalog.json)."""
    if not refresh and os.path.isfile(CATALOG_FILE):
        with open(CATALOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "catalog_raw.json")
    r = bridge_py.send("bridge.uqs.catalog " + _slash(out), 60)
    if not r.get("ok"):
        raise UqsError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    for t in data["score_transforms"]:
        t["kind"] = SCORE_TRANSFORMS[t["type"]] if 0 <= t["type"] < len(SCORE_TRANSFORMS) else str(t["type"])
    for fn in data["functions"]:
        fn["leaf"] = LEAF_KINDS[fn["leaf_kind"]] if 0 <= fn["leaf_kind"] < len(LEAF_KINDS) else str(fn["leaf_kind"])
    os.makedirs(DATA, exist_ok=True)
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def search(text="", kind="", refresh=False):
    """[{kind, name, description, ...}] whose name, description, type or an input's name contains `text` (case-insensitive); kind limits the list to one of KINDS."""
    t = (text or "").lower()
    data = catalog(refresh)
    out = []
    for k in KINDS:
        if kind and kind != k:
            continue
        for row in data[k]:
            hay = " ".join(str(row.get(k, "")) for k in ("name", "description", "type", "kind", "returns", "generates", "leaf")) + " " + " ".join(i["name"] + " " + i["type"] for i in row.get("inputs", []))
            hay = hay.lower()
            if t in hay:
                out.append(dict(row, kind=k))
    return out


# ---------------------------------------------------------------- the code's side

QUERY_RES = [re.compile(r'SetQueryBlueprint\(\s*"([^"]+)"'), re.compile(r'FindQueryBlueprintIDByName\(\s*"([^"]+)"')]
PARAM_RES = [re.compile(r'RuntimeParamsStorage\(\)\s*\.\s*AddOrReplace\(\s*"([^"]+)"'), re.compile(r'\bparams\s*\.\s*AddOrReplace\(\s*"([^"]+)"')]


def code_names(code_dir=None):
    """({query name: [file:line]}, {runtime parameter: [file:line]}) from the game's C++ string literals."""
    code_dir = code_dir or CODE
    queries, params = {}, {}
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
                for rx in QUERY_RES:
                    for m in rx.finditer(line):
                        queries.setdefault(m.group(1), []).append("%s:%d" % (rel, n))
                for rx in PARAM_RES:
                    for m in rx.finditer(line):
                        params.setdefault(m.group(1), []).append("%s:%d" % (rel, n))
    return queries, params


def _finding(sev, code, where, message, fix=""):
    return {"severity": sev, "code": code, "where": where, "message": message, "fix": fix}


def _ask(spec):
    import bridge_py
    os.makedirs(SCRATCH, exist_ok=True)
    out = os.path.join(SCRATCH, "check_%d.json" % os.getpid())
    if os.path.exists(out):
        os.remove(out)
    r = bridge_py.send("bridge.uqs.check %s | %s" % (_slash(out), spec), 90)
    if not r.get("ok"):
        raise UqsError("the editor refused: %s (deploy the bridge plugin: bridge_devloop.py deploy)" % r.get("error"))
    with open(out, encoding="utf-8") as f:
        eng = json.load(f)
    os.remove(out)
    return eng


def check(query=DEFAULT_QUERY, code_dir=None, cross=True):
    """query: a query's name (the stem of libs/ai/uqs/<name>.uqs) or the path of any .uqs file. -> {ok, errors, warnings, findings, loaded, valid, query: {...}}: the engine's own load + validation plus the
    comparison with the game's code. The XML data source enumerates libs/ai/uqs ONCE at startup, so a file the engine does not know yet (or one outside that folder) is checked as a temporary
    zz_mcp_ copy registered for the call; the engine removes the copy again (removing a record deletes its file)."""
    stamp = "%d_%d" % (os.getpid(), int(__import__("time").time()))
    src = None
    if os.path.isfile(query):
        src = os.path.abspath(query)
        name = os.path.splitext(os.path.basename(src))[0]
    else:
        name = query
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise UqsError("a query name is the file's stem (letters, digits, _ . -)")
    path = src or os.path.join(QUERY_DIR, name + ".uqs")
    in_folder = os.path.normcase(os.path.dirname(path)) == os.path.normcase(QUERY_DIR)
    eng = None
    if in_folder and not name.startswith("zz_mcp_"):
        eng = _ask(name)
        if eng["loaded"] or "not among the enumerated records" not in eng["load_error"]:
            src = None
        else:
            eng = None
    if eng is None:
        if not os.path.isfile(path):
            raise UqsError("no query file %s" % path)
        copy_name = name if name.startswith("zz_mcp_") else "zz_mcp_%s_%s" % (stamp, name)
        copy_path = os.path.join(QUERY_DIR, copy_name + ".uqs")
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(copy_path):
            shutil.copyfile(path, copy_path)
        try:
            eng = _ask(copy_name + " | register")
        finally:
            if os.path.exists(copy_path):
                os.remove(copy_path)
    findings = []
    if not eng["loaded"]:
        findings.append(_finding("error", "U001", name, "the engine could not load the query: %s" % (eng["load_error"] or "(no message)"),
                                 "check libs/ai/uqs/%s.uqs exists and is well-formed (a literal value is written BARE, not as JSON)" % name))
    elif not eng["valid"]:
        findings.append(_finding("error", "U002", name, "the engine's validation rejects this query: the game would refuse to run it"))
    for e in eng.get("errors", []):
        findings.append(_finding("error", "U003", name, e))
    cat = None
    q = eng.get("query") or {}
    if eng["loaded"]:
        try:
            cat = catalog()
        except (UqsError, OSError):
            cat = None
        if cat is not None:
            gens = {g["name"] for g in cat["generators"]}
            evs = {e["name"] for e in cat["instant_evaluators"]} | {e["name"] for e in cat["deferred_evaluators"]}
            fns = {f["name"] for f in cat["functions"]}
            sts = {s["name"] for s in cat["score_transforms"]}
            if q.get("generator") and q["generator"] not in gens:
                findings.append(_finding("error", "U010", name, "generator '%s' is not registered with the engine" % q["generator"]))
            for ev in q.get("instant_evaluators", []):
                if ev["name"] not in evs:
                    findings.append(_finding("error", "U011", name, "evaluator '%s' is not registered with the engine" % ev["name"]))
                if ev.get("score_transform") and ev["score_transform"] not in sts:
                    findings.append(_finding("warning", "U012", name, "score transform '%s' is not registered (evaluator %s)" % (ev["score_transform"], ev["name"])))

            def walk(node, where):
                if node is None:
                    return
                if node["func"] and node["func"] not in fns:
                    findings.append(_finding("error", "U013", name, "%s uses function '%s', which is not registered" % (where, node["func"])))
                for c in node["children"]:
                    walk(c, where + "/" + c["param"])
            def literals(node, where):
                """A literal is parsed when the query STARTS, not when it is validated: 'forty' for a float passes the engine's validation (measured)."""
                if node is None:
                    return
                m = re.match(r"_builtin_Func_Literal_std::(\w+)$", node["func"] or "")
                if m and node["literal"] is not None:
                    t, v = m.group(1), node["literal"].strip()
                    ok = True
                    if t == "float":
                        ok = bool(re.match(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$", v))
                    elif t == "int":
                        ok = bool(re.match(r"^[-+]?\d+$", v))
                    elif t == "bool":
                        ok = v.lower() in ("true", "false", "0", "1")
                    if not ok:
                        findings.append(_finding("error", "U015", name, "%s: the literal %r is not a %s: the engine's validation accepts it and the query fails when it starts" % (where, node["literal"], t),
                                                 "write the value bare (40, 2.5, true), not as text or JSON"))
                for c in node["children"]:
                    literals(c, where + "/" + c["param"])
            literals(q.get("generator_input"), "Generator")
            for ev in q.get("instant_evaluators", []):
                literals(ev["input"], ev["name"])
            walk(q.get("generator_input"), "Generator")
            for ev in q.get("instant_evaluators", []):
                walk(ev["input"], ev["name"])
            # registered inputs the query leaves out: the engine matches an <Input> by its 4-character paramID when it has one, by name otherwise, so a renamed input with the right id still works
            def inputs_of(kind_rows, key, node):
                row = kind_rows.get(key)
                if not row or not node:
                    return
                given_names = {c["param"] for c in node["children"]}
                for inp in row["inputs"]:
                    if inp["name"] not in given_names and not any(c.get("param_id") == inp.get("id") for c in node["children"]):
                        findings.append(_finding("warning", "U014", name, "%s takes input '%s' (%s) and the query gives none" % (key, inp["name"], inp["type"])))
            inputs_of({g["name"]: g for g in cat["generators"]}, q.get("generator"), q.get("generator_input"))
            ev_rows = {e["name"]: e for e in cat["instant_evaluators"] + cat["deferred_evaluators"]}
            for ev in q.get("instant_evaluators", []):
                inputs_of(ev_rows, ev["name"], ev["input"])
    if cross and eng["loaded"]:
        queries, params = code_names(code_dir)
        declared = {p["name"] for p in q.get("runtime_params", [])}
        uses = [where for qn, where in queries.items() if qn == name]
        if queries and name in queries:
            for p, where in sorted(params.items()):
                if p not in declared:
                    findings.append(_finding("warning", "U020", where[0], "the code sets runtime parameter '%s' for %s, which the query does not declare (<RuntimeParam>): it is ignored" % (p, name)))
            for p in sorted(declared):
                if p not in params:
                    findings.append(_finding("warning", "U021", name, "the query declares runtime parameter '%s' and no C++ sets it: the query fails to start" % p))
        elif queries:
            findings.append(_finding("info", "U022", name, "no C++ asks for this query (the code asks for: %s)" % ", ".join(sorted(queries))))
        for qn, where in queries.items():
            if not os.path.isfile(os.path.join(QUERY_DIR, qn + ".uqs")):
                findings.append(_finding("error", "U023", where[0], "the code asks for query '%s' and libs/ai/uqs has no %s.uqs: the request does nothing" % (qn, qn)))
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "findings": findings, "loaded": eng["loaded"], "valid": eng["valid"], "file": _slash(path), "query": q}


def main():
    a = sys.argv[1:]
    if not a or a[0] == "catalog":
        rows = search(a[1] if len(a) > 1 else "")
        print("%d matches" % len(rows))
        for r in rows[:80]:
            print("%-20s %-46s %s" % (r["kind"], r["name"], ", ".join("%s:%s" % (i["name"], i["type"]) for i in r.get("inputs", []))[:110]))
    elif a[0] == "check":
        r = check(a[1] if len(a) > 1 else DEFAULT_QUERY)
        print("%s: %d error(s), %d warning(s); loaded %s, valid %s" % ("OK" if r["ok"] else "PROBLEMS", r["errors"], r["warnings"], r["loaded"], r["valid"]))
        for f in r["findings"]:
            print("  %-7s %s  %s\n          %s" % (f["severity"], f["code"], f["where"], f["message"]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
