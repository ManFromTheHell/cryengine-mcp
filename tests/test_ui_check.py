#!/usr/bin/env python3
"""Self-test of ui_check.py: the file and code checks on synthetic UI folders with a clean control first (offline), the engine comparison on a synthetic engine record, then, with the editor open, the
project's own MainMenu against what the running FlashUI kept and what the loaded movie answers (with its positive and negative control).

    test_ui_check.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import copy
import json
import os
import shutil
import sys
import tempfile

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import ui_check as U

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text="", sev=None):
    return sorted({f["code"] for f in findings if (text in f["message"] or text in f["where"]) and (sev is None or f["severity"] == sev)})


XML = """<UIElements name="T">
  <UIElement name="Menu" mouseevents="1" keyevents="1">
    <GFx file="m.gfx" layer="3"><Constraints><Align mode="dynamic" valign="center" halign="center" scale="1" max="0"/></Constraints></GFx>
    <functions>
      <function name="Setup" funcname="setup"><param name="Caption" type="string"/><param name="Lines" type="int"/></function>
      <function name="Hide" funcname="hide"/>
    </functions>
    <events><event name="OnButton" fscommand="onButton"><param name="Id" type="string"/></event></events>
    <Arrays><Array name="Names" varname="names"/></Arrays>
    <MovieClips><MovieClip name="Cursor" instancename="Cursor"/></MovieClips>
  </UIElement>
