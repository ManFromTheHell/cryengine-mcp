#!/usr/bin/env python3
"""Run CRYENGINE's OWN packager (the .cryproject context menu "Package Build") unattended.

Crytek's Tools/CryVersionSelector/release_project.py asks for the configuration in a Tk dialog and
waits for Enter at the end. This runs release_project.run() unchanged with only that dialog answered
in code -- nothing is added to or changed in its output (the built-in packager is used as is; problems
are fixed in the project, not in a separate builder):

    package_project.py <project.cryproject> [--config win_x64:Profile] [--out <dir>] [--symbols]

The same build comes out of the Launcher: right-click the project -> Package Build.

Known limits of the stock packager in 5.7.1, to be solved in the project:
- It never copies project shader source (.cfx/.cfi/.ext): it only ships file types listed in
  rc_jobs/game_assets.xml, plus every existing Assets/*.pak as-is.
- RC fails to re-convert already-compiled .dds (e.g. GameSDK's), so those textures are missing unless
  the project keeps them in an Assets/*.pak.
- For win_x64:Release it still copies the PROFILE Game.dll (its per-configuration swap checks the
  plugin type "EPluginType::Native"; 5.7 cryprojects write "EType::Native").
Run it OUTSIDE Sandbox (minutes, spawns rc.exe), and export the level first (exporter.export_to_engine).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import builtins
import os
import sys

# CRYENGINE_ENGINE_DIR overrides the Launcher's default 5.7.1 install location (--engine overrides both).
ENGINE = os.environ.get("CRYENGINE_ENGINE_DIR") or os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\CRYENGINE Launcher\Engines\crytek\cryengine-57-lts\5.7.1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--config", default="win_x64:Profile")
    parser.add_argument("--out", default=None)
    parser.add_argument("--symbols", action="store_true")
    parser.add_argument("--engine", default=ENGINE)
    args = parser.parse_args()
    if not os.path.isdir(os.path.join(args.engine, "Tools", "CryVersionSelector")):
        sys.exit("engine folder not found: %s (pass --engine <dir> or set CRYENGINE_ENGINE_DIR)" % args.engine)

    sys.path.insert(0, os.path.join(args.engine, "Tools", "CryVersionSelector"))
    import release_project
    import release_project_gui

    configs = dict(release_project.get_available_configurations(args.engine))
    if args.config not in configs:
        sys.exit("configuration %s not available; have %s" % (args.config, sorted(configs)))

    def configure_build(export_path, _configurations):
        out = args.out or export_path
        print("packaging to", out)
        return (out, args.config, configs[args.config], args.symbols)

    # The only two interactions of the stock packager: the configuration dialog and "Press Enter".
    release_project_gui.configure_build = configure_build
    builtins.input = lambda *a, **k: ""
    release_project.run(os.path.abspath(args.project))


if __name__ == "__main__":
    main()
