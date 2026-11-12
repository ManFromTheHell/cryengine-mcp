#!/usr/bin/env python3
"""Copy GameSDK models into this project with everything they need to render.

The GameSDK sample's paks are not mounted by this project, so a model has to be copied in
with its LODs, its materials and every texture those materials name (including CryEngine's
split mip files: foo.dds, foo.dds.1, foo.dds.2 ... and foo.dds.a for the alpha half).

    python level/import_gamesdk_asset.py Objects/natural/trees/pine            (a whole folder)
    python level/import_gamesdk_asset.py Objects/props/furniture/log_furniture/bench_a.cgf

Prints what it copied and any texture it could not find.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import re
import sys
import zipfile

import crypak

# CRYENGINE_GAMESDK_DIR overrides the GameSDK sample project folder (same variable as asset_lint.py)
GAMESDK = os.environ.get("CRYENGINE_GAMESDK_DIR") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Assets\crytek\cryengine-gamesdk-sample-project\5.7.1\gamesdk")
PROJECT_ASSETS = __import__("project_root").ASSETS
PAKS = ["objects-part0.pak", "objects-part1.pak", "textures.pak", "gamedata.pak"]


class Paks:
    """Every entry of the GameSDK paks, read with crypak (CryPak writes backslashes into the
    local headers, which Python's zipfile rejects)."""
    def __init__(self):
        self.index = {}
        self.untouched = 0
        if not os.path.isdir(GAMESDK):
            raise SystemExit("GameSDK sample not found at %s: install it from the CRYENGINE Launcher or set CRYENGINE_GAMESDK_DIR" % GAMESDK)
        for name in PAKS:
            for e in crypak.entries(os.path.join(GAMESDK, name)):
                self.index.setdefault(e["name"].lower(), e)

    def find(self, path):
        return self.index.get(path.lower().replace("\\", "/"))

    def read(self, key):
        return crypak.read(self.index[key])

    def with_prefix(self, prefix):
        p = prefix.lower().replace("\\", "/")
        return [k for k in self.index if k.startswith(p)]

    def copy(self, key, copied):
        e = self.index.get(key)
        if e is None or key in copied:
            return False
        out = os.path.join(PROJECT_ASSETS, *e["name"].split("/"))
        if os.path.isfile(out) and os.path.getsize(out) == e["usize"]:
            # already in the project with the same size: left UNTOUCHED (same bytes, same modified time). Rewriting it would give a project file a fresh timestamp,
            # and a clean-up by timestamp could then delete files the level needs.
            copied.add(key)
            self.untouched += 1
            return True
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(crypak.read(e))
        copied.add(key)
        return True


def textures_of(mtl_bytes):
    text = mtl_bytes.decode("utf-8", "replace")
    return set(re.findall(r'File="([^"]+)"', text))


def main(targets):
    paks = Paks()
    copied, missing = set(), set()
    models = []
    for target in targets:
        t = target.lower().replace("\\", "/").rstrip("/")
        if t.endswith(".cgf") or t.endswith(".cga") or t.endswith(".chr"):
            models.append(t)
        else:
            models += [k for k in paks.with_prefix(t + "/") if k.endswith(".cgf")]

    # Plain materials (terrain layers): the .mtl and its textures.
    for target in targets:
        t = target.lower().replace("\\", "/")
        if t.endswith(".mtl") and paks.find(t) is not None:
            paks.copy(t, copied)
            for tex in textures_of(paks.read(t)):
                tl = tex.lower().replace("\\", "/")
                if tl.startswith("%engine%") or tl.startswith("engineassets"):
                    continue
                stem = re.sub(r"\.(tif|dds|png|tga)$", "", tl)
                hits = [k for k in paks.with_prefix(stem + ".") if re.match(re.escape(stem) + r"\.dds(\.\d+|\.a|\.\d+a)?$", k)]
                if not hits:
                    missing.add(tex)
                for h in hits:
                    paks.copy(h, copied)

    for model in models:
        base = model[:-4]
        for key in paks.with_prefix(base):   # the model, its _lodN files, .cgfm, .chrparams
            if re.match(re.escape(base) + r"(_lod\d)?\.(cgf|cgfm|cga|chr)$", key):
                paks.copy(key, copied)
        folder = model.rsplit("/", 1)[0] + "/"
        for key in paks.with_prefix(folder):
            if key.endswith(".mtl") and key.count("/") == folder.count("/"):
                if paks.copy(key, copied) or key in copied:
                    for tex in textures_of(paks.read(key)):
                        tl = tex.lower().replace("\\", "/")
                        if tl.startswith("%engine%") or tl.startswith("engineassets"):
                            continue
                        stem = re.sub(r"\.(tif|dds|png|tga)$", "", tl)
                        hits = [k for k in paks.with_prefix(stem + ".") if re.match(re.escape(stem) + r"\.dds(\.\d+|\.a|\.\d+a)?$", k)]
                        if not hits:
                            missing.add(tex)
                        for h in hits:
                            paks.copy(h, copied)
    print("copied %d files for %d models%s" % (len(copied) - paks.untouched, len(models), (" (%d more were already in the project: left untouched)" % paks.untouched) if paks.untouched else ""))
    for m in sorted(missing):
        print("  missing texture:", m)


if __name__ == "__main__":
    main(sys.argv[1:])