</UIElements>
"""
CODE = """
#include <x>
namespace { bool SameName(const char* a, const char* b); }
IUIElement* CUi::Menu() { return gEnv->pFlashUI->GetUIElement("Menu"); }
void CUi::Page(const char* szTitle, int lines, float scale)
{
	Call("Setup", szTitle, lines);
	Call("Hide");
}
void CUi::OnUIEvent(IUIElement* p, const SUIEventDesc& event, const SUIArguments& args)
{
	if (SameName(event.sName, "onButton") || SameName(event.sDisplayName, "OnButton")) { }
}
"""


def make(xml=XML, code=CODE, extra_code=None, files=("m.gfx",), assets=()):
    root = tempfile.mkdtemp(prefix="zz_mcp_ui_")
    ui = os.path.join(root, "Assets", "libs", "ui")
    os.makedirs(os.path.join(ui, "UIElements"))
    os.makedirs(os.path.join(root, "Code"))
    if xml is not None:
        with open(os.path.join(ui, "UIElements", "Menu.xml"), "w", encoding="utf-8") as f:
            f.write(xml)
    for g in files:
        with open(os.path.join(ui, g), "wb") as f:
            f.write(b"CFX")
    with open(os.path.join(root, "Code", "ui.cpp"), "w", encoding="utf-8") as f:
        f.write(code + (extra_code or ""))
    for a in assets:
        p = os.path.join(root, "Assets", a.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"x")
    return root


def run(root, engine=None):
    return U.check(os.path.join(root, "Assets", "libs", "ui"), os.path.join(root, "Code"), engine=engine, with_engine=False, assets_dir=os.path.join(root, "Assets"))


def engine_record(movie=True, missing=()):
    def fn(name, movie_name, params):
        row = {"name": name, "movie_name": movie_name, "desc": "", "dynamic": False, "params": [{"name": "p%d" % i, "movie_name": "p%d" % i, "type": "any"} for i in range(params)]}
        if movie:
            row["in_movie"] = name not in missing
        return row
    el = {"name": "Menu", "group": "T", "file": "m.gfx", "layer": 3, "valid": True, "was_init": True, "movie_loaded": movie, "flags": ["key_events", "mouse_events"],
          "constraints": {"type": "dynamic", "halign": "mid", "valign": "mid", "scale": True, "max": False, "left": 0, "top": 0, "width": 1024, "height": 768},
          "functions": [fn("Setup", "setup", 2), fn("Hide", "hide", 0)],
          "events": [{"name": "OnButton", "fscommand": "onButton", "desc": "", "dynamic": False, "params": [{"name": "Id", "movie_name": "Id", "type": "string"}]}],
          "variables": [], "arrays": [{"name": "Names", "movie_name": "names"}], "movieclips": [{"name": "Root", "movie_name": "_root"}, {"name": "Cursor", "movie_name": "Cursor"}]}
    if movie:
        el["movie"] = {"width": 1, "height": 1, "frames": 1, "frame_rate": 30, "control_root": True, "control_absent": False}
        for r in el["arrays"] + el["movieclips"]:
            r["in_movie"] = True
    return {"available": True, "elements": [el], "actions": [], "event_systems": []}


def main():
    created = []

    def mk(*a, **kw):
        r = make(*a, **kw)
        created.append(r)
        return r

    try:
        r = run(mk())
        check("control: a clean fixture (centre alignment words included) has no error and no warning", r["errors"] == 0 and r["warnings"] == 0, codes(r["findings"]))
        check("control: the harmless 'center' words are information, not warnings", codes(r["findings"], "center", "info") == ["U013"], "")
        check("the code scan finds the 2 calls, the handled event names and the element it uses", r["code"]["calls"] == 2 and r["code"]["events_handled"] == 2 and r["code"]["elements_used"] == ["Menu"], r["code"])
        check("a clean fixture against a matching engine record: still no error and no warning", run(mk(), engine_record())["errors"] == 0 and run(mk(), engine_record())["warnings"] == 0, "")

        # ---- the file
        r = run(mk(xml="<UIElements><UIElement name='A'"))
        check("positive control: XML that does not parse is U001", "U001" in codes(r["findings"]) and r["errors"] >= 1, codes(r["findings"]))
        r = run(mk(xml=XML.replace('<GFx file="m.gfx" layer="3">', "<Other>").replace("</GFx>", "</Other>")))
        check("positive control: an element without <GFx> is U001", codes(r["findings"], "GFx", "error") == ["U001"], "")
        r = run(mk(files=()))
        check("positive control: a movie that is not there is U003", codes(r["findings"], "m.gfx", "error") == ["U003"], "")
        r = run(mk(xml=XML.replace('<function name="Hide" funcname="hide"/>', '<function name="Hide" funcname="hide"/><function name="hide" funcname="hide2"/>')))
        check("positive control: a function whose name equals another's (case-insensitive) is dropped by the engine: U004", codes(r["findings"], "not unique", "error") == ["U004"], "")
        r = run(mk(xml=XML.replace('funcname="hide"/>', 'funcname="setup"/>')))
        check("positive control: two functions with the same MOVIE name are dropped too: U004", codes(r["findings"], "not unique", "error") == ["U004"], "")
        r = run(mk(xml=XML.replace("mouseevents", "mouseevent")))
        check("positive control: a flag attribute spelt wrongly is U013, a warning", codes(r["findings"], "mouseevent", "warning") == ["U013"], "")
        r = run(mk(xml=XML.replace('mode="dynamic"', 'mode="bogus"')))
        check("positive control: an Align mode the engine does not know is U013", codes(r["findings"], "bogus", "warning") == ["U013"], "")
        r = run(mk(xml=XML.replace('valign="center"', 'valign="bottom"')))
        check("control: a valid alignment word is no finding at all", codes(r["findings"], 'valign="bottom"') == [] and codes(r["findings"], 'valign=') == [], codes(r["findings"], "valign"))
        r = run(mk(xml=XML.replace('type="int"', 'type="integer"')))
        check("positive control: a parameter type that is not bool/int/float/string is information (read as any)", codes(r["findings"], "integer", "info") == ["U013"], "")
        r = run(mk(xml=XML.replace('funcname="setup"', 'funcname="_root.setup"')))
        check("control: a movie name written with _root. is the same name (the engine strips it)", r["errors"] == 0 and r["warnings"] == 0, codes(r["findings"]))
        r = run(mk(xml=XML.replace("<Arrays>", "<arrays>").replace("</Arrays>", "</arrays>").replace("<Array ", "<array ")))
        check("control: section and entry tags are case-insensitive (the sample writes <Arrays>)", r["errors"] == 0 and r["files"][0]["elements"][0]["arrays"] == 1, "")

        # ---- the code
        r = run(mk(extra_code='void CUi::More() { Call("Setpu", "a", 1); }\n'))
        check("positive control: a call to a name the element does not declare is U005 (the engine makes a dynamic definition)", codes(r["findings"], "Setpu", "warning") == ["U005"], "")
        r = run(mk(xml=XML.replace('funcname="setup"', 'funcname="doSetup"'), extra_code='void CUi::More() { Call("doSetup", "a", 1); }\n'))
        check("positive control: calling the MOVIE name instead of the declared name is U005 and says so", any("movie name" in f["message"] for f in r["findings"] if f["code"] == "U005"), "")
        r = run(mk(extra_code='void CUi::More() { Call("SETUP", "a", 1); }\n'))
        check("control: the declared name in another letter case is found (the engine compares case-insensitively)", codes(r["findings"], "SETUP") == [], codes(r["findings"], "SETUP"))
        r = run(mk(extra_code='void CUi::More() { Call("Setup", "a"); }\n'))
        check("positive control: too few arguments is U007", codes(r["findings"], "1 argument", "warning") == ["U007"], "")
        r = run(mk(extra_code='void CUi::More() { Call("Setup", "a", 2, true); }\n'))
        check("positive control: too many arguments is U007", codes(r["findings"], "3 argument", "warning") == ["U007"], "")
        r = run(mk(extra_code='void CUi::More() { Call("Setup", 7, "x"); }\n'))
        check("positive control: a number where the declaration says string, and the reverse, is U007 twice", len([f for f in r["findings"] if f["code"] == "U007"]) == 2, "")
        r = run(mk(extra_code='void CUi::More(const char* szT, float f) { Call("Setup", szT, f); }\n'))
        check("control: a float for an int parameter is fine (the movie is loosely typed); parameter names give the kinds", codes(r["findings"], "Setup") == [], codes(r["findings"], "Setup"))
        r = run(mk(extra_code='void CUi::More(bool b) { Call("Setup", "t", b); }\n'))
        check("positive control: a bool passed through a function parameter is typed and mismatched (int declared)", codes(r["findings"], "argument 2", "warning") == ["U007"], "")
        r = run(mk(extra_code='void CUi::More() { Call(\n\t"Setup",\n\t"a", std::max(1, 2)); }\n'))
        check("control: a call over several lines with a nested call in an argument counts 2 arguments", codes(r["findings"], "Setup") == [], codes(r["findings"], "Setup"))
        r = run(mk(extra_code='// Call("Nope", 1);\n/* Call("Nope2"); */\nconst char* s = "Call(\\"Nope3\\")";\n'))
        check("control: calls in comments and strings are not calls", r["errors"] == 0 and r["warnings"] == 0, codes(r["findings"]))
        r = run(mk(extra_code='bool X(IUIElement* e) { return e->CallFunction("Hid", args, &res); }\n'))
        check("positive control: CallFunction(\"typo\") is found too (U005)", codes(r["findings"], "Hid", "warning") == ["U005"], "")
        r = run(mk(extra_code='void CUi::E(const SUIEventDesc& event) { if (SameName(event.sName, "onButon")) {} }\n'))
        check("positive control: handling an fscommand the element does not declare is U008, an error", codes(r["findings"], "onButon", "error") == ["U008"], "")
        r = run(mk(extra_code='void CUi::E(const SUIEventDesc& event) { if (SameName(event.sDisplayName, "OnButtn")) {} }\n'))
        check("positive control: handling a display name that is not declared is U008", codes(r["findings"], "OnButtn", "error") == ["U008"], "")
        r = run(mk(extra_code='IUIElement* X() { return gEnv->pFlashUI->GetUIElement("Nope"); }\n'))
        check("positive control: asking for an element no file declares is U002, an error", codes(r["findings"], "Nope", "error") == ["U002"], "")
        r = run(mk(extra_code='const char* k = "Libs/UI/Textures/gone.dds";\n'))
        check("positive control: naming a Libs/UI file that does not exist is U010", codes(r["findings"], "gone.dds", "error") == ["U010"], "")
        r = run(mk(extra_code='const char* k = "Libs/UI/Textures/here.dds";\n', assets=("libs/ui/textures/here.dds",)))
        check("control: a named file that exists (any letter case) is no finding", codes(r["findings"], "here.dds") == [], "")

        # ---- the engine's record
        e = engine_record()
        e["elements"] = []
        check("positive control: an element the engine did not keep is U002, an error", codes(run(mk(), e)["findings"], "did not keep", "error") == ["U002"], "")
        e = engine_record()
        e["elements"][0]["layer"] = 9
        check("positive control: a different layer is U004", codes(run(mk(), e)["findings"], "layer", "error") == ["U004"], "")
        e = engine_record()
        e["elements"][0]["flags"] = ["key_events"]
        check("positive control: a flag the file asks for that the engine did not set is U004", codes(run(mk(), e)["findings"], "flags", "error") == ["U004"], "")
        e = engine_record()
        e["elements"][0]["functions"].pop()
        check("positive control: a declared function the engine did not keep is U004", codes(run(mk(), e)["findings"], "function 'Hide'", "error") == ["U004"], "")
        e = engine_record()
        e["elements"][0]["functions"][0]["params"].pop()
        check("positive control: a different parameter count is U004", codes(run(mk(), e)["findings"], "parameters", "error") == ["U004"], "")
        e = engine_record()
        e["elements"][0]["constraints"]["halign"] = "lower"
        check("positive control: different constraints are U004", codes(run(mk(), e)["findings"], "constraints", "error") == ["U004"], "")
        e = engine_record(missing=("Setup",))
        r = run(mk(), e)
        check("positive control: a function the code calls and the loaded movie lacks is U006, an error", codes(r["findings"], "Setup", "error") == ["U006"], "")
        e = engine_record(missing=("Hide", "Setup"))
        r = run(mk(code=CODE.replace('Call("Hide");', "")), e)
        check("control: a declared function the movie lacks and nothing calls is information only", codes(r["findings"], "Hide", "info") == ["U006"] and codes(r["findings"], "Hide", "error") == [], "")
        e = engine_record()
        e["elements"][0]["movie"]["control_absent"] = True
        check("positive control: a movie query that says a made-up name is there fails its control: U009, an error", codes(run(mk(), e)["findings"], "controls", "error") == ["U009"], "")
        e = engine_record()
        e["elements"][0]["movie"]["control_root"] = False
        check("positive control: a movie query that cannot see _root fails its control too", codes(run(mk(), e)["findings"], "controls", "error") == ["U009"], "")
        e = engine_record()
        e["elements"][0]["arrays"][0]["in_movie"] = False
        check("control: an array the movie does not have at load time is information (U011)", codes(run(mk(), e)["findings"], "Names", "info") == ["U011"], "")
        e = engine_record()
        e["available"] = False
        check("positive control: no FlashUI in the engine is an error", run(mk(), e)["errors"] >= 1, "")
        e = engine_record()
        e["elements"].append(copy.deepcopy(e["elements"][0]))
        e["elements"][1]["name"] = "Other"
        check("control: an engine element the files do not declare is information only", run(mk(), e)["errors"] == 0, codes(run(mk(), e)["findings"], "Other"))
        # an action file the engine did not load
        root = mk()
        os.makedirs(os.path.join(root, "Assets", "libs", "ui", "UIActions"))
        open(os.path.join(root, "Assets", "libs", "ui", "UIActions", "Intro.xml"), "w").write("<x/>")
        check("positive control: a UI action file the engine did not load is U012", codes(run(root, engine_record())["findings"], "Intro.xml", "warning") == ["U012"], "")
        e = engine_record()
        e["actions"] = [{"name": "Intro", "type": "flowgraph", "valid": True, "enabled": True}]
        check("control: the same file loaded by the engine is no finding", codes(run(root, e)["findings"], "Intro.xml") == [], "")

        # ---- the real project, the real engine
        r = U.check(with_engine=False)
        check("the project's own UI against its own code (no editor): no error", r["errors"] == 0, [f["message"][:60] for f in r["findings"] if f["severity"] == "error"])
        has_main = bool(r.get("files") and r["files"][0].get("elements") and r["files"][0]["elements"][0].get("name") == "MainMenu")   # the numbers below are the GameSDK sample's menu
        if has_main:
            check("the project's MainMenu declares 57 functions, 6 events and uses 23 calls from the code", r["files"][0]["elements"][0]["functions"] == 57 and r["files"][0]["elements"][0]["events"] == 6 and r["code"]["calls"] >= 20, r["code"])
        else:
            print("  --   SKIPPED: this project has no MainMenu UI element (the exact-number checks are GameSDK/Ashvale-specific)")
        try:
            import bridge_py
            bridge_py.send("bridge.camera.get", 10)
            live = True
        except Exception:                                         # noqa: BLE001
            live = False
        if live and not has_main:
            print("  --   SKIPPED: the live checks assert the GameSDK MainMenu element, which this project does not have")
        elif live:
            d = U.engine_dump(True)
            el = d["elements"][0]
            check("live: the engine holds FlashUI and the element MainMenu with its movie file, layer and flags", d["available"] and el["name"] == "MainMenu" and el["file"] == "Menus_Startmenu.gfx" and el["layer"] == 3 and "mouse_events" in el["flags"], el.get("flags"))
            check("live: it kept 57 functions, 6 events, 2 arrays, 3 movie clips (the root and two)", (len(el["functions"]), len(el["events"]), len(el["arrays"]), len(el["movieclips"])) == (57, 6, 2, 3), (len(el["functions"]), len(el["events"])))
            check("live control: the movie is loaded; _root answers yes and a made-up name answers no", el.get("movie_loaded") is True and el["movie"]["control_root"] is True and el["movie"]["control_absent"] is False, el.get("movie"))
            have = {f["name"]: f.get("in_movie") for f in el["functions"]}
            check("live: every function the code calls is in the loaded movie (setupScreen, addButton, ...)", all(have.get(n) is True for n in ("SetupScreen", "AddButton", "AddSlider", "AddSwitch", "AddBackButton", "AddUserStats", "AddLeaderboard", "ShowConfirmation", "AddImage", "HideBackGroundIMG", "showLoader", "hideLoader")), [n for n, v in have.items() if v is False][:8])
            check("live: some declared functions are NOT in the movie (the sample's file is wider than its movie): the query can say no", sum(1 for v in have.values() if v is False) >= 1, sum(1 for v in have.values() if v is False))
            check("live: the engine's event systems include the System ones (flow nodes UI:System)", any(s["name"] == "System" for s in d["event_systems"]), [s["name"] for s in d["event_systems"]])
            r = U.check(with_engine=True)
            check("live: the whole check passes against the running engine: no error, no U004", r["errors"] == 0 and "U004" not in codes(r["findings"]), [f["message"][:70] for f in r["findings"] if f["severity"] == "error"])
            check("live: one movie was loaded and asked per project UI element (MainMenu, and PgBoard: the same movie as a second element)", r["movies_loaded"] == len(os.listdir(os.path.join(U.UI_DIR, "UIElements"))) and r["engine"] is True, r["movies_loaded"])
            # the comparison fires on a real record that is altered
            alt = copy.deepcopy(d)
            alt["elements"][0]["functions"] = [f for f in alt["elements"][0]["functions"] if f["name"] != "AddButton"]
            alt["elements"][0]["layer"] = 7
            rr = U.check(engine=alt)
            check("live control: the same real record with a function removed and another layer is reported (U004)", {"U004"} <= set(codes(rr["findings"])) and rr["errors"] >= 2, codes(rr["findings"]))
        else:
            print("  (editor not answering: the live checks are skipped)")
    finally:
        for r in created:
            shutil.rmtree(r, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
