#!/usr/bin/env python3
"""Self-test of editor_ux.py: the guards offline (a fake bridge), then, with the editor open, the real undo stack and Notification Center through scratch objects named zz_mcp_ux_* (deleted in a finally).

    test_editor_ux.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import editor_ux as U

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def raises(fn, text=""):
    try:
        fn()
    except U.UxError as exc:
        return text in str(exc)
    except Exception:                                             # noqa: BLE001
        return False
    return False


def main():
    # ---- the guards, with a fake editor
    sent = []
    state = {"undo_steps": 3, "redo_steps": 1, "latest": ["Claude: create Brush (a)", "Claude: move a", "Move (user)"], "mode": "off"}
    real_send = U._send

    def fake(cmd, timeout=30):
        sent.append(cmd)
        if cmd == "bridge.undo.info":
            return dict(state, ok=True)
        return {"ok": True, "undo_steps": 2, "redo_steps": 2, "latest": []}
    U._send = fake
    try:
        check("notify refuses a level that is not info / warning / critical", raises(lambda: U.notify("loud", "x"), "level"), "")
        check("notify refuses an empty title, a title with the separator '|' and a title over 80 characters", raises(lambda: U.notify("info", "")) and raises(lambda: U.notify("info", "a|b")) and raises(lambda: U.notify("info", "x" * 81)), "")
        sent.clear()
        U.notify("warning", "Audit   finished", "0 errors,\n51 warnings")
        check("notify squeezes whitespace and sends one command", sent == ["bridge.notify warning Audit finished | 0 errors, 51 warnings"], sent)
        sent.clear()
        check("positive control: undo without `expect` is refused and says what the newest steps are", raises(lambda: U.undo("undo", 1, ""), "Claude: create Brush") and sent == ["bridge.undo.info"], sent)
        sent.clear()
        check("positive control: undo whose newest step does not contain `expect` is refused and sends no undo", raises(lambda: U.undo("undo", 1, "Claude: delete")) and not any("step" in c for c in sent), sent)
        sent.clear()
        check("positive control: two steps where the second is the USER's ('Move (user)') are refused when `expect` is 'Claude'", raises(lambda: U.undo("undo", 3, "Claude")) and not any("step" in c for c in sent), sent)
        sent.clear()
        r = U.undo("undo", 2, "Claude")
        check("control: two steps that both contain `expect` are undone with one command", sent[-1] == "bridge.undo.step undo 2" and r["took"] == 2, sent)
        check("redo is refused when the redo stack is shorter than asked, and not otherwise", raises(lambda: U.undo("redo", 5), "redo stack") and U.undo("redo", 1)["took"] == 1, "")
        check("an unknown action is refused", raises(lambda: U.undo("explode"), "action"), "")
    finally:
        U._send = real_send

    # ---- the real editor
    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
        live = True
    except Exception:                                             # noqa: BLE001
        live = False
    if not live:
        print("  (editor not answering: the live checks are skipped)")
    else:
        import object_props as P
        cube = "%ENGINE%/EngineAssets/Objects/primitive_cube.cgf"
        with P.scratch("zz_mcp_ux_"):
            base = U.info()
            check("live: the undo mode is off at rest", base["mode"] == "off", base["mode"])
            P.create("Brush", "zz_mcp_ux_plain", [30, 30, 30], cube)
            after_plain = U.info()
            check("live control: a creation that does not ask for undo adds no undo step (the scratch objects of the tests)", after_plain["undo_steps"] == base["undo_steps"], (base["undo_steps"], after_plain["undo_steps"]))
            r = P.create("Brush", "zz_mcp_ux_mine", [32, 32, 32], cube, undoable=True)
            after = U.info()
            check("live: an undoable creation adds one step named for it, and the mode is back to off", r["undoable"] and after["undo_steps"] == after_plain["undo_steps"] + 1 and "zz_mcp_ux_mine" in after["latest"][0] and after["mode"] == "off", after["latest"][:1])
            check("live positive control: undo with the wrong `expect` is refused and the object is still there", raises(lambda: U.undo("undo", 1, "zz_other")) and P.exists("zz_mcp_ux_mine"), "")
            u = U.undo("undo", 1, "zz_mcp_ux_mine")
            check("live: undo takes the creation back (the object is gone, one redo step)", not P.exists("zz_mcp_ux_mine") and u["redo_steps"] >= 1 and u["undo_steps"] == after_plain["undo_steps"], u["undo_steps"])
            U.undo("redo", 1)
            check("live: redo brings it back", P.exists("zz_mcp_ux_mine"), "")
            n = U.notify("info", "Claude self-test", "The notification center answers")
            check("live: a notification gets an id from the editor's Notification Center", n.get("ok") and isinstance(n.get("id"), int), n)
            check("live: a critical one too, and a message with a quote and a non-ASCII character is accepted", U.notify("critical", "Test \"q\"", "Café ✓")["ok"], "")
            U.undo("undo", 1, "zz_mcp_ux_mine")                 # leave the stack as it was found, minus the scratch
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
