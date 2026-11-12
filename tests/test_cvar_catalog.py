#!/usr/bin/env python3
"""Self-test of cvar_catalog.py. Editor-free: it parses fixtures, and checks the catalog already built in engine_data/ against facts the
engine itself states. It takes about 3 s.

    test_cvar_catalog.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import cpp_conditions as P
import cvar_catalog as C

results = []
NL = chr(10)


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-76s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:70]))


def one(text, name):
    recs = [r for r in C.scan_text(text, "x.cpp") if r["name"] == name]
    return recs[0] if recs else None


def main():
    # ---- the parser, on fixtures that stand for each registration form seen in the engine --------------------------------------
    r = one('REGISTER_CVAR(e_foo, 1, VF_CHEAT | VF_DUMPTODISK, "first part "' + NL + '  "second part");', "e_foo")
    check("REGISTER_CVAR: name from the identifier, default, flags, help joined across literals",
          r and r["default"] == "1" and r["vf"] == ["CHEAT", "DUMPTODISK"] and r["help"] == "first part second part", r and (r["default"], r["vf"], r["help"]))
    r = one('REGISTER_CVAR2("r_bar", &m_bar, 0.5f, VF_NULL, "usage (a, b): see x");', "r_bar")
    check("REGISTER_CVAR2: name from the string; commas and parentheses inside the help do not split arguments", r and r["default"] == "0.5f" and r["help"] == "usage (a, b): see x", r and r["help"])
    r = one('REGISTER_INT_CB("s_baz", 3, 0, "h", OnChange);', "s_baz")
    check("REGISTER_INT_CB is understood (a callback argument after the help)", r and r["default"] == "3" and r["help"] == "h", r)
    r = one('REGISTER_CVAR_DEV_ONLY(g_dbg, 0, VF_NULL, "dev");', "g_dbg")
    check("a *_DEV_ONLY macro is marked dev-only (it registers nothing in a Release build)", r and r["dev_only"] and not r["dedi_only"], r and r["macro"])
    r = one('REGISTER_CVAR2_CB_DEDI_ONLY("net_x", &v, 1, 0, "h", Cb);', "net_x")
    check("a *_CB_DEDI_ONLY macro is understood and marked dedicated-server-only", r and r["dedi_only"] and r["default"] == "1", r and r["macro"])
    check("a commented-out registration is not found", one("// REGISTER_CVAR(e_gone, 0, 0, \"\");", "e_gone") is None, "")
    check("the macro's own #define is not taken for a registration", one("#define REGISTER_FOO(x) REGISTER_CVAR(e_def, 0, 0, \"\")", "e_def") is None, "")
    r = one("REGISTER_CVAR(e_multi," + NL + "   (a ? 1 : 2)," + NL + "   VF_NULL," + NL + '   "m");', "e_multi")
    check("a call over several lines with a nested expression is read whole", r and r["default"] == "(a ? 1 : 2)" and r["line"] == 1, r and r["default"])
    r = one('gEnv->pConsole->RegisterInt("ai_x", 1, VF_NULL, "h");', "ai_x")
    check("a console method call (pConsole->RegisterInt) is understood", r and r["kind"] == "cvar" and r["default"] == "1", r)
    r = one('REGISTER_COMMAND("ash_go", Cmd, VF_CHEAT, "go there");', "ash_go")
    check("REGISTER_COMMAND is a command, with its flags", r and r["kind"] == "command" and r["vf"] == ["CHEAT"], r and r["kind"])
    r = one('REGISTER_CVAR_AUTO(int, e_svoTI_Active, 0, VF_NULL, "Activates voxel GI");', "e_svoTI_Active")
    check("REGISTER_CVAR_AUTO (the SVOGI form) is understood", r and r["default"] == "0" and r["help"] == "Activates voxel GI", r)
    r = one('attachVariable("sys_PakPriority", &g.n, "tell CryPak", VF_READONLY | VF_CHEAT);', "sys_PakPriority")
    check("attachVariable (the sys_Pak* form) is understood", r and r["vf"] == ["CHEAT", "READONLY"], r and r["vf"])
    src = NL.join(["#if !defined(_RELEASE)", 'REGISTER_CVAR(e_a, 0, 0, "");', "#else", 'REGISTER_CVAR(e_b, 0, 0, "");', "#endif",
                   "#ifndef _RELEASE", 'REGISTER_CVAR(e_c, 0, 0, "");', "#endif", "#ifdef _RELEASE", 'REGISTER_CVAR(e_d, 0, 0, "");', "#endif",
                   "#if USE_THING", 'REGISTER_CVAR(e_e, 0, 0, "");', "#endif"])
    st = {n: (one(src, "e_" + n) or {}).get("release_cond", "NOT FOUND") for n in "abcde"}
    check("preprocessor: !defined(_RELEASE) -> absent, its #else -> release_only, #ifndef -> absent, #ifdef _RELEASE -> release_only, unknown macro -> undecided",
          [st[k] for k in "abcde"] == ["absent", "release_only", "absent", "release_only", None], st)

    # ---- the preprocessor evaluator (cpp_conditions) -------------------------------------------------------------------------
    d = {"A": "1", "B": "0", "V": "5"}
    ev = lambda e, s=False: P.evaluate(e, d, strict=s)
    check("evaluate: defined/!/&&/||/comparisons on known macros", ev("defined(A) && !defined(Z_UNKNOWN_BUT_STRICT)", True) is True and ev("A && B") is False and ev("V >= 5 && V < 6") is True and ev("!B || 0") is True, "")
    check("evaluate: an unknown macro makes the answer None, unless the other side already decides it (True || ? is True, False && ? is False)",
          ev("UNKNOWN_X") is None and ev("A || UNKNOWN_X") is True and ev("B && UNKNOWN_X") is False and ev("B || UNKNOWN_X") is None and ev("defined(UNKNOWN_X)") is None, "")
    mini = NL.join(["#if defined(_RELEASE) && !defined(RELEASE)", "#define RELEASE", "#endif", "#if (!defined(_RELEASE) || defined(PERFORMANCE_BUILD))", "#define ENABLE_PROFILING_CODE", "#endif",
                    "#undef ENABLE_STATOSCOPE", "#if defined(ENABLE_PROFILING_CODE)", "#define ENABLE_STATOSCOPE 1", "#endif",
                    "#if CRY_PLATFORM_ORBIS", "#define ORBIS_ONLY", "#elif CRY_PLATFORM_WINDOWS", "#define WIN_ONLY 1", "#else", "#define NEITHER", "#endif"])
    r_, p_ = P.defines_from(mini, True), P.defines_from(mini, False)
    check("defines_from runs a header for Release and for Profile (macros it #defines under !_RELEASE exist only in Profile; #elif chains take one branch)",
          "RELEASE" in r_ and "RELEASE" not in p_ and "ENABLE_STATOSCOPE" not in r_ and p_.get("ENABLE_STATOSCOPE") == "1" and "WIN_ONLY" in r_ and "WIN_ONLY" in p_ and "ORBIS_ONLY" not in r_ and "NEITHER" not in r_,
          (sorted(r_.known), "WIN_ONLY" in r_))
    check("a macro the header #defines but leaves undefined is definitely off; one it only tests (a CMake option) stays unknown",
          P.evaluate("ENABLE_STATOSCOPE", r_) is False and P.evaluate("ENABLE_STATOSCOPE", p_) is True and P.evaluate("USE_BROFILER", p_) is None, "")
    terms = P.condition_terms(["#if A", "x", "#elif B", "y", "#else", "z", "#endif", "w"])
    check("condition_terms: after #elif the earlier branch is negated, after #else both are; outside the block, none",
          terms[1] == [("A", True)] and terms[3] == [("A", False), ("B", True)] and terms[5] == [("A", False), ("B", False)] and terms[7] == [], terms[3])
    _pd = os.path.join(C.SOURCE, "Code", "CryEngine", "CryCommon", "CryCore", "Project", "ProjectDefines.h")
    if os.path.isfile(_pd):
        real = open(_pd, encoding="utf-8", errors="replace").read()
        rr, rp = P.defines_from(real, True), P.defines_from(real, False)
        check("the engine's own ProjectDefines.h: profiling code, Statoscope and unit testing exist in Profile only; the CVar whitelist in Release only",
              all(P.evaluate(m, rr) is False and P.evaluate(m, rp) is True for m in ("ENABLE_PROFILING_CODE", "ENABLE_STATOSCOPE", "CRY_TESTING")) and P.evaluate("CVARS_WHITELIST", rr) is True and P.evaluate("CVARS_WHITELIST", rp) is False,
              [m for m in ("ENABLE_PROFILING_CODE", "ENABLE_STATOSCOPE", "CRY_TESTING", "CVARS_WHITELIST")])
    else:
        print("SKIPPED: the engine source tree is not at %s (set CRYENGINE_SOURCE): the ProjectDefines.h check is not run" % C.SOURCE)
    rs = lambda text, n: one(text, n)["release_cond"]
    if os.path.isfile(_pd):        # the macro table comes from the engine's own ProjectDefines.h: without a source checkout there is nothing to decide with
        check("release_state: #if ENABLE_STATOSCOPE -> absent in Release; unconditional -> present; an undecidable macro -> None",
              rs('#if ENABLE_STATOSCOPE' + NL + 'REGISTER_CVAR(e_p, 0, 0, "");' + NL + '#endif', "e_p") == "absent" and rs('REGISTER_CVAR(e_q, 0, 0, "");', "e_q") == "present"
              and rs('#if USE_BROFILER' + NL + 'REGISTER_CVAR(e_r, 0, 0, "");' + NL + '#endif', "e_r") is None, "")

    # ---- the engine's dumps ------------------------------------------------------------------------------------------------
    dump = NL.join(["Command: ash_x ", "script: ", "help: does x", "second line", "", "variable: r_foo DUMPTODISK, READONLY", "type: int", "current: 2",
                    "help: Usage: r_foo [n]", "0: off", "1: on", "", "variable: e_bar ", "type: float", "current: 0.5", "help: b", ""])
    v, c = C.parse_dump(dump)
    check("parse_dump: a variable's flags, type, current value and multi-line help", v["r_foo"]["flags"] == ["DUMPTODISK", "READONLY"] and v["r_foo"]["type"] == "int"
          and v["r_foo"]["current"] == "2" and v["r_foo"]["help"] == "Usage: r_foo [n]" + NL + "0: off" + NL + "1: on" and v["e_bar"]["flags"] == [], v["r_foo"])
    check("parse_dump: a command with a multi-line help, not mixed with the next record", c["ash_x"]["help"] == "does x" + NL + "second line" and set(v) == {"r_foo", "e_bar"}, c["ash_x"]["help"])
    audit = "[CVARS]: [BEGIN AUDIT]" + NL + "[CVARS]: [VARIABLE] e_a [VF_CHEAT] [VF_CONST_CVAR]" + NL + "[CVARS]: [VARIABLE] e_a [VF_CHEAT] [VF_CONST_CVAR]" + NL + \
            "[CVARS]: [VARIABLE] e_b" + NL + "[CVARS]: [END AUDIT] (commands 0/9; variables 2/2)"
    flags, total = C.parse_audit(audit)
    check("parse_audit: flags per variable; the duplicate (console + log) lines are absorbed; the totals are read", flags == {"e_a": {"CHEAT", "CONST_CVAR"}, "e_b": set()} and total == (0, 9, 2, 2), (flags, total))
    g = C.parse_group(NL.join(["[default]", "; c", "= 3", "e_x=128", "e_y=1 -- note", "[1]", "e_x=32", "; xbox", "[5]", "e_y=9"]))
    check("parse_group: default level, [default] values, per-level overrides, a '-- comment' is not part of the value",
          g["default_level"] == 3 and g["sections"]["default"] == {"e_x": "128", "e_y": "1"} and g["sections"]["1"] == {"e_x": "32"}, g)

    # ---- the rules (CXConsole::OnBeforeVarChange) ----------------------------------------------------------------------------
    s = C.settable({"CHEAT", "CONST_CVAR"})
    check("cheat+const: settable in the editor and a Profile build (dev mode), NOT in Release", (s["editor"], s["profile"], s["release"]) == ("yes", "yes", "no"), s)
    s = C.settable({"DUMPTODISK"})
    check("a plain variable: settable everywhere", (s["editor"], s["profile"], s["release"]) == ("yes", "yes", "yes"), s)
    s = C.settable({"DEPRECATED"})
    check("deprecated: not settable anywhere (dev mode does not help)", (s["editor"], s["profile"], s["release"]) == ("no", "no", "no"), s)
    s = C.settable({"DEV_ONLY"})
    check("dev-only: absent from a Release build", s["release"] == "absent", s)
    s = C.settable({"READONLY"})
    check("read-only: refused in Release like cheat", s["release"] == "no" and "readonly" in s["why"], s)

    # ---- the built catalog against facts the engine states -----------------------------------------------------------------
    if not os.path.isfile(C.CATALOG):
        print("SKIPPED: %s is not generated (python catalogs/cvar_catalog.py --capture --build, editor open): the catalog checks are not run" % C.CATALOG)
        bad = results.count(False)
        print("cvar catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
        return 1 if bad else 0
    cat = C.load()
    E = cat["entries"]
    st = cat["stats"]
    names = [e["name"].lower() for e in E if e["kind"] == "cvar"]
    check("one entry per variable (no duplicates): %d variables, %d commands" % (st["variables"], st["commands"]), len(names) == len(set(names)) == st["variables"], len(names))
    audit_n = len([l for l in open(os.path.join(C.DATA, "cvar_flags.txt"), encoding="utf-8") if l.strip()])
    check("every variable the engine's audit listed is in the catalog (audit %d, catalog %d)" % (audit_n, st["variables"]), audit_n == st["variables"], "")
    rec = st["variables_registration_found"] / st["variables"]
    check("the source scan finds the registration of %.1f%% of the editor's variables (>= 95%%)" % (100 * rec), rec >= 0.95, "%d of %d" % (st["variables_registration_found"], st["variables"]))
    check("every command has its registration in the source", st["commands_registration_found"] == st["commands"], st["commands_nothing_found"])
    unexplained = [e["name"] for e in E if e["kind"] == "cvar" and not e["source"]]
    check("variables with no source line are only the %d runtime-built q_Shader*/sys_spec_quality names: %s" % (len(unexplained), unexplained[:3]),
          all(n.startswith(("q_Shader", "sys_spec_")) for n in unexplained), unexplained)
    num = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)f?$")
    same = diff = 0
    for e in E:
        t = (e["default"] or "").strip().strip("()").strip()
        if e["kind"] == "cvar" and num.match(t):
            try:
                a, b = float(t.rstrip("f")), float(e["current"])
            except ValueError:
                continue
            same, diff = (same + 1, diff) if abs(a - b) <= 1e-3 * max(1, abs(a)) else (same, diff + 1)
    check("control: the default read from source equals the editor's current value for %.1f%% of numeric variables (>= 90%%; the rest are spec/config changes)" % (100 * same / (same + diff)),
          same / (same + diff) >= 0.90, "%d same, %d differ" % (same, diff))
    byname = {e["name"].lower(): e for e in E}
    e = byname["e_defaultmaterial"]
    check("e_DefaultMaterial (the one that did nothing in Release): cheat+const, settable in the editor/Profile, refused in Release",
          {"CHEAT", "CONST_CVAR"} <= set(e["flags"]) and e["settable"]["release"] == "no" and e["settable"]["profile"] == "yes", (e["flags"], e["settable"]))
    e = byname["r_antialiasingmode"]
    check("r_AntialiasingMode: DUMPTODISK only, settable in Release, default read from the source (the engine default is 2)", "DUMPTODISK" in e["flags"] and e["settable"]["release"] == "yes" and e["current"] != "" and e["default"] is not None, (e["flags"], e["default"], e["current"]))
    e = byname["e_svoti_active"]
    check("a variable registered by the SVOGI form (REGISTER_CVAR_AUTO) has its source line", e["source_kind"] == "registration" and "SceneTreeCVars" in e["source"], e["source"])
    e = byname["e_particlesquality"]
    check("a spec group's values are attached to the variable it sets (e_ParticlesQuality <- sys_spec_Particles)", "sys_spec_Particles" in e["spec_groups"] and e["spec_groups"]["sys_spec_Particles"]["1"] == "2", e["spec_groups"])
    cheat = sum(1 for x in E if x["kind"] == "cvar" and "CHEAT" in x["flags"])
    audit_cheat = sum(1 for l in open(os.path.join(C.DATA, "cvar_flags.txt"), encoding="utf-8") if " CHEAT" in l)
    check("the cheat count equals the audit's (%d)" % audit_cheat, cheat == audit_cheat, cheat)
    rows, total = C.search("antialiasing", kind="cvar", limit=5)
    check("search finds a variable by a word in its name or help, name matches first", total >= 1 and rows[0]["name"].lower().startswith("r_antialiasing"), [x["name"] for x in rows[:3]])
    rows, total = C.search("", settable_in="release", flag="CHEAT", limit=5)
    check("control: nothing flagged cheat is listed as settable in Release", total == 0, total)

    # ---- the catalog against what the PACKAGED RELEASE GAME did (engine_data/release_probe.json: the cvar_probe job's record) --------------------------
    _probe = os.path.join(C.DATA, "release_probe.json")
    if os.path.isfile(_probe):
        rec = json.load(open(os.path.join(C.DATA, "release_probe.json"), encoding="utf-8"))
        cmp = C.compare_probe(rec["rows"])
        check("the catalog's Release prediction matches the %d variables the real Release package was asked to change (%s)" % (len(rec["rows"]), "; ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in sorted(cmp["by_class"].items()))),
              cmp["total"] >= 50 and cmp["agree"] == cmp["total"] and not cmp["disagreements"], cmp["disagreements"][:2])
        check("that sample holds every class (accepted, refused, absent) and the evaluator's own class (absent without a _DEV_ONLY flag)",
              set(cmp["by_class"]) == {"accepted", "refused", "absent"} and any(r["state"] == "absent" and "DEV_ONLY" not in (C.get(r["name"]) or {"flags": []})["flags"] for r in rec["rows"]), sorted(cmp["by_class"]))
    else:
        print("SKIPPED: %s is not present (the cvar_probe job record of a packaged Release run)" % _probe)
    check("the evaluator never claimed the Profile editor would not have a variable it has (contradictions: %d)" % len(st["evaluator_contradictions"]), st["evaluator_contradictions"] == [], st["evaluator_contradictions"][:3])
    e = byname["e_statoscopefilenameusedatagroups"]
    check("the miss the first probe found (e_StatoscopeFilenameUseDatagroups, absent in Release with no dev-only flag) is now predicted, with the reason",
          e["settable"]["release"] == "absent" and "ENABLE_STATOSCOPE" in e["settable"]["why"], e["settable"]["why"])

    bad = results.count(False)
    print("cvar catalog test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
