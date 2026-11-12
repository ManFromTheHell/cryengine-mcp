#!/usr/bin/env python3
"""Self-test of uqs_check.py: the code scanner on synthetic input (offline), then, with the editor open, the engine's UQS registry and the game's query through the engine's own editor service, with
deliberately broken copies (positive controls) and the unchanged query as the control. The copies are written outside libs/ai/uqs, checked as registered zz_mcp_ copies and removed by the engine.
About 30 s live.

    test_uqs_check.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import uqs_check as U

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-120s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(r):
    return sorted({f["code"] for f in r["findings"]})


def main():
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "a.cpp"), "w").write('g.SetQueryBlueprint("MyQuery");\nauto& params = g.GetRuntimeParamsStorage();\nparams.AddOrReplace("npcPos", f, &x);\ng.GetRuntimeParamsStorage().AddOrReplace("goal", f, &y);\n')
        q, p = U.code_names(d)
        check("the code scanner reads the query name and both forms of runtime parameter", sorted(q) == ["MyQuery"] and sorted(p) == ["goal", "npcPos"], (sorted(q), sorted(p)))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # The cases below pin the author's game (the BestSpotToStand query and the C++ that sets its parameters); a project without that query skips them.
    has_query = os.path.isfile(os.path.join(U.QUERY_DIR, "BestSpotToStand.uqs"))
    if not has_query:
        print("  --   SKIP the project-specific cases: no BestSpotToStand.uqs in %s" % U.QUERY_DIR)
    q, p = U.code_names() if has_query else (["BestSpotToStand"], ["agentType", "npcPos", "playerPos"])
    check("the game's own code asks for BestSpotToStand and sets npcPos, playerPos, agentType", sorted(q) == ["BestSpotToStand"] and sorted(p) == ["agentType", "npcPos", "playerPos"], (sorted(q), sorted(p)))

    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                           # noqa: BLE001
        live = False
        print("  --   the editor is not open: the live checks are skipped")
    if live and not has_query:
        print("  --   SKIP the live checks: they edit copies of the project's BestSpotToStand query, which this project does not have")
        live = False
    if live:
        c = U.catalog(refresh=True)
        check("the engine's UQS registry: 11 generators, 6 instant and 1 deferred evaluator, 33 functions, 6 score transforms, the 3 query factories", (len(c["generators"]), len(c["instant_evaluators"]), len(c["deferred_evaluators"]), len(c["functions"]), len(c["score_transforms"]), sorted(f["name"] for f in c["query_factories"])) == (11, 6, 1, 33, 6, ["Chained", "Fallbacks", "Regular"]), {k: len(v) for k, v in c.items()})
        gen = [g for g in c["generators"] if g["name"] == "std::PointsOnGridProjectedOntoNavMesh"][0]
        check("a generator lists its inputs with the engine's 4-character ids and types: center CENT, size SIZE (float)", {(i["name"], i["id"]) for i in gen["inputs"]} >= {("center", "CENT"), ("size", "SIZE"), ("spacing", "SPAC")} and [i["type"] for i in gen["inputs"] if i["name"] == "size"] == ["float"], gen["inputs"][:2])
        check("search finds the navmesh generators and the navmesh test by a word of their name", {"std::PointsOnNavMesh", "std::TestLocationInNavMesh"} <= {r["name"] for r in U.search("navmesh")}, [r["name"] for r in U.search("navmesh")][:4])
        check("the library holds the project's one query, BestSpotToStand", [x["name"] for x in c["queries"]] == ["BestSpotToStand"], c["queries"])

        good = U.check()
        check("control: the project's own query loads, validates and agrees with the C++ (3 runtime parameters set, 3 declared, no finding)", good["ok"] and good["loaded"] and good["valid"] and not good["findings"] and len(good["query"]["runtime_params"]) == 3, good["findings"][:2])

        base = open(os.path.join(U.QUERY_DIR, "BestSpotToStand.uqs"), encoding="utf-8").read()
        scratch = os.path.join(U.SCRATCH, "cases")
        os.makedirs(scratch, exist_ok=True)

        def variant(name, text, same=False, **kw):
            assert same or text != base, name
            p = os.path.join(scratch, "t_%s.uqs" % name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return U.check(p, cross=False, **kw)
        try:
            r = variant("copy", base, same=True)
            check("control: an unchanged copy of the query, registered as a temporary record, loads and validates like the original", r["ok"] and r["loaded"] and r["valid"], codes(r))
            r = variant("nogen", base.replace("std::PointsOnGridProjectedOntoNavMesh", "std::NoSuchGenerator"))
            check("positive control: an unknown generator is rejected by the engine (names the generator) and by the registry", not r["valid"] and any("Unknown GeneratorFactory" in f["message"] and "NoSuchGenerator" in f["message"] for f in r["findings"]) and "U010" in codes(r), codes(r))
            r = variant("noeval", base.replace('"std::TestMinDistance"', '"std::NoSuchEvaluator"'))
            check("positive control: an unknown evaluator is rejected", not r["valid"] and "U011" in codes(r) and any("Unknown InstantEvaluatorFactory" in f["message"] for f in r["findings"]), codes(r))
            r = variant("nofunc", base.replace('_builtin_Func_Literal_std::float" returnValue="40"', '_builtin_Func_NoSuch" returnValue="40"', 1))
            check("positive control: an unknown function is rejected, with the generator input it sits in", not r["valid"] and any("Unknown function" in f["message"] and "Input/size" in f["message"] for f in r["findings"]) and "U013" in codes(r), codes(r))
            r = variant("badtype", base.replace('type="std::Pos3"                  addToDebugRenderWorld="1"/>', 'type="std::NoSuchType"               addToDebugRenderWorld="1"/>', 1))
            check("positive control: a runtime parameter of an unknown item type is rejected, naming the parameter", not r["valid"] and any("Unknown item type" in f["message"] and "RuntimeParam[npcPos]" in f["message"] for f in r["findings"]), codes(r))
            r = variant("badtransform", base.replace('scoreTransform="Linear"', 'scoreTransform="NoSuchTransform"', 1))
            check("positive control: an unknown score transform is rejected", not r["valid"] and "U012" in codes(r), codes(r))
            r = variant("missinginput", base.replace('  <Input name="spacing" paramID="SPAC">\n   <Function name="_builtin_Func_Literal_std::float" returnValue="2"/>\n  </Input>\n', ""))
            check("positive control: a generator input the query leaves out is rejected by the engine ('expected 5, got 4') and named by the registry (spacing)", not r["valid"] and any("expected 5, got 4" in f["message"] for f in r["findings"]) and any(f["code"] == "U014" and "'spacing'" in f["message"] for f in r["findings"]), codes(r))
            r = variant("badliteral", base.replace('returnValue="40"/>', 'returnValue="forty"/>', 1))
            check("positive control: a float literal 'forty' PASSES the engine's validation (measured) and is caught by the literal check (U015)", r["valid"] and "U015" in codes(r) and not r["ok"], codes(r))
            r = variant("renamed", base.replace('<Input name="size" paramID="SIZE">', '<Input name="sizee" paramID="SIZE">', 1))
            check("control: an input renamed in the file but with the right paramID is accepted by the engine and is NOT reported as missing (the engine matches by id)", r["valid"] and "U014" not in codes(r), codes(r))
            r = variant("notxml", base.replace("<Query ", "<Query <<", 1))
            check("positive control: a file that is not well-formed XML cannot be loaded (the engine's message)", not r["loaded"] and "U001" in codes(r) and "invalid xml" in r["findings"][0]["message"], codes(r))
            r = variant("wrongroot", base.replace("<Query ", "<Qry ", 1).replace("</Query>", "</Qry>"))
            check("positive control: a root that is not <Query> is refused with the engine's message", not r["loaded"] and "must be <Query>" in r["findings"][0]["message"], codes(r))
            # the code against the query: a fake source tree that sets one parameter the query lacks and leaves two out
            fake = tempfile.mkdtemp()
            try:
                open(os.path.join(fake, "a.cpp"), "w").write('g.SetQueryBlueprint("zz_mcp_cross");\nauto& params = g.GetRuntimeParamsStorage();\nparams.AddOrReplace("npcPos", f, &x);\nparams.AddOrReplace("ghostParam", f, &y);\n')
                p = os.path.join(scratch, "zz_mcp_cross.uqs")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(base)
                rc = U.check(p, code_dir=fake)
                check("the code against the query: a parameter the code sets and the query lacks (U020) and two the query declares and the code leaves out (U021) are reported", {"U020", "U021"} <= set(codes(rc)) and sum(1 for f in rc["findings"] if f["code"] == "U021") == 2, codes(rc))
            finally:
                shutil.rmtree(fake, ignore_errors=True)
        finally:
            shutil.rmtree(os.path.join(U.SCRATCH, "cases"), ignore_errors=True)
        left = [f for f in os.listdir(U.QUERY_DIR) if f.startswith("zz_mcp_")]
        check("the project is as it was: no zz_mcp_ copy is left in libs/ai/uqs, and the original is still the only query", not left and os.listdir(U.QUERY_DIR) == ["BestSpotToStand.uqs"], os.listdir(U.QUERY_DIR))
        again = U.check()
        check("the original query still validates afterwards (the temporary records did not disturb the library)", again["ok"] and again["valid"], codes(again))

    fails = results.count(False)
    print("\n%d checks, %d failed" % (len(results), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
