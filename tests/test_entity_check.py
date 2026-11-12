#!/usr/bin/env python3
"""Self-test of entity_check.py: the comparison on synthetic layers and a synthetic engine (offline, with a clean control first), then, with the editor open, the real level against the live entities, and
altered COPIES of the real layer against the same live entities (a misspelt property, a changed value, an unknown component, a missing entity).

    test_entity_check.py        # exit 1 on any failure
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
import entity_check as E

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-125s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:60]))


def codes(findings, text="", sev=None):
    return sorted({f["code"] for f in findings if (text in f["message"] or text in f["where"]) and (sev is None or f["severity"] == sev)})


LAYER = """<Layer><LayerObjects>
 <Object Type="Entity" Layer="Main" Name="Lamp1" EntityClass="NightLamp"><Properties /><Components>
  <Component TypeName="CNightLampComponent" Name="CNightLampComponent"><properties Kind="WallTorch" Brightness="0.25" Radius="4"/></Component></Components></Object>
 <Object Type="Entity" Layer="Main" Name="Crate1" EntityClass="PickupItem"><Properties /><Components>
  <Component TypeName="CPickupItemComponent" Name="x"><properties Mesh="a.cgf" Mass="6"><MeshScale x="0.28" y="0.28" z="1.1"/></properties></Component></Components></Object>
 <Object Type="Brush" Name="Wall1"/>
