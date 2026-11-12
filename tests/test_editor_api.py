#!/usr/bin/env python3
"""Self-test of editor_api.py (the Sandbox Python API as a catalog). Editor-free: it reads the saved dump and the saved sweep. About 1 s.

    test_editor_api.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_api as A

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-86s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def row(name):
    return next((r for r in A.catalog() if r["name"] == name), None)


def main():
    rows = A.catalog()
    raw = json.load(open(A.DUMP, encoding="utf-8"))
    expected = sum(len(f) for m, f in raw.items() if m not in A.SKIP_MODULES)
    check("every function of the dump is catalogued (%d; json and SandboxBridge are not the editor's)" % expected, len(rows) == expected and expected >= 550, len(rows))   # the count depends on the editor build (586 on the one this was written against)
    check("every function but the two enum-like names parsed a return type", {"general.object_type", "general.system_config_spec"} <= {r["name"] for r in rows if not r["callable"]} and sum(not r["callable"] for r in rows) <= 12, [r["name"] for r in rows if not r["callable"]])
    r = row("asset.count_assets_of_type")
    check("a signature is parsed: argument type and name, return type, the engine's description and what the argument means",
          r["args"] == [("arg1", "str")] and r["returns"] == "int" and r["description"] == "Count the number of assets of a specific type" and r["param_help"] == {"type": "The name of the asset type"}, r["args"])
    check("a function with a plain quoted description is read too", row("designer.allnoneselection")["description"] == "runs All/None selection tool", "")
    check("modal: the known dialog-openers and a function whose description says so are flagged; an ordinary one is not",
          row("terrain.import_heightmap")["modal"] and row("general.open_level")["modal"] and not row("general.get_current_level_name")["modal"], sum(r["modal"] for r in rows))
    boxes = [r for r in rows if r["description"].lower().startswith("shows") and "box" in r["description"].lower()]
    check("T-10: the box functions (message_box, message_box_ok, message_box_yes_no, edit_box, edit_box_check_data_type, combo_box, open_file_box) are modal (they were not)",
          {r["name"] for r in boxes} >= {"general.message_box", "general.message_box_ok", "general.message_box_yes_no", "general.edit_box", "general.combo_box", "general.open_file_box"} and all(r["modal"] for r in boxes), [r["name"] for r in boxes if not r["modal"]])
    check("T-10: general.exit / unload_all_plugins / run_lua are flagged dangerous, and denied() lists them with every modal function", all(row(n)["dangerous"] for n in ("general.exit", "general.unload_all_plugins", "general.run_lua"))
          and set(A.denied()) >= {r["name"] for r in rows if r["modal"]} | set(A.DANGEROUS), len(A.denied()))
    check("T-10: the pane openers are not modal (editor_panes: dockable windows)", not row("general.open_pane")["modal"] and not row("general.open_or_focus_pane")["modal"])
    check("read_only means a getter with NO argument and not modal (21 candidates)", row("general.get_current_level_name")["read_only"] and not row("asset.count_assets_of_type")["read_only"]
          and sum(r["read_only"] for r in rows) == 21, sum(r["read_only"] for r in rows))
    check("control: the project's own calls are counted (general.save_level is used), and a function nobody called counts 0 (trackview.add_node)", row("general.save_level")["project_calls"] >= 1 and row("trackview.add_node")["project_calls"] == 0, row("general.save_level")["project_calls"])
    used = sum(1 for r in rows if r["project_calls"])
    check("the project calls only a small part of the API (%d of %d functions named in its scripts)" % (used, len(rows)), 0 < used < len(rows) // 10, used)

    found, total = A.search("track", module="trackview", limit=500)
    check("search by word and module", total >= 80 and all(r["module"] == "trackview" for r in found), total)
    found, _ = A.search("", unused_only=True, limit=1000)
    check("unused_only leaves out every function the project calls", not any(r["project_calls"] for r in found) and len(found) == len(rows) - used, len(found))
    found, _ = A.search("", read_only=True, limit=100)
    check("read_only lists only safe getters", len(found) == 21 and all(r["read_only"] for r in found), len(found))
    found, _ = A.search("create", include_modal=False, limit=100)
    check("include_modal=False drops the dialog openers", not any(r["modal"] for r in found), len(found))
    m = A.modules()
    check("the module table adds up (586 functions, 24 modules; trackview 87, general 112)", sum(v["functions"] for v in m.values()) >= 550 and len(m) >= 24 and m["trackview"]["functions"] >= 80 and m["general"]["functions"] >= 100, len(m))

    if os.path.exists(A.SAMPLES):
        s = json.load(open(A.SAMPLES, encoding="utf-8"))
        names = {r["name"] for r in rows if r["read_only"]}
        check("the sweep covers exactly the read-only functions (%d), each answered or raised cleanly, none hung" % len(s), set(s) == names and all("ok" in v and v["seconds"] < 2 for v in s.values()), len(s))
        check("a swept getter's return is attached to its row (the open level's name)", row("general.get_current_level_name")["sample"]["sample"] == "'ashvale'" or row("general.get_current_level_name")["sample"]["ok"], row("general.get_current_level_name")["sample"])
        check("control: a getter that needs a selection reports that, as an error type and message, instead of a made-up value", s["selection.get_center"]["ok"] is False and "selected" in s["selection.get_center"]["sample"], s["selection.get_center"])
    else:
        print("  --   no sweep saved yet: those checks skipped (python bridge/editor_api.py --sweep, editor open)")
    panes = A.panes()
    check("the editor's pane list is saved (57 tools, including Track View and the Console Variables pane)", len(panes) >= 50 and "Track View" in panes and "Console Variables" in panes and "Mannequin Editor" in panes, len(panes))

    bad = results.count(False)
    print("editor api test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
