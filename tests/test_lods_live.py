#!/usr/bin/env python3
"""Live test of build_mesh's levels of detail in the EDITOR (the game is not run): a mesh with one LOD is built, placed as a scratch brush, and pictures are taken with the engine
left alone and with e_LodMin 1 (the engine must use LOD 1 even close up). The detail parts are in LOD 0 only, so the picture of the brush changes; a control pair shows the noise,
and a mesh WITHOUT a LOD must not change when e_LodMin is raised. About 60 s; needs the editor open.

    test_lods_live.py       # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import sys
import time

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import pipeline as P

results = []
FOLDER = "objects/_mcp_detailtest"             # a path with "_lod" in it is refused by the engine (measured), so not "_mcp_lodtest"


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-112s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:50]))


def main():
    try:
        import bridge_py
        bridge_py.send("bridge.camera.get", 10)
    except Exception:                                           # noqa: BLE001
        print("  SKIPPED: the editor is not open -- 0 checks run (this test needs the live editor)")
        return 0
    import debug_views as D
    import numpy as np
    import object_props as O
    import shot_compare as S
    assets = os.path.join(os.path.dirname(HERE), "Assets")
    base = [{"box": [-1.0, -0.5, 0.0, 1.0, 0.5, 3.0], "material": "stone"}]
    studs = [{"box": [-0.9 + 0.25 * i, -0.62, 0.3 + 0.8 * (i % 3), -0.75 + 0.25 * i, -0.5, 0.5 + 0.8 * (i % 3)], "material": "stone", "detail": 1} for i in range(8)]
    spec = {"name": "slab", "folder": FOLDER, "material_file": "materials/stone", "materials": ["stone"], "parts": base + studs}
    try:
        with_lod = P.build_mesh(dict(spec, lods=1))
        without = P.build_mesh(dict(spec, name="slab_flat"))
        tris = [r["triangles"] for r in with_lod["lods"]]
        check("the mesh is built with a LOD 1 and the compiled file says so (%s triangles)" % tris, with_lod["ok"] and len(tris) == 2 and tris[1] < tris[0], tris)
        eye, tgt = [487, 384, 40.5], [487, 392, 39.2]

        def measure(mesh_file):
            """One scratch brush at a free spot between the houses: two pictures at the engine's own choice of LOD (the noise), one with e_LodMin 1. -> (noise, change) in the box around it.
            One brush at a time: houses have LODs too now (2026-10-07), so a second brush beside a house would be judged against the house's change."""
            with O.scratch("zz_lodt"):
                bridge_py.send("bridge.object.create Brush 487 392 38 zz_lodt_a %s/%s" % (FOLDER, mesh_file))
                time.sleep(2)
                old = D._cvar_get("e_LodMin")
                try:
                    a0, a1 = S.load(D.shoot("lt_0", eye, tgt)), S.load(D.shoot("lt_1", eye, tgt))
                    D._cvar_set("e_LodMin", 1)
                    time.sleep(2)
                    a_lod = S.load(D.shoot("lt_lod", eye, tgt))
                finally:
                    D._cvar_set("e_LodMin", old)
                h, w = a0.shape[:2]
                box = (slice(int(h * .25), int(h * .85)), slice(int(w * .36), int(w * .62)))
                d = lambda x, y: float(np.abs(x[box].astype(int) - y[box].astype(int)).mean())          # noqa: E731
                assert str(D._cvar_get("e_LodMin")) == str(old)
                return d(a0, a1), d(a0, a_lod)
        noise, ch = measure("slab.cgf")
        _, flat = measure("slab_flat.cgf")
        check("with e_LodMin 1 the brush that HAS a LOD changes (%.2f in its box against the noise %.2f): the studs are gone" % (ch, noise), ch > 3 * max(noise, 0.3) and ch > 1.5 * flat, ch)
        check("control: the brush WITHOUT a LOD does not change when e_LodMin is raised (%.2f)" % flat, flat < 3 * max(noise, 0.5), flat)
        check("e_LodMin is back at 0", str(D._cvar_get("e_LodMin")) == "0", D._cvar_get("e_LodMin"))
        check("the scratch brushes are gone", not O.names_like("zz_lodt"), O.object_count())
    finally:
        shutil.rmtree(os.path.join(assets, *FOLDER.split("/")), ignore_errors=True)
    check("cleanup: the throwaway mesh folder is gone", not os.path.exists(os.path.join(assets, *FOLDER.split("/"))), "")
    bad = results.count(False)
    print("lods live test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