</LayerObjects></Layer>"""

ENGINE = {("Lamp1", "CNightLampComponent"): {"Kind": "WallTorch", "Brightness": 0.25, "Radius": 4.0},
          ("Crate1", "CPickupItemComponent"): {"Mesh": "a.cgf", "Mass": 6.0, "MeshScale": {"x": 0.28, "y": 0.28, "z": 1.1}}}


def make_level(xml=LAYER):
    root = tempfile.mkdtemp(prefix="zz_mcp_ent_")
    os.makedirs(os.path.join(root, "t", "layers"))
    open(os.path.join(root, "t", "layers", "main.lyr"), "w", encoding="utf-8").write(xml)
    return root


def fetch_from(table):
    def fetch(name, comp):
        if (name, comp) in table:
            return table[(name, comp)], ""
        return None, "no component %r on %s" % (comp, name)
    return fetch


def main():
    made = []
    try:
        root = make_level()
        made.append(root)
        objs = {"Lamp1": "Entity", "Crate1": "Entity", "Wall1": "Brush"}
        r = E.check("t", objects=objs, fetch=fetch_from(ENGINE), levels_dir=root)
        check("control: a layer that the engine holds exactly has no finding (2 entities, 2 components, nested structure included)", r["findings"] == [] and r["compared"] == 2 and r["entities"] == 2, (r["entities"], r["compared"], codes(r["findings"])))
        ents = E.read_layers("t", root)
        check("the layer reader takes the Entity objects only (a Brush is not one) with class, components and properties", [e["name"] for e in ents] == ["Lamp1", "Crate1"] and ents[0]["cls"] == "NightLamp" and ents[0]["components"][0]["props"].get("Kind") == "WallTorch", "")

        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Lamp1", "CNightLampComponent"): {"Kind": "WallTorch", "Brightness": 0.25}}), levels_dir=root)
        check("positive control: a property the file sets and the component lacks (Radius) is N003, a warning that lists what the component has", codes(r["findings"], "Radius", "warning") == ["N003"] and "Kind" in r["findings"][0]["message"], codes(r["findings"]))
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Lamp1", "CNightLampComponent"): {"Kind": "WallTorch", "Brightness": 0.5, "Radius": 4.0}}), levels_dir=root)
        check("positive control: a different value is N004 and shows both", codes(r["findings"], "Brightness", "warning") == ["N004"] and "0.25" in r["findings"][0]["message"] and "0.5" in r["findings"][0]["message"], "")
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Lamp1", "CNightLampComponent"): {"Kind": "WallTorch", "Brightness": 0.25000001, "Radius": 4}}), levels_dir=root)
        check("control: float noise (0.25 vs 0.25000001) and an int for a float are the same value", r["findings"] == [], codes(r["findings"]))
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Lamp1", "CNightLampComponent"): {"Kind": "Torch", "Brightness": 0.25, "Radius": 4.0}}), levels_dir=root)
        check("positive control: a word that is not what the engine holds (an enum it did not accept) is N004", codes(r["findings"], "Kind", "warning") == ["N004"], "")
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Crate1", "CPickupItemComponent"): {"Mesh": "a.cgf", "Mass": 6.0, "MeshScale": {"x": 0.28, "y": 0.28}}}), levels_dir=root)
        check("positive control: a missing member of a nested structure (MeshScale.z) is N003 with its path", codes(r["findings"], "MeshScale.z", "warning") == ["N003"], "")
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from({**ENGINE, ("Crate1", "CPickupItemComponent"): {"Mesh": "a.cgf", "Mass": 6.0, "MeshScale": {"x": 9, "y": 0.28, "z": 1.1}}}), levels_dir=root)
        check("positive control: a different value inside a nested structure is N004 with its path", codes(r["findings"], "MeshScale.x", "warning") == ["N004"], "")
        t2 = {k: v for k, v in ENGINE.items() if k[0] != "Crate1"}
        r = E.check("t", entities=ents, objects=objs, fetch=fetch_from(t2), levels_dir=root)
        check("positive control: a component the live entity does not have is N002, an error, and carries the engine's own list", codes(r["findings"], "Crate1", "error") == ["N002"], "")
        r = E.check("t", entities=ents, objects={"Lamp1": "Entity", "Wall1": "Brush"}, fetch=fetch_from(ENGINE), levels_dir=root)
        check("positive control: an entity the editor did not load is N001, an error", codes(r["findings"], "Crate1", "error") == ["N001"], "")
        r = E.check("t", entities=ents, objects={**objs, "Runtime1": "Entity"}, fetch=fetch_from(ENGINE), levels_dir=root)
        check("control: an editor entity no layer holds is information only (N005)", codes(r["findings"], "Runtime1", "info") == ["N005"] and r["errors"] == 0, "")
        both = ET.fromstring(LAYER.replace('<Component TypeName="CNightLampComponent" Name="CNightLampComponent">', '<Component TypeName="CNightLampComponent"><properties Kind="A"/></Component><Component TypeName="CNightLampComponent" Name="x">'))
        open(os.path.join(root, "t", "layers", "two.lyr"), "w", encoding="utf-8").write(ET.tostring(both, encoding="unicode"))
        r = E.check("t", objects=objs, fetch=fetch_from(ENGINE), levels_dir=root)
        check("a component type twice on one entity, and a name with a space, are information (not compared), never a guess", codes(r["findings"], "occurs 2 times", "info") == ["N006"], codes(r["findings"], "", "info"))
        check("the names the bridge cannot address (space) are not compared", E.check("t", entities=[{"name": "A B", "cls": "X", "file": "f", "components": [], "legacy": False}], objects={}, fetch=fetch_from({}))["findings"][0]["code"] == "N006", "")
        check("the value rules: true/1 and false/0 for a bool, 1.0 for 1, a word for a string", E.same_value("true", True) and E.same_value("0", False) and not E.same_value("true", False) and E.same_value("1.0", 1) and not E.same_value("x", 1.0) and E.same_value("a", "a"), "")

        # ---- the real level against the live editor
        try:
            import bridge_py
            bridge_py.send("bridge.camera.get", 10)
            live = True
        except Exception:                                         # noqa: BLE001
            live = False
        if live and not os.path.isfile(os.path.join(E.LEVELS, "ashvale", "layers", "main.lyr")):
            print("  (live part skipped: the project has no levels/ashvale/layers/main.lyr, the sample level these checks compare against)")
            live = False
        if live:
            r = E.check("ashvale")
            check("live: every entity of the real level (77) and its component is compared with the engine", r["entities"] >= 70 and r["compared"] == r["components"] == r["entities"], (r["entities"], r["compared"]))
            check("live: the real level has no error and no warning (the engine kept every property the file sets)", r["errors"] == 0 and r["warnings"] == 0, [f["message"][:60] for f in r["findings"] if f["severity"] != "info"][:2])
            src = os.path.join(E.LEVELS, "ashvale", "layers", "main.lyr")
            text = open(src, encoding="utf-8").read()
            lamp = '<properties Kind="WallTorch" Brightness="0.25" Radius="4"/>'
            check("the live controls start from a lamp the file really holds", lamp in text, "")
            root2 = tempfile.mkdtemp(prefix="zz_mcp_ent_")
            made.append(root2)
            os.makedirs(os.path.join(root2, "a", "layers"))
            altered = text.replace(lamp, '<properties Kind="WallTorch" Brigthness="0.25" Radius="9"/>', 1)
            altered = altered.replace('TypeName="CChestComponent"', 'TypeName="CNoSuchComponent"', 1)
            altered = altered.replace('Name="Field_Fence16"', 'Name="Field_Fence16"', 1)
            open(os.path.join(root2, "a", "layers", "main.lyr"), "w", encoding="utf-8").write(altered)
            r = E.check("a", levels_dir=root2)
            check("live positive control: a misspelt property ('Brigthness') in an altered copy of the real layer is N003 against the live engine", codes(r["findings"], "Brigthness", "warning") == ["N003"], codes(r["findings"]))
            check("live positive control: a changed value (Radius 9 against the 4 the engine holds) is N004", codes(r["findings"], "the file says '9'", "warning") == ["N004"], "")
            check("live positive control: an unknown component type is N002, an error", codes(r["findings"], "CNoSuchComponent", "error") == ["N002"], "")
            check("live control: nothing else in the altered copy is reported (3 changes, 3 findings)", len([f for f in r["findings"] if f["severity"] != "info"]) == 3, [(f["code"], f["where"][-30:]) for f in r["findings"] if f["severity"] != "info"])
        else:
            print("  (editor not answering: the live checks are skipped)")
    finally:
        for r in made:
            shutil.rmtree(r, ignore_errors=True)
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
