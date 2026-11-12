"""
MCP server bridging Claude to a running CryEngine 5.7 instance via
ClaudeBridgePlugin, a genuine Sandbox editor plugin (its source is
Code/Sandbox/Plugins/ClaudeBridgePlugin/ in a CRYENGINE source checkout) that hooks
eNotify_OnIdleUpdate -- "sent every frame while editor is idle" -- so it
responds continuously in edit mode as well as Play mode, and (unlike the old
Game.dll bridge) keeps responding even while the Sandbox window has lost OS
focus -- both confirmed via live PowerShell round-trips against
127.0.0.1:37778.

NOTE on window focus: the plugin drains its command queue from eNotify_OnIdleUpdate
and also from a 25 ms WM_TIMER on a hidden message-only window, so queued commands
run while the Sandbox window is unfocused, minimised or showing a modal dialog. The
editor's own update (for example the deferred game-mode toggle) still runs only
while a Sandbox window is active. Putting "ed_keepEditorActive = 1" in user.cfg has
no effect; changing that CVar from 0 to 1 while the editor is running does (see
docs/troubleshooting.md).

Run this alongside the installed Sandbox.exe (the plugin auto-loads from its
EditorPlugins folder -- nothing to launch separately) and register it as a
local MCP server in the Claude desktop app, e.g. in its MCP config:

    {
      "mcpServers": {
        "cryengine": {
          "command": "python",
          "args": ["C:\\path\\to\\cryengine-mcp\\server.py"]
        }
      }
    }

The plugin listens on 127.0.0.1:37778 by default -- override with the
CRYENGINE_BRIDGE_PORT environment variable if you changed kBridgePort on the
plugin side, or to point this at the older Play-mode-only Game.dll bridge on
37777 instead (see ../Code/GamePlugin.cpp / ../Code/TcpCommandServer.cpp --
kept for standalone-launcher scenarios where no Sandbox editor is running).

Sandbox has THREE separate command registries, and picking the wrong tool for
a given command gets you "Unknown command" even though the command genuinely
exists:

  * IConsole (run_console_command) -- CVars and true console commands only
    (e.g. "goto", "r_DisplayInfo 1"). Does NOT know about "module.command"
    names. Notably, `map <level>` is unconditionally refused here while
    running inside the editor (CCryAction::MapCmd's explicit
    gEnv->IsEditor() guard in CryAction.cpp) -- use load_level instead.
  * ICommandManager (execute_editor_command) -- commands registered via
    REGISTER_EDITOR_AND_SCRIPT_COMMAND / REGISTER_PYTHON_COMMAND, i.e. the
    "module.command" namespace: asset.edit, general.save_level, etc.
  * The actual Python interpreter (execute_python) -- commands registered via
    REGISTER_ONLY_PYTHON_COMMAND_WITH_EXAMPLE, which is Python-only and
    NEVER reaches ICommandManager at all. This is a large and easy-to-miss
    category: general.new_object and general.create_object (the standard way
    to spawn objects/entities from script) are both Python-only -- confirmed
    empirically, execute_editor_command("general.new_object ...") returns
    "Unknown command: general.new_object". Modules are pre-imported as
    "from sandbox import <module>" (see PythonManager.cpp), so call e.g.
    execute_python("general.new_object('EntityWithStaticMeshComponent', "
                    "'objects/sphere.cgf', 'Ball', 512, 512, 50)").
"""
import _paths  # noqa: F401  (puts every tool folder on sys.path)

import itertools
import json
import os
import socket

import threading
import time

from mcp.server.fastmcp import FastMCP

import bridge_py
import server_support

# Opt-in: CLAUDE_AUTO_RECOVER=1 lets a tool that finds the editor gone bring it back (bridge_watchdog.recover: starts Sandbox.exe, and replaces a crashed one with taskkill /F) instead of failing.
# Off by default so a first read-only call never starts or kills an editor; set CLAUDE_AUTO_RECOVER=1 in the MCP server's env block to enable it.
os.environ.setdefault("CLAUDE_AUTO_RECOVER", "0")
HOST = os.environ.get("CRYENGINE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CRYENGINE_BRIDGE_PORT", "37778"))
# The plugin's own command-queue timeout (kCommandTimeoutSeconds in
# ClaudeBridgePlugin.h) is 20s -- keep this comfortably above that so the
# Python client never gives up before the plugin itself would.
TIMEOUT_SECONDS = 25.0

_request_ids = itertools.count(1)

INSTRUCTIONS = """CRYENGINE 5.7.1 project tools (the Ashvale game). tool_guide() maps every tool by family; query_knowledge records the traps (query it before an unfamiliar step).
Work loop: health_check (can the state be trusted? is the package stale?) -> make assets (make_texture, write_material, build_mesh) -> lint_assets -> place_object (needs the
editor) -> start_job('package') -> shoot_game to LOOK at the packaged game -> compare_shots against a control / visual_check -> start_job('tests') before calling it done.
snapshot_project BEFORE anything that deletes, overwrites or rebuilds in bulk: the project has no version control.
Rules every tool keeps: nothing downloads (a tool that would needs the user asked first, naming file, source and size); start_job runs a whitelist of jobs, while execute_python / execute_python_script run arbitrary Python inside the editor (guarded only against accidents by really=true, not a sandbox);
no existing file or object is replaced without overwrite=true; jobs that close the editor (package, tests, shader_cache, shoot, visual, cvar_probe) need allow_editor_close=true: tell the user
first; results are measurements read back from the engine's files, not "ok". Shots and tests use the PACKAGED build: package after any change."""

mcp = FastMCP("cryengine", instructions=INSTRUCTIONS)


def _exchange(cmd: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """One raw round trip to the plugin: no recovery, no explanation. Raises the socket errors as they are."""
    request_id = next(_request_ids)
    payload = json.dumps({"id": request_id, "cmd": cmd}) + "\n"
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.sendall(payload.encode("utf-8"))
        sock.settimeout(timeout)
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("connection closed before a response line was received")
            buffer += chunk
    line, _, _rest = buffer.partition(b"\n")
    try:
        return json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"malformed response from bridge: {line!r}") from e


def _send_command(cmd: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Sends one command to the plugin's TCP listener and returns the parsed JSON response. Raises RuntimeError with a message that says WHAT TO DO
    (T-12: a timeout means a modal dialog may be open -> list_dialogs / click_dialog_button, with the open dialogs attached; a refused connection means
    the editor is gone -> wait_for_editor / health_check). An editor that is gone is brought back (CLAUDE_AUTO_RECOVER) unless the deliberate-close
    marker file exists (T-14); a relaunch is reported in the tool's result as editor_relaunched: true.
    """
    try:
        try:
            response = _exchange(cmd, timeout)
        except ConnectionRefusedError:
            if os.environ.get("CLAUDE_AUTO_RECOVER") != "1":
                raise
            # The editor is GONE (crashed, closed, killed): bring it back now (bridge_watchdog.recover) instead of failing the tool and waiting for the 5-minute scheduled check.
            if not bridge_py.recover_if_allowed():
                raise
            response = _exchange(cmd, timeout)
    except bridge_py.EditorClosedOnPurpose as e:
        raise RuntimeError(str(e)) from e
    except ConnectionRefusedError as e:
        raise RuntimeError(bridge_py.refused_message(HOST, PORT)) from e
    except socket.timeout as e:
        raise RuntimeError(bridge_py.timeout_message(timeout, bridge_py._open_dialogs(), HOST, PORT)) from e

    if not response.get("ok", False):
        err = str(response.get("error", "unknown error from CryEngine bridge"))
        if "timed out waiting" in err.lower() or "genuinely blocked" in err.lower():
            err += " -- " + bridge_py.dialog_hint(bridge_py._open_dialogs())
        raise RuntimeError(err)

    return response


def _send_structured(cmd: str, timeout: float = TIMEOUT_SECONDS):
    """Like _send_command, but for the "bridge.*" commands, whose "output"
    field is itself a JSON payload (an object or array) rather than plain
    console text -- decode that second layer here so tools can return real
    Python data instead of a JSON-in-a-string blob.
    """
    response = _send_command(cmd, timeout=timeout)
    raw = response.get("output", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"malformed structured response from bridge: {raw!r}") from e


@mcp.tool()
def run_console_command(command: str, really: bool = False) -> str:
    """Execute an arbitrary CryEngine CONSOLE command (CVars and true console
    commands only -- see execute_editor_command/execute_python for Sandbox's
    other two command registries) and return whatever it logged.

    Examples: "goto 100 200 30", "r_DisplayInfo 1", "e_TimeOfDay 12".
    Note: "map <level>" is refused inside the editor -- use load_level.
    "quit"/"exit", bridge.window.close and the bridge.execute_* passthroughs of a dangerous call are refused unless really=true.
    """
    hits = server_support.check_console(command)
    if hits and not really:
        raise RuntimeError(server_support.refuse_danger(hits, "this console command")["refused"])
    response = _send_command(command)
    return response.get("output", "")


@mcp.tool()
def execute_editor_command(command: str, really: bool = False) -> dict:
    """Execute a Sandbox ICommandManager command -- the "module.command"
    namespace registered via REGISTER_EDITOR_AND_SCRIPT_COMMAND /
    REGISTER_PYTHON_COMMAND (asset.*, level.*, general.save_level, etc).
    This is a DIFFERENT registry from run_console_command's IConsole and
    execute_python's Python interpreter -- if a command isn't found here,
    try execute_python instead (many general.* object/entity commands, e.g.
    general.new_object, are Python-interpreter-only and never reach this
    registry at all).

    Returns {"ok": true, "log": "<captured console/log output>"} -- check
    "log" for warnings, since a successful "ok" here only means the command
    executed without throwing, not that it did anything (e.g. an unknown
    object typeName just logs a warning and silently no-ops).

    Example: execute_editor_command("asset.edit levels/mylevel/mylevel.level")

    DANGER LIST (T-10): general.exit, general.unload_all_plugins, general.run_lua, general.run_file and every function the editor_api catalog marks `modal`
    (message_box*, edit_box, combo_box, open_file_box, the dialog openers) are refused unless really=true.
    """
    hits = server_support.check_editor_command(command)
    if hits and not really:
        return server_support.refuse_danger(hits, "this editor command")
    return _send_structured(f"bridge.execute_command {command}")


@mcp.tool()
def execute_python(code: str, really: bool = False) -> dict:
    """Execute a line of Python through Sandbox's actual Python interpreter
    (the same one the in-editor Python console uses). Reaches commands
    registered via REGISTER_ONLY_PYTHON_COMMAND_WITH_EXAMPLE that
    execute_editor_command's ICommandManager path can never see -- notably
    general.new_object and general.create_object, the standard way to spawn
    objects/entities from script. Modules are pre-imported as
    "from sandbox import <module>", so call the bare module name.

    Returns {"ok": true, "log": "<captured console/log output>"}.

    Example: execute_python(
        "general.new_object('EntityWithStaticMeshComponent', "
        "'objects/sphere.cgf', 'Ball', 512, 512, 50)"
    )
    -- EntityWithStaticMeshComponent auto-adds both a CStaticMeshComponent
    (the given .cgf) and a CRigidBodyComponent (physics, dynamic by default) --
    see EntityObjectWithStaticMeshComponent.cpp.

    DANGER LIST (T-10): code that names general.exit, general.unload_all_plugins, general.run_lua, general.run_file, any `modal` editor function (message_box*,
    edit_box, combo_box, open_file_box ...), or runs programs / deletes files (os.system, subprocess, shutil.rmtree, os.remove) is refused unless really=true.
    """
    hits = server_support.check_python(code)
    if hits and not really:
        return server_support.refuse_danger(hits, "this Python")
    return _send_structured(f"bridge.execute_python {code}")


def _project_assets_dir() -> str:
    """The active project's asset folder from project_root.PROJECT (CRYENGINE_PROJECT / use_project): the .cryproject's content.assets[0], else <project>/Assets. Needs no editor."""
    import glob
    root = __import__("project_root").PROJECT
    for cp in sorted(glob.glob(os.path.join(root, "*.cryproject"))):
        try:
            with open(cp, encoding="utf-8") as f:
                a = (json.load(f).get("content") or {}).get("assets") or []
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(a, str):
            a = [a]
        if a and isinstance(a[0], str) and a[0]:
            return os.path.join(root, *a[0].replace(chr(92), "/").split("/"))
    return os.path.join(root, "Assets")


class _EditorIO:
    """The editor as load_level_logic / export_logic see it (tests give them a fake)."""
    clock = staticmethod(time.time)
    sleep = staticmethod(time.sleep)

    def __init__(self):
        self.levels_dir = os.path.join(_project_assets_dir(), "levels")

    def level_exists(self, name):
        parts = [x for x in str(name).replace(chr(92), "/").split("/") if x]      # a level may sit in a subfolder: 'sub/name'
        if not parts or ".." in parts:
            return False
        return os.path.isfile(os.path.join(self.levels_dir, *parts, parts[-1] + ".level"))

    def levels(self):
        """Every level folder under levels/ (also in subfolders), as the relative posix path."""
        found = []
        for dirpath, _dirs, files in os.walk(self.levels_dir):
            base = os.path.basename(dirpath)
            if base + ".level" in files:
                found.append(os.path.relpath(dirpath, self.levels_dir).replace(os.sep, "/"))
        return sorted(found)

    def current(self):
        import world_build
        return world_build.current_level()

    def save(self):
        import world_build
        return world_build.save_level()

    def send(self, cmd, timeout):
        resp = bridge_py.send(cmd, timeout)
        if not resp.get("ok", True):
            raise RuntimeError(str(resp.get("error") or resp)[:300])
        return resp

    def dialogs(self):
        out = json.loads(bridge_py.send("bridge.dialog.list", 5).get("output") or "{}")
        return [{"title": d.get("title", ""), "buttons": [b.get("standard") or b.get("text") for b in d.get("buttons", [])]} for d in out.get("dialogs", [])]

    def click_default(self, title):
        bridge_py.send("bridge.dialog.click default %s" % title[:40], 20)

    def start(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        t.start()

        class _H:
            alive = staticmethod(t.is_alive)
            join = staticmethod(t.join)
        return _H()


@mcp.tool()
def load_level(level_name: str, save_current: bool = True) -> dict:
    """Load a level by name via Sandbox's asset system (asset.edit), the
    supported way to open a level from outside the editor UI -- console's
    `map <level>` is unconditionally refused while running inside the editor
    (CCryAction::MapCmd's gEnv->IsEditor() guard).
    The level that is open is SAVED first (save_current=True): leaving a modified level opens a modal
    'Save Level?' prompt that blocks the bridge. If that save FAILS the level is not left (the answer says
    save_failed); save_current=False skips the save and discards unsaved changes.
    It WAITS until the level is the open one (up to 3 minutes) and returns {ok, level, seconds, n_warnings, ...}
    (n_warnings = warning lines the load logged); an open dialog is reported with `dialog_open` and what to click,
    never waited out. A level that does not exist is refused with the list of the ones that do.
    """
    return server_support.load_level_logic(level_name, save_current, _EditorIO())


@mcp.tool()
def goto_position(x: float, y: float, z: float, ax: float = 0, ay: float = 0, az: float = 0) -> str:
    """Move the editor camera / player to a world position, with optional
    angles in degrees (equivalent to the console's `goto` command).
    """
    return run_console_command(f"goto {x} {y} {z} {ax} {ay} {az}")


@mcp.tool()
def project_info() -> dict:
    """WHICH PROJECT these tools work on, and which one the open editor runs. The tools (asset checks, lints, jobs, package, snapshots, catalogs ...) read and write the ACTIVE
    project's folder; the EDITOR-side tools talk to whatever project the open Sandbox has loaded. They are two settings: this tells both, and says when they disagree (then
    editor-side results describe a different project than the files). The active project is chosen by the environment variable CRYENGINE_PROJECT, else active_project.txt in the repository root,
    else the parent folder of the repository (the layout where the repository sits inside the project folder; see project_root.py). Change it with use_project. Also lists the projects the engine knows (projects.json)."""
    import project_root
    out = {"active_project": project_root.PROJECT, "name": project_root.NAME, "chosen_by": project_root.WHY, "has_cryproject": os.path.isfile(project_root.CRYPROJECT)}
    known = []
    try:
        d = json.load(open(project_root.PROJECTS_JSON, encoding="utf-8"))
        for p in d.get("projects", []):
            known.append({"name": p.get("name"), "path": p.get("path"), "editor_startup": bool(p.get("startupProject"))})
    except (OSError, ValueError):
        pass
    out["engine_knows"] = known
    try:
        # the verb writes its answer to a file: it takes the output path as its argument
        import tempfile
        _out = os.path.join(tempfile.gettempdir(), "cryengine_mcp_project_info.json").replace(os.sep, "/")
        if os.path.exists(_out):
            os.remove(_out)
        _reply = bridge_py.send("bridge.project.info " + _out, 30)
        if not _reply.get("ok"):
            info = {"error": _reply.get("error", "bridge.project.info failed")}
        else:
            info = json.load(open(_out, encoding="utf-8"))
    except Exception as e:                                  # the editor is not running: the file side still works
        info = {"editor": "not reachable: %s" % e}
    out["editor"] = info if info is not None else "ask run_console_command('bridge.project.info') or cryproject_check"
    ed_path = None
    if isinstance(info, dict):
        ed_path = info.get("root") or info.get("path") or info.get("file")
    if ed_path:
        same = os.path.normcase(os.path.abspath(os.path.dirname(ed_path) if ed_path.lower().endswith(".cryproject") else ed_path)) == os.path.normcase(os.path.abspath(project_root.PROJECT))
        out["editor_matches_active_project"] = same
        if not same:
            out["warning"] = "the open editor runs a different project than the one the file tools use"
    return out


@mcp.tool()
def use_project(path: str) -> dict:
    """Make another CRYENGINE project the one these tools work on (a folder holding Game.cryproject, or the file itself). Writes active_project.txt in the repository root; the tool modules read it
    when the MCP server starts, so RESTART/RECONNECT the cryengine MCP server afterwards (the result says so). Nothing in the project is changed and the editor is not touched: the
    editor-side tools keep describing whatever project the open Sandbox has until the editor is reopened on this one (the engine's project list is projects.json). path='' clears it
    (back to the parent folder of the repository)."""
    import project_root
    if not path:
        if os.path.isfile(project_root.ACTIVE_FILE):
            os.remove(project_root.ACTIVE_FILE)
        return {"cleared": True, "note": "restart the MCP server: the default is the folder holding this server again"}
    p = project_root._as_project(path)
    if not p:
        return {"ok": False, "refused": "no Game.cryproject at %s" % path}
    open(project_root.ACTIVE_FILE, "w", encoding="utf-8").write(p + "\n")
    return {"ok": True, "active_project_after_restart": p, "now_still": project_root.PROJECT, "note": "restart/reconnect the cryengine MCP server for the file tools to use it"}


@mcp.tool()
def ping() -> dict:
    """Connectivity check -- confirms the bridge is reachable and the
    engine is actually ticking it (i.e. you're in an active Play session),
    without changing any state. Reads the r_DisplayInfo cvar, which exists
    in every CRYENGINE project.
    """
    return get_cvar("r_DisplayInfo")


@mcp.tool()
def take_screenshot(filename: str = "") -> dict:
    """Take a screenshot via the engine's renderer (IRenderer::ScreenShot).
    Pass a filename to control where it's saved; leave it blank to let the
    engine pick its own default location and name.
    """
    cmd = "bridge.screenshot" if not filename else f"bridge.screenshot {filename}"
    return _send_structured(cmd)


@mcp.tool()
def get_cvar(name: str) -> dict:
    """Read a console variable's current value and help text directly from
    the engine (via ICVar), e.g. get_cvar("r_DisplayInfo") or
    get_cvar("e_TimeOfDay"). More reliable than run_console_command for
    reading values, since it doesn't depend on scraping console text.
    """
    return _send_structured(f"bridge.cvar_get {name}")


@mcp.tool()
def set_cvar(name: str, value: str) -> dict:
    """Set a console variable to a new value directly via ICVar, e.g.
    set_cvar("r_DisplayInfo", "1") or set_cvar("e_TimeOfDay", "12"). Returns
    the cvar's value after the change, read back from the engine.
    """
    return _send_structured(f"bridge.cvar_set {name} {value}")


@mcp.tool()
def list_entities(name_filter: str = "", limit: int = 100, offset: int = 0) -> dict:
    """List entities currently in the loaded level: {"items": [{"id", "name", "pos": [x, y, z]}], shown, offset, total, total_is_lower_bound,
    truncated, next_offset, note}. Pass name_filter to only return entities whose name contains that substring. The bridge returns at most 200
    entities (truncated=true says when that cut the list: narrow the filter); limit (1-500, default 100) and offset page what it returned.
    """
    cmd = "bridge.entities.list" if not name_filter else f"bridge.entities.list {name_filter}"
    try:
        return server_support.page(_send_structured(cmd), limit, offset, cap=200)
    except ValueError as exc:
        return _refusal(exc)


@mcp.tool()
def teleport_entity(name: str, x: float, y: float, z: float) -> dict:
    """Move an entity (found by exact name, via IEntitySystem::FindEntityByName)
    to a new world position. Unlike goto_position, this moves a specific
    named entity rather than the camera/player.
    """
    return _send_structured(f"bridge.entities.teleport {name} {x} {y} {z}")


@mcp.tool()
def generate_terrain_height(
    passes: int = 4,
    frequency: float = 3.0,
    frequency_step: float = 1.7,
    fade: float = 0.8,
    cover: int = 128,
    seed: int = 17,
    sharpness: float = 0.995,
    smoothness: int = 1,
) -> dict:
    """Replace the current (flat) heightmap with real fractal-noise height
    variation -- the same generator as Sandbox's "Generate Terrain" dialog
    (CHeightmap::GenerateTerrain). All args are optional; defaults are a
    moderate, natural-looking setting. Valid ranges: passes 1-10,
    frequency 1.1-10.0, frequency_step 1.0-2.5, fade 0.1-2.0, cover 0-255,
    seed 0-32, sharpness 0.990-0.999, smoothness(blur) 0-3. Width/height are
    taken from the heightmap's own current resolution. This REPLACES the
    heightmap of the open level (nothing is saved until save_level; snapshot_project
    first, editor_undo can revert it); values outside the ranges are refused.
    """
    for _n, _v, _lo, _hi in (("passes", passes, 1, 10), ("frequency", frequency, 1.1, 10.0), ("frequency_step", frequency_step, 1.0, 2.5),
                             ("fade", fade, 0.1, 2.0), ("cover", cover, 0, 255), ("seed", seed, 0, 32), ("sharpness", sharpness, 0.990, 0.999),
                             ("smoothness", smoothness, 0, 3)):
        if not _lo <= _v <= _hi:
            raise RuntimeError("%s must be %s to %s (got %s)" % (_n, _lo, _hi, _v))
    cmd = (
        f"bridge.terrain.generate_height {passes} {frequency} {frequency_step} "
        f"{fade} {cover} {seed} {sharpness} {smoothness}"
    )
    return _send_structured(cmd)


@mcp.tool()
def set_terrain_layer_texture(path: str) -> dict:
    """Point the currently-selected terrain layer at a custom diffuse
    texture (CLayer::LoadTexture), replacing the default white.dds. Path is
    resolved through CryPak relative to the project's Assets folder (e.g.
    "textures/terrain_grass_dirt.bmp"). Must be power-of-2 sized. Requires a
    terrain layer to already be selected (e.g. via terrain.create_layer).
    """
    return _send_structured(f"bridge.terrain.set_layer_texture {path}")


@mcp.tool()
def list_terrain_layers() -> list:
    """List every terrain layer as {"index", "id", "name", "selected"}.
    "selected" reflects CLayer::IsSelected() -- what set_terrain_layer_texture
    and the ICommandManager terrain.flood_layer/terrain.delete_layer/
    terrain.duplicate_layer/terrain.move_layer_* commands actually operate
    on. Selection does not survive a level reload, so check this before
    assuming a prior select_terrain_layer call (or terrain.create_layer,
    which auto-selects) is still in effect.
    """
    return _send_structured("bridge.terrain.list_layers")


@mcp.tool()
def select_terrain_layer(layer: str) -> dict:
    """Select a terrain layer by 0-based index or by exact name (first
    match), via CTerrainManager::SelectLayer -- the only way to (re)select
    an EXISTING layer, since terrain.create_layer only auto-selects the
    layer it just created. Needed before set_terrain_layer_texture or any
    of the "operates on the selected layer" ICommandManager commands
    (terrain.flood_layer, terrain.delete_layer, terrain.duplicate_layer,
    terrain.move_layer_*) when the layer you want isn't the one already
    selected. Pass an int-like string ("0", "2", ...) or a layer name.
    """
    return _send_structured(f"bridge.terrain.select_layer {layer}")


# ---------------------------------------------------------------------------
# Level-building and AI tools.
#
# Learned from the GameSDK sample project (woodland / airfield levels); the
# reasoning behind each one is in rag/cryengine_project_rag.jsonl -- call
# query_knowledge("...") before guessing.
# ---------------------------------------------------------------------------
import base64
import re
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "rag"))

import crypak  # noqa: E402
import level_inventory  # noqa: E402

_GUID_RE = re.compile(r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$")


def _python_script(source: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Run multi-line Python in Sandbox via bridge.execute_python.b64, so
    indentation survives (the plain execute_python route collapses whitespace)."""
    b64 = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return _send_structured("bridge.execute_python.b64 " + b64, timeout=timeout)


def _python_json(source: str, timeout: float = TIMEOUT_SECONDS):
    """Run a script that ends with print("@@JSON " + json.dumps(result)) and
    return that result. Raises with the script's own output on failure."""
    res = _python_script("import json\n" + source + "\n", timeout=timeout)
    log = res.get("log", "")
    for line in reversed(log.splitlines()):
        if line.startswith("@@JSON "):
            return json.loads(line[len("@@JSON "):])
    raise RuntimeError("script returned no result. Output:\n" + log[-3000:])


def _asset_root() -> str:
    """The active project's asset folder (e.g. <project>/gamesdk or <project>/Assets): the .cryproject's content.assets[0] of project_root.PROJECT (the CRYENGINE_PROJECT
    variable / use_project). Read from disk, no editor needed; not cached, so use_project and edits to the .cryproject are seen."""
    return _project_assets_dir()


def _gamesdk_assets() -> str:
    """The GameSDK sample project's asset folder (the reference content: its Lua entity scripts, archetype libraries, levels), read from its paks; '' when the launcher does not have it"""
    import glob
    p = os.environ.get("GAMESDK_ASSETS")     # set it to the sample's gamesdk asset folder when the Launcher keeps it elsewhere
    if not p:
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "CRYENGINE Launcher", "Assets", "crytek", "cryengine-gamesdk-sample-project")
        p = (sorted(glob.glob(os.path.join(base, "*", "gamesdk")))[-1:] or [""])[0]
    return p if p and os.path.isdir(p) else ""


def _archetype_libraries() -> dict:
    """{library name: the asset root that holds it} from the project and then the GameSDK sample (loose or packed)"""
    found = {}
    for root in (_asset_root(), _gamesdk_assets()):
        if not root:
            continue
        for name, _size, _src in project_inventory.list_matching(root, r"(?i)libs/entityarchetypes/[^/]+\.xml$"):
            found.setdefault(os.path.splitext(os.path.basename(name))[0], root)
    return found


def _load_archetype_library(library: str) -> list:
    libs = _archetype_libraries()
    root = libs.get(library) or next((r for n, r in libs.items() if n.lower() == library.lower()), None)
    data = crypak.read_asset(root, "Libs/EntityArchetypes/%s.xml" % library) if root else None
    if data is None:
        raise RuntimeError("archetype library '%s' not found; the libraries available are %s (the project's own Assets have none: they come from the GameSDK sample's paks)" % (library, sorted(libs) or "none"))
    import xml.etree.ElementTree as ET
    out = []
    for p in ET.fromstring(data).iter("EntityPrototype"):
        props = p.find("Properties")
        get = (lambda k: props.get(k)) if props is not None else (lambda k: None)
        out.append({
            "archetype": "%s.%s" % (p.get("Library"), p.get("Name")),
            "guid": p.get("Id"),
            "class": p.get("Class"),
            "faction": get("esFaction"),
            "behavior_tree": get("esModularBehaviorTree"),
            "equipment": get("equip_EquipmentPack"),
            "navigation_type": get("esNavigationType"),
            "model": get("fileModel"),
        })
    return out


@mcp.tool()
def execute_python_script(code: str, include_engine_log: bool = False, really: bool = False) -> dict:
    """Run a MULTI-LINE Python script inside Sandbox with indentation intact
    (bridge.execute_python.b64). Prefer this over execute_python for anything
    with a loop, try/except or def -- execute_python collapses whitespace.

    Sandbox modules (general, object, level, terrain, vegetation, ...) are in
    globals. There is no `ai` module; use execute_editor_command("ai.<cmd>").
    Keep it short: the script runs on the editor main thread, and anything over a
    couple of seconds (os.walk of a big tree, subprocess.run, a socket call back
    into the bridge) freezes the editor and times the bridge out.
    Returns {"log": <stdout + tracebacks>} and optionally the C++ engine_log. A long log is saved to a file and summarised (read_output pages it).
    The same danger list as execute_python applies (really=true to override).
    """
    hits = server_support.check_python(code)
    if hits and not really:
        return server_support.refuse_danger(hits, "this Python script")
    res = _python_script(code)
    if not include_engine_log:
        res.pop("engine_log", None)
    return res


@mcp.tool()
def list_objects(name_filter: str = "", limit: int = 100, offset: int = 0) -> dict:
    """List EDITOR objects (brushes, entities, archetypes, nav areas, shapes...)
    as {"items": [...], shown, offset, total, total_is_lower_bound, truncated, next_offset, note}: each item has
    name, type, pos, rotDeg, scale, material, hidden/frozen. The bridge returns at most 500 (truncated=true says
    when that cut the list; total is the level's real object count when no filter is given); limit (1-500,
    default 100) and offset page what it returned. Pass name_filter (case-insensitive substring) on big levels. Unlike
    list_entities this includes non-entity objects such as NavigationArea.
    For a complete, uncapped view of a level use inspect_level instead.
    (object.get_all_objects is broken under Sandbox's Python 3: it raises
    "expected bytes, str found".)
    """
    cmd = "bridge.object.list" if not name_filter else "bridge.object.list " + name_filter
    rows = _send_structured(cmd)
    total = None
    if not name_filter and len(rows) >= 500:
        try:
            total = _python_json("print('@@JSON ' + json.dumps(general.get_objects_count()))")
        except Exception:                                           # noqa: BLE001 - the total is a nicety
            total = None
    try:
        return server_support.page(rows, limit, offset, cap=500, total=total)
    except ValueError as exc:
        return _refusal(exc)


@mcp.tool()
def list_archetypes(library: str = "Characters", name_filter: str = "") -> list:
    """List entity archetypes in Libs/EntityArchetypes/<library>.xml (read straight from the loose file
    or the .pak) of the loaded project and then of the GameSDK sample, with the fields that define an AI
    character: faction, behavior_tree, equipment, navigation_type, model. GameSDK libraries: Characters,
    Animals, Lights, Props_Destroyable, Props_Interactive, Props_Physics. An unknown library is refused
    with the list of the ones that exist.
    """
    items = _load_archetype_library(library)
    if name_filter:
        items = [i for i in items if name_filter.lower() in i["archetype"].lower()]
    return items


@mcp.tool()
def spawn_archetype(archetype: str, x: float, y: float, z: float, name: str = "") -> dict:
    """Place an entity archetype -- the way GameSDK places its AI soldiers
    (airfield's Multiplayer_AI layer: 12 x Characters.Enemy.Easy_Rifle).
    `archetype` is "Library.Name" (e.g. "Characters.Enemy.Easy_Rifle") or a GUID.
    The archetype carries faction, behavior tree, equipment, model and nav agent
    type, so nothing else needs setting. The AI only moves if it stands on a
    navmesh built for its esNavigationType -- see create_navigation_area.
    Archetype properties are NOT readable per instance through
    general.get_entity_property ("invalid property"); read them with
    list_archetypes.
    """
    guid = archetype
    if not _GUID_RE.match(archetype):
        library = archetype.split(".", 1)[0]
        match = [i for i in _load_archetype_library(library) if i["archetype"].lower() == archetype.lower()]
        if not match:
            raise RuntimeError("no archetype '%s' in library %s -- see list_archetypes" % (archetype, library))
        guid = match[0]["guid"]
    guid = "{" + guid.strip("{}").upper() + "}"
    return _python_json(
        "n = general.new_object('EntityArchetype', %r, %r, %f, %f, %f)\n"
        "if not n:\n"
        "    raise RuntimeError('new_object returned no object for archetype %s')\n"
        # object.get_object_layer raises SystemError under Python 3 -- do not add it back.
        "print('@@JSON ' + json.dumps({'name': n, 'type': object.get_object_type(n), "
        "'pos': list(object.get_position(n)), 'guid': %r}))"
        % (guid, name, x, y, z, guid, guid)
    )


@mcp.tool()
def create_navigation_area(points: list, floor_z: float, height: float = 16.0, exclusion: bool = False,
                           name: str = "", remove_inaccessible_triangles: bool = True) -> dict:
    """Create an MNM NavigationArea from WORLD-space [x, y] polygon points.

    Rules this encodes (each cost real time to find):
      * The area is a prism extending UP from floor_z by `height`. floor_z must
        be BELOW the lowest ground inside it and floor_z+height ABOVE the
        highest, or that ground is not voxelised. GameSDK's woodland uses
        heights of 8-18 m with the floor a few metres under the terrain.
      * general.nav_insert_point wants OBJECT-RELATIVE coordinates; this tool
        converts, placing the object at points[0].
      * remove_inaccessible_triangles (default true, the engine default) only
        bites once a NavigationSeedPoint lies inside the area: then every island
        not connected to a seed is dropped (rooftops, rock tops). With no seed in
        the volume the whole mesh counts as accessible
        (NavigationSystem::ComputeMeshesAccessibility). So one misplaced seed can
        erase most of an area -- put seeds on the walkable ground you care about.
      * exclusion=True makes a hole volume that cuts the mesh of the areas it
        overlaps; woodland uses 28 of them around rocks, water and props.
      * All agent types in Scripts/AI/Navigation.xml are enabled on a new area.
    """
    if len(points) < 3:
        raise RuntimeError("a navigation area needs at least 3 points")
    ox, oy = float(points[0][0]), float(points[0][1])
    rel = [(float(p[0]) - ox, float(p[1]) - oy) for p in points]
    # set_entity_param rejects 'RemoveInaccessibleTriangles' ("is an invalid parameter",
    # tested 2026-09-15) although the object has that variable, so it is only attempted
    # when the caller asks for the non-default value, and a failure is reported, not fatal.
    # Any other failure deletes the half-built area so no stray object is left behind.
    return _python_json(
        "n = general.new_object('NavigationArea', '', %r, %f, %f, %f)\n"
        "if not n:\n"
        "    raise RuntimeError('could not create NavigationArea')\n"
        "notes = []\n"
        "try:\n"
        "    general.set_entity_param(n, 'Height', %f)\n"
        "    general.set_entity_param(n, 'Exclusion', %s)\n"
        "    for i, (px, py) in enumerate(%r):\n"
        "        general.nav_insert_point(n, i, px, py, 0.0)\n"
        "except BaseException:\n"
        "    object.delete(n)\n"
        "    raise\n"
        "if not %s:\n"
        "    try:\n"
        "        general.set_entity_param(n, 'RemoveInaccessibleTriangles', False)\n"
        "    except Exception as e:\n"
        "        notes.append('RemoveInaccessibleTriangles not set (' + str(e) + '); still on - set it in the Properties panel')\n"
        "print('@@JSON ' + json.dumps({'name': n, 'pos': list(object.get_position(n)), "
        "'height': general.get_entity_param(n, 'Height'), 'exclusion': general.get_entity_param(n, 'Exclusion'), "
        "'points': %d, 'notes': notes}))"
        % (name, ox, oy, floor_z, height, bool(exclusion), rel, bool(remove_inaccessible_triangles), len(rel))
    )


@mcp.tool()
def create_ai_path(points: list, name: str = "", agent_type: str = "Villager", closed: bool = False,
                   allow_off_navmesh: bool = False) -> dict:
    """Create an AIPath shape from WORLD-space [x, y, z] points -- the editor's
    "AI Path" object, a named route designers draw for AI to walk.

    Workflow (the built-in CRYENGINE one): lay down a NavigationArea
    (create_navigation_area), regenerate_navigation, THEN draw the path inside it.
    Every point is tested against agent_type's navmesh; if any point is off the
    mesh the path is deleted and the offending points are returned, unless
    allow_off_navmesh=True. A point off the mesh is unreachable, so an NPC
    walking the path would skip it.

    The AIPath registers itself with the AI system as a designer path under its
    object name. My Project's Villager walks it when its "Patrol AI Path"
    property (PatrolPath) is set to that name; game code reads it through
    gEnv->pAISystem->GetINavigation()->GetPointOnPathBySegNo. closed=True sets
    the shape's Closed flag so the route loops; open paths are walked there and
    back. Needs the ClaudeBridge plugin build with bridge.shape.add_points.
    """
    if len(points) < 2:
        raise RuntimeError("an AI path needs at least 2 points")
    p0 = points[0]
    created = _python_json(
        "n = general.new_object('AIPath', '', %r, %f, %f, %f)\n"
        "if not n:\n"
        "    raise RuntimeError('could not create AIPath')\n"
        "notes = []\n"
        "if %s:\n"
        "    try:\n"
        "        general.set_entity_param(n, 'Closed', True)\n"
        "    except Exception as e:\n"
        "        notes.append('Closed not set (' + str(e) + ') - tick it in the Properties panel')\n"
        "print('@@JSON ' + json.dumps({'name': n, 'pos': list(object.get_position(n)), 'notes': notes}))"
        % (name, float(p0[0]), float(p0[1]), float(p0[2]), bool(closed))
    )
    obj = created["name"]
    coords = " ".join("%f %f %f" % (float(p[0]), float(p[1]), float(p[2])) for p in points)
    try:
        added = _send_structured("bridge.shape.add_points %s %s %s" % (obj, agent_type or "-", coords))
    except Exception:
        _python_json("object.delete(%r)\nprint('@@JSON {}')" % obj)
        raise
    if added.get("offMesh") and not allow_off_navmesh:
        _python_json("object.delete(%r)\nprint('@@JSON {}')" % obj)
        return {"ok": False, "deleted": obj,
                "reason": "%d of %d points are off the %s navmesh -- keep the path inside the navigation "
                          "area (and regenerate the navmesh first)" % (added["offMesh"], added["added"], agent_type),
                "points": added.get("points")}
    if agent_type and not added.get("agentTypeValid"):
        created.setdefault("notes", []).append("agent type %r unknown or no AI system: points were NOT checked "
                                               "against the navmesh" % agent_type)
    return {"ok": True, **created, "pointCount": added.get("pointCount"), "points": added.get("points")}


@mcp.tool()
def create_nav_seed_point(x: float, y: float, z: float, name: str = "") -> dict:
    """Place a NavigationSeedPoint. Navmesh triangles connected to a seed point
    are kept; islands with no seed (rooftops, inside rocks) are removed when the
    area has RemoveInaccessibleTriangles on. Put one on walkable ground inside
    every separately reachable region. Woodland has 5 for its 5 inclusion areas.
    """
    return _python_json(
        "n = general.new_object('NavigationSeedPoint', '', %r, %f, %f, %f)\n"
        "if not n:\n"
        "    raise RuntimeError('could not create NavigationSeedPoint')\n"
        "print('@@JSON ' + json.dumps({'name': n, 'type': object.get_object_type(n), 'pos': list(object.get_position(n))}))"
        % (name, x, y, z)
    )


@mcp.tool()
def regenerate_navigation(agent_type_index: int = 0) -> dict:
    """Rebuild the navmesh for one agent type WITHOUT the modal dialog, via
    ai.regenerate_agent_type_layer<N> (RequestGlobalUpdateForAgentType). N is the
    agent type's order in Scripts/AI/Navigation.xml (GameSDK: 0 =
    MediumSizedCharacters, 1 = VehicleMedium). Do NOT use
    ai.regenerate_agent_type_all or ai.regenerate_mnm_type('all'): both open
    "MNM Navigation Rebuilding request" and block the editor until clicked.
    The rebuild runs in the background; watch it with set_ai_debug(navigation=1).
    """
    if not 0 <= agent_type_index <= 5:
        raise RuntimeError("agent_type_index must be 0-5 (only 6 commands are registered)")
    return execute_editor_command("ai.regenerate_agent_type_layer%d" % agent_type_index)


@mcp.tool()
def set_ai_debug(navigation: int = 1, movement: bool = False, path_follower: bool = False,
                 pathfinding: bool = False, off: bool = False) -> dict:
    """Toggle the AI debug overlays that make navigation visible in the
    viewport. navigation: 0 off, 1 triangles+contour, 2 +mesh, 3 +external
    links, 5 island IDs (islands = regions not connected to each other).
    movement shows each actor's plan ([FollowPath] ...); path_follower draws the
    followed path. ai_DebugDraw is the master switch and is set automatically.
    """
    values = {
        "ai_DebugDraw": 0 if off else 1,
        "ai_DebugDrawNavigation": 0 if off else navigation,
        "ai_DebugMovementSystem": 0 if off else int(movement),
        "ai_DrawPathFollower": 0 if off else (2 if path_follower else 0),
        "ai_DebugPathfinding": 0 if off else int(pathfinding),
    }
    return {k: _send_structured("bridge.cvar_set %s %s" % (k, v)).get("value") for k, v in values.items()}


@mcp.tool()
def list_windows() -> dict:
    """List Sandbox's top-level windows (out-of-band: answers even while a modal
    dialog blocks the main thread). The only way to notice that a command is
    stuck on a dialog such as "MNM Navigation Rebuilding request" or
    "Navigation System Warning".
    """
    return _send_structured("bridge.window.list")


@mcp.tool()
def list_dialogs() -> dict:
    """The editor's open dialogs, read through Qt on the GUI thread (bridge.dialog.list): title,
    question text, and buttons {text, standard, enabled, default}. Answers even while a queued
    command is stuck inside the dialog -- it has its own queue."""
    return _send_structured("bridge.dialog.list")


@mcp.tool()
def click_dialog_button(button: str, title: str = "") -> dict:
    """Click a dialog button by name (bridge.dialog.click): yes / no / ok / cancel / save / discard /
    default, or the button's text without spaces. Targets the active modal unless `title` (part of
    one) picks another. Needs no focus and cannot reach another program's window. It is a real
    click, so the dialog's caller gets that button as its answer; confirm with list_dialogs."""
    return _send_structured(("bridge.dialog.click %s %s" % (button, title)).strip())


@mcp.tool()
def export_level(wait_seconds: float = 120.0, answer_dialogs: bool = True, max_attempts: int = 3) -> dict:
    """File > Export to Engine (exporter.export_to_engine). This is what writes
    the navmesh (mnmnav<mission>.bai), cover surfaces (cover<mission>.bai) and
    level.pak, so AI navigation survives an editor restart and works in the
    launcher. general.save_level alone does NOT write the navmesh.

    Same logic as the export script of this repository: ai.regenerate_ignored first (applies buffered navmesh changes),
    then the export runs on a thread while the open dialogs are polled; with answer_dialogs=true each is answered with
    its DEFAULT button ("Navigation System Warning": 'still processing' defaults to Cancel so stale navigation data is
    not exported, 'pending changes' defaults to Yes = apply them) and the export is retried (max_attempts) while the
    navigation is still processing. answer_dialogs=false only reports the dialogs (answer them with click_dialog_button).
    Returns {ok, attempts, dialogs_seen, answered, output}. Save the level first (save_level) if there are unsaved changes.
    """
    if not 10 <= wait_seconds <= 900 or not 1 <= max_attempts <= 10:
        return _refusal("wait_seconds is 10 to 900 and max_attempts 1 to 10")
    return server_support.export_logic(_EditorIO(), wait_seconds, answer_dialogs, max_attempts)


@mcp.tool()
def inspect_level(level_dir: str = "", nav_points: bool = False) -> str:
    """Offline inventory of a level from its layer files and level.pak: object
    types per layer, AI characters (archetype, faction, behavior tree, equipment),
    navigation areas (inclusion/exclusion, floor, height, bbox), seed points,
    cover surfaces, smart-object classes, and whether a navmesh was ever
    exported. No object cap, works on any project -- use it to study a finished
    level (e.g. GameSDK woodland) before building one like it.
    level_dir defaults to the level currently open in Sandbox.
    """
    if not level_dir:
        # get_current_level_path() is project-relative ("gamesdk/levels/singleplayer/woodland").
        level_dir = _python_json(
            "import os\n"
            "print('@@JSON ' + json.dumps(os.path.join(os.getcwd(), general.get_current_level_path())))"
        )
    return level_inventory.markdown(level_inventory.inventory(level_dir, nav_points))


import level_study  # noqa: E402

_COLLISION_PROXY_MTL = """<Material MtlFlags="532512" Shader="Illum" GenMask="1" SurfaceType="mat_nodraw" MatTemplate="" Diffuse="1,0,0" Emissive="0,0,0" Opacity="0.05">
 <PublicParams IndirectColor="0.25,0.25,0.25"/>
</Material>
"""


@mcp.tool()
def study_level(section: str = "overview", level_dir: str = "", max_chars: int = 60000) -> str:
    """Explain how a finished level is built, one system at a time (offline, reads
    layers/*.lyr, leveldata/ and level.pak; no object cap). Sections:
      overview   - size, mission lighting, minimap extent, layer tree + DefaultLoaded flags
      streaming  - AreaShape -> AreaTrigger -> Engine:LayerSwitch wiring (what loads where)
      audio      - AudioAreaAmbience + linked shapes, AudioTriggerSpots, switches/RTPCs
      borders    - invisible collision walls, occluder meshes, kill zones, playable extent
      terrain    - surface types + terrain materials, vegetation, roads, water, decals
      lighting   - time of day + preset values, env probes (root/child), lights, helpers
      cinematics - TrackView sequences (cameras, faders, events), how they are started
      logic      - every flowgraph with node classes and referenced entities
    level_dir defaults to the level open in Sandbox.
    """
    if not level_dir:
        level_dir = _python_json(
            "import os\n"
            "print('@@JSON ' + json.dumps(os.path.join(os.getcwd(), general.get_current_level_path())))"
        )
    if section not in level_study.SECTIONS:
        raise RuntimeError("unknown section '%s'; choose from %s" % (section, ", ".join(level_study.SECTIONS)))
    text = level_study.to_text(level_study.study(level_dir, [section])[section])
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated, %d chars]" % len(text)


@mcp.tool()
def create_collision_wall(x: float, y: float, z: float, length: float, height: float, thickness: float = 0.5,
                          yaw_deg: float = 0.0, name: str = "", material: str = "materials/special/collision_proxy") -> dict:
    """Place an invisible wall that blocks the player/vehicles - GameSDK woodland's
    border technique (layer Player_Collisions: 147 meshes with
    materials/special/collision_proxy_entitiesonly).

    How it works (engine source): any brush whose MATERIAL has MTL_FLAG_COLLISION_PROXY
    (0x2000; GameSDK's MtlFlags="532512") gets ERF_COLLISION_PROXY automatically
    (CBrush::SetMaterial). Such nodes are skipped by rendering, shadows and GI but keep
    physics. So the wall is just a scaled primitive_cube with that material; (x, y, z) is
    the centre of the wall's base.

    If `material` does not exist in the project it is created as
    <assets>/<material>.mtl with MtlFlags 532512, surface type mat_nodraw (a red
    5%-opaque box that only shows in the editor). Put walls on their own layer
    (e.g. "Player_Collisions", DefaultLoaded) in a real level.
    """
    material = material.replace(chr(92), "/")
    if material.lower().endswith(".mtl"):
        material = material[:-4]
    if os.path.isabs(material) or ".." in material.split("/") or ":" in material or not material.strip("/"):
        raise RuntimeError("material must be a relative asset path without '..' or an extension, e.g. materials/special/collision_proxy")
    assets = _asset_root()
    created_material = False
    if crypak.read_asset(assets, material + ".mtl") is None:
        path = os.path.join(assets, *material.split("/")) + ".mtl"
        if os.path.commonpath([os.path.abspath(path), os.path.abspath(assets)]) != os.path.abspath(assets):
            raise RuntimeError("material path escapes the project's asset folder: %s" % material)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_COLLISION_PROXY_MTL)
        created_material = True
    res = _python_json(
        "import math\n"
        "n = general.new_object('Brush', 'objects/default/primitive_cube.cgf', %r, %f, %f, %f)\n"
        "if not n:\n"
        "    raise RuntimeError('could not create the wall brush')\n"
        "try:\n"
        "    object.set_scale(n, %f, %f, %f)\n"
        "    object.set_rotation(n, 0.0, 0.0, %f)\n"
        "    object.set_custom_material(n, %r)\n"
        "except BaseException:\n"
        "    object.delete(n)\n"
        "    raise\n"
        "print('@@JSON ' + json.dumps({'name': n, 'pos': list(object.get_position(n)), 'scale': list(object.get_scale(n)), "
        "'rotation': list(object.get_rotation(n)), 'material': object.get_custom_material(n)}))"
        # primitive_cube is 1 m, centred on its origin: lift by half the height.
        % (name, x, y, z + height / 2.0, length, thickness, height, yaw_deg, material)
    )
    res["created_material_file"] = created_material
    return res


import character_collider  # noqa: E402
import gamedata_study  # noqa: E402
import project_inventory  # noqa: E402
import xml_outline  # noqa: E402


def _cap(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated, %d chars]" % len(text)


@mcp.tool()
def study_gamedata(section: str = "items", asset_dir: str = "", max_chars: int = 60000) -> str:
    """Explain a project's DATA libraries (the counterpart of study_level). Sections:
      items       weapons/ammo/accessories: class, category, fire modes (rate, damage, clip),
                  zoom modes, ammo physics (speed, mass, pierceability), equipment packs
      vehicles    movement type (StdWheeled/ArcadeWheeled/Tank/Helicopter/StdBoat), seats and
                  views, parts, components with damageMax, actions, mannequin
      mfx         MaterialEffects.xml matrix (effect row x surface column -> FXLib:effect)
      surfaces    SurfaceTypes.xml physics per surface (friction, pierceability, breakable)
      archetypes  prototypes and classes per Libs/EntityArchetypes library
      particles   Libs/Particles libraries and .pfx files
      ui          UI elements (gfx) and UI action flowgraphs
      gamerules   GameModes.xml + module classes per mode
      input       action maps and key bindings
      difficulty  Difficulty/*.cfg cvars side by side
      prefabs, locale, entities (every Lua entity class),
      entityprops (default editor Properties of every Lua entity class - large; prefer
                   entity_class_properties for a few classes)
    asset_dir defaults to the loaded project's asset folder.
    """
    root = asset_dir or _asset_root()
    if section not in gamedata_study.SECTIONS:
        raise RuntimeError("unknown section '%s'; choose from %s" % (section, ", ".join(gamedata_study.SECTIONS)))
    data = gamedata_study.study(root, [section])[section]
    text = level_study.to_text(json.loads(json.dumps(data, default=lambda o: dict(o) if hasattr(o, "items") else str(o))))
    return _cap(text, max_chars)


@mcp.tool()
def entity_class_properties(classes: list, asset_dir: str = "") -> dict:
    """Default editor Properties of Lua entity classes (parsed from their Scripts/Entities/*.lua
    Properties table), e.g. ["Door", "RigidBodyEx", "HealthPack", "AreaTrigger"]. Use it to
    know which properties exist before setting them with general.set_entity_property.
    Looks in the project's own Assets first and then in the GameSDK sample's paks (where the stock classes
    live); `asset_dir` forces one folder. A class found nowhere is listed under "_not_found" with a hint,
    never silently dropped. C++ entity components are not Lua classes: schematyc_search / component_properties.
    """
    wanted = list(dict.fromkeys(classes))
    roots = [asset_dir] if asset_dir else [r for r in (_asset_root(), _gamesdk_assets()) if r]
    found = {}
    sources = {}
    for root in roots:
        todo = set(wanted) - set(found)
        if not todo:
            break
        for cls, info in gamedata_study.s_entityprops(gamedata_study.Assets(root), todo).items():
            found[cls] = info
            sources[cls] = root
    out = {c: found[c] for c in wanted if c in found}
    missing = [c for c in wanted if c not in found]
    if missing:
        out["_not_found"] = missing
        out["_hint"] = ("no Scripts/Entities/**/<Class>.lua with that class name in %s. Class names are the Lua file's table name (Door, RigidBodyEx, HealthPack, AreaTrigger); a C++ component is not a Lua class "
                        "(schematyc_search finds it). project_overview(list_regex='Scripts/Entities/.*') lists the scripts." % " or ".join(roots))
    if sources and set(sources.values()) != {roots[0]}:
        out["_sources"] = {c: sources[c] for c in out if c in sources}
    return out


@mcp.tool()
def project_overview(asset_dir: str = "", list_regex: str = "", max_chars: int = 40000) -> str:
    """Map everything a project ships: each pak (size, file count, top folders), content areas
    (meshes, textures, levels, audio, UI, particles, scripts...) with where they live, and the
    loose folders. With list_regex, list every file (packed or loose) whose path matches.
    """
    root = asset_dir or _asset_root()
    if list_regex:
        rows = project_inventory.list_matching(root, list_regex)
        return _cap("\n".join("%10d  %s  [%s]" % (size, name, src) for name, size, src in rows), max_chars)
    return _cap(project_inventory.markdown(project_inventory.inventory(root)), max_chars)


@mcp.tool()
def outline_asset_xml(path: str, depth: int = 5, names_only: bool = False, asset_dir: str = "") -> str:
    """Outline an XML asset (loose or packed) so an unfamiliar format can be understood without
    dumping it: every tag path with count and first attributes. names_only=True lists the name
    attributes of the main repeated element instead (e.g. all effects in a particle library).
    Example: outline_asset_xml("Scripts/Entities/Vehicles/Implementations/Xml/HMMWV.xml", 4)
    """
    root = xml_outline.load(asset_dir or _asset_root(), path)
    if names_only:
        tag, vals = xml_outline.names(root)
        return "<%s> x%d\n%s" % (tag, len(vals), "\n".join(vals))
    return "\n".join(xml_outline.outline(root, depth, 6))


@mcp.tool()
def get_entity_physics(name: str) -> dict:
    """Live physical state of one entity (needs the ClaudeBridge plugin build with
    bridge.entities.physics). For a character (PE_LIVING) it returns the collider
    (sizeCollider, heightCollider, capsule, colliderBottom/Top, stepUpHeight), dynamics
    (kInertia, kInertiaAccel, kAirControl, gravity, mass, slide/climb/jump/fall angles in
    DEGREES, maxVelGround, bActive), and the live contact state (bFlying, vel,
    velRequested, groundSlope, groundHeight, bStuck, bSquashed), plus collision class
    type/ignore bits and parts. Works in edit mode; the player only has meaningful
    living state in game mode (game.toggle_game_mode). GameSDK's player entity is named
    "Dude" in the editor.
    """
    return _send_structured("bridge.entities.physics " + name)


@mcp.tool()
def component_properties(entity: str, component: str, values: dict = None, persist: bool = False) -> dict:
    """Read, and optionally set, the properties of one component on an entity -- the
    values the Inspector shows under a component (needs the ClaudeBridge plugin build
    with bridge.component.properties).

    `component` is the component class name, case-insensitive ("CNpcComponent",
    "CCharacterControllerComponent"); a label only works if it has no spaces, because
    the bridge splits commands on whitespace. Append "#N" for the N-th instance of that
    type. `values` is a (possibly nested) dict of member names as serialized in the
    layer file; members not given keep their value. Examples:
      component_properties("Villager1", "CNpcComponent", {"PatrolPath": "VillagerRoute"})
      component_properties("Villager1", "CCharacterControllerComponent",
                           {"Movement": {"Inertia": 10, "InertiaAcc": 11}})
    Without `values` it returns every property. A change sends
    ENTITY_EVENT_COMPONENT_PROPERTY_CHANGED (a CCharacterControllerComponent
    re-physicalizes on it, so physics changes apply live, even in game mode) and flags
    the level modified. general.set_entity_param cannot do this: it only reaches
    legacy Lua entity properties.

    persist=true (needs `values`): after the change the level is SAVED and the layer file is read back; the answer carries persist: {saved, ok, checked,
    missing, mismatched, where} -- a value that is live but did not reach the file is listed, so "I set it" is no longer all you know (T-15).
    """
    if persist and not values:
        raise RuntimeError("persist=true needs values: there is nothing to save")
    if " " in entity or " " in component:
        raise RuntimeError("entity and component names cannot contain spaces (the bridge splits on "
                           "whitespace); use the component class name, e.g. CCharacterControllerComponent")
    cmd = "bridge.component.properties %s %s" % (entity, component)
    if not values:
        return _send_structured(cmd)

    # Never send a partial object. Loading JSON through the engine's archive resets
    # string members that the JSON does not mention (measured: writing only
    # {"PatrolPath": ...} blanked the villager's AgentType), so read every property,
    # merge the requested values in, and write the complete set back.
    current = _send_structured(cmd).get("properties")
    if not isinstance(current, dict) or not current:
        raise RuntimeError("component %s on %s returned no properties; refusing to write a partial object" % (component, entity))

    def merge(base, patch):
        out = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
        return out

    result = _send_structured(cmd + " " + json.dumps(merge(current, values), separators=(",", ":")))
    if persist:
        import entity_check
        import world_build
        saved = world_build.save_level()
        level = world_build.current_level()["name"]
        ver = server_support.verify_persisted(entity_check.read_layers(level), entity, component, values)
        result = dict(result, persisted=bool(saved.get("saved")) and ver["ok"], persist=dict(ver, saved=saved.get("saved"), level=level))
    return result


@mcp.tool()
def sample_entity_physics(name: str, samples: int = 20, interval_s: float = 0.1) -> dict:
    """Poll get_entity_physics repeatedly and summarise it: position/velocity per sample,
    how often it was flying or stuck, speed min/max, height changes. Use it to turn
    "the character stutters / slides / gets stuck" into numbers. Keep samples*interval
    short (a few seconds); each sample is a round trip through the editor main thread.
    """
    rows = []
    for i in range(max(1, min(samples, 200))):
        t0 = time.time()
        try:
            d = _send_structured("bridge.entities.physics " + name)
        except Exception as e:  # noqa: BLE001
            rows.append({"i": i, "error": str(e)})
            break
        liv = d.get("living") or {}
        vel = liv.get("vel") or d.get("velocity") or [0, 0, 0]
        rows.append({"i": i, "t": round(t0, 3), "pos": d.get("physPos") or d.get("entityPos"),
                     "vel": vel, "speed2d": round(((vel[0] or 0) ** 2 + (vel[1] or 0) ** 2) ** 0.5, 3),
                     "velRequested": liv.get("velRequested"), "flying": liv.get("bFlying"),
                     "stuck": liv.get("bStuck"), "groundSlope": liv.get("groundSlope")})
        time.sleep(max(0.0, interval_s - (time.time() - t0)))
    ok = [r for r in rows if "error" not in r]
    summary = {}
    if ok:
        sp = [r["speed2d"] for r in ok]
        zs = [r["pos"][2] for r in ok if r.get("pos")]
        summary = {"samples": len(ok), "speed2d_min": min(sp), "speed2d_max": max(sp),
                   "flying_samples": sum(1 for r in ok if r["flying"]), "stuck_samples": sum(1 for r in ok if r["stuck"]),
                   "z_min": min(zs) if zs else None, "z_max": max(zs) if zs else None}
    return {"summary": summary, "rows": rows}


@mcp.tool()
def plan_character_collider(mode: str = "design", total_height: float = 1.9, radius: float = 0.4,
                            step: float = 0.45, radius_param: float = 0.0, height_param: float = 0.0,
                            center: float = 0.0, capsule: bool = True) -> dict:
    """Capsule/cylinder maths for a living character (see character_collider.py).
    mode="design": give total_height (pivot to top), radius and the step-up height you want;
      returns pe_player_dimensions values AND CCharacterControllerComponent Radius/Height/
      transform z. GameSDK's standing player: radius 0.41, cylinder half-height 0.3, centre
      1.25 -> bottom 0.54 m above the feet, top 1.96 m, step-up 0.54 m.
    mode="component": check existing component settings (radius_param, height_param, center).
    The step-up height is heightCollider - size.z - radius; a collider touching the ground
    gets 0 and catches on every bump (livingentity.cpp).
    """
    if mode == "component":
        return character_collider.from_component(radius_param, height_param, center, capsule)
    return character_collider.design(total_height, radius, step, capsule)


@mcp.tool()
def read_asset_file(path: str, max_chars: int = 20000) -> str:
    """Read a text asset of the loaded project by game path, loose or packed,
    e.g. "Scripts/AI/Navigation.xml", "Scripts/AI/Factions.xml",
    "Scripts/AI/BehaviorTrees/SDK_Grunt_03.xml", "Libs/EquipmentPacks/AI_Rifle.xml".
    """
    data = crypak.read_asset(_asset_root(), path)
    if data is None:
        raise RuntimeError("not found (loose or in any .pak under %s): %s" % (_asset_root(), path))
    text = data.decode("utf-8", "replace")
    return text if len(text) <= max_chars else text[:max_chars] + "\n... [truncated, %d chars total]" % len(text)


@mcp.tool()
def query_knowledge(query: str, top: int = 4, category: str = "", include_superseded: bool = False) -> list:
    """Search this project's knowledge base (rag/cryengine_project_rag.jsonl):
    traps, silent failures, engine internals, how GameSDK's AI is built, and
    what has already been tried. Query it BEFORE running an unfamiliar editor
    command or concluding something is broken. Categories: trap, tooling, playbook,
    fact, howto, ai, project, level-craft, engine-reference, gameplay, navigation,
    animation, build-package (old names are still accepted).
    """
    import rag_query
    chunks = rag_query.load(rag_query.DEFAULT_CORPUS)
    hits = rag_query.search(chunks, query, max(1, min(int(top), 20)), category or None, include_superseded)
    # status / verified_in say how far to trust a chunk: 'verified_in: none' = never seen working in the running game; superseded chunks are hidden unless asked for
    return [{"id": c["id"], "title": c["title"], "category": c["category"], "score": round(s, 2),
             "status": c.get("status", ""), "verified_in": c.get("verified_in", ""), "date": c.get("date", ""), "superseded_by": c.get("superseded_by", ""),
             "verified_note": c.get("verified_note", ""), "content": c["content"]} for s, c in hits]


# ---------------------------------------------------------------------------
# Tools that need NO running editor: lint, jobs, "see the game",
# and the asset pipeline. They work on the project folder and the engine's own
# tools (rc.exe, Crytek's packager), so a session can use them with Sandbox closed.
#
# Rules they keep: nothing here downloads anything (a tool that would need a file
# says so and the user is asked first, naming file, source and size); no arbitrary
# command is run (jobs are a whitelist of this folder's scripts with validated
# arguments); a file that exists is never replaced without overwrite=true; whatever
# a tool writes is linted before it answers.
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

from mcp.server.fastmcp import Image  # noqa: E402

import asset_lint  # noqa: E402
import audio_report as audio_mod  # noqa: E402
import health  # noqa: E402
import jobs as jobs_mod  # noqa: E402
import objects as objects_mod  # noqa: E402
import pipeline  # noqa: E402
import shot_compare  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402


def _refusal(exc) -> dict:
    return {"ok": False, "refused": str(exc)}


@mcp.tool()
def lint_assets(scope: list[str] | None = None, max_findings: int = 60) -> dict:
    """Check the project's loose assets for the mistakes that have cost this project days, in about a second, without the editor.
    scope: any of meshes, materials, textures, audio, level, xml, shaders, animations (default all). Newer codes: L013 (info) meshes of 1,000+ triangles with no LOD, L014 a layer places a mesh whose path contains '_lod' (the engine refuses it), L015 a Mannequin .adb names an animation that does not exist, L016 an .adb fragment its FragDef does not declare, L017 a material's SurfaceType that Libs/MaterialEffects/SurfaceTypes.xml does not define (the engine falls back to mat_default: mat_flesh on 36 materials, mat_rock on 10, mat_cloth, mat_glass, mat_dirt).
    Findings carry a code: L001 a .cgf names a material that does not exist (renders flat grey), L002 a mesh uses a sub-material index the .mtl
    lacks, L003 a .mtl names a missing texture, L004 an XML does not parse (a '--' inside a comment: RC skips it and the packager ships a STALE copy),
    L005 parallax/offset-bump flags without a Heightmap slot (or the reverse), L006 a texture in the wrong format (_displ must be BC4, _ddna BC5) or
    not a power of two, L007 a .wav/.mp3 under audio/ (never packaged: use .ogg), L008 an audio event naming a missing sample, L010 a level layer
    naming a missing mesh/material, L011 the shader cache is older than a material or texture (regenerate it, then run the shader tour).
    Returns {ok, errors, warnings, checked: {what: how many}, findings: [{severity, code, path, message, fix}]}. A clean result lists how many files
    and references it actually examined: a zero with nothing examined means nothing.
    """
    return asset_lint.lint(scope, max_findings)


@mcp.tool()
def job_kinds() -> list:
    """The kinds of long job start_job can run (tests, package, build_game, shader_cache, shoot, visual, cvar_probe, ci, editor_suite, voices, music, textures, selftest), each with its parameters and
    whether it closes the editor / launches the game / needs the machine to itself. Jobs are a fixed whitelist of this project's scripts."""
    return jobs_mod.kinds()


@mcp.tool()
def test_plan() -> list:
    """What the `tests` job would run right now and why: for each test group (smoke shaders menus audio items nights world perf), whether it is stale
    (its files changed since its last green run) or unchanged. Reads files only, changes nothing, takes a few seconds."""
    try:
        return jobs_mod.plan()
    except jobs_mod.JobError as exc:
        return [str(exc)]


@mcp.tool()
def start_job(kind: str, params: dict | None = None, allow_editor_close: bool = False) -> dict:
    """Start a long-running job detached from this server (it survives a tool timeout and a restart) and return its id at once; read it with job_status
    and read_job_log. Use this for anything that takes minutes: the test groups (~30 min for all), packaging, the shader-cache refresh (10-20 min),
    voice and music rendering. See job_kinds for the kinds and their params.
    SAFETY: not a command runner. A job is a script of this repository with arguments validated here. Jobs that package or launch the game
    (tests, package, shader_cache, shoot, visual, cvar_probe) CLOSE THE EDITOR (a graceful save-and-close) and refuse while Sandbox.exe is running unless
    allow_editor_close=true: tell the user before passing it. Only one such job runs at a time. Nothing downloads anything.
    """
    try:
        return jobs_mod.start(kind, params, allow_editor_close)
    except jobs_mod.JobError as exc:
        return _refusal(exc)


@mcp.tool()
def job_status(job_id: str) -> dict:
    """State of a job (running | done | failed | stopped | lost), seconds elapsed, exit code, a verdict read from its OUTPUT (a test run exits 0 even when
    tests fail: the SUMMARY line is what counts), and the last lines of its log."""
    try:
        return jobs_mod.status(job_id)
    except jobs_mod.JobError as exc:
        return _refusal(exc)


@mcp.tool()
def read_job_log(job_id: str, tail: int = 60, contains: str = "") -> dict:
    """The last `tail` lines (at most 400) of a job's output, optionally only the lines containing `contains` (case-insensitive), e.g. 'FAIL'."""
    try:
        return {"job": job_id, "lines": jobs_mod.log(job_id, tail, contains)}
    except jobs_mod.JobError as exc:
        return _refusal(exc)


@mcp.tool()
def list_jobs(limit: int = 15) -> list:
    """The most recent jobs, newest first, with state and elapsed seconds."""
    return jobs_mod.list_jobs(limit)


@mcp.tool()
def stop_job(job_id: str) -> dict:
    """Stop a running job (its whole process tree, and the game it launched). A job that had closed the editor leaves it closed: the watchdog reopens it."""
    try:
        return jobs_mod.stop(job_id)
    except jobs_mod.JobError as exc:
        return _refusal(exc)


def _as_image(path: str, width: int = 960):
    """A picture file as an MCP image: a JPEG scaled with ffmpeg when it is on PATH (a 1280x720 PNG is ~1 MB of context), else the file as it is."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        jpg = os.path.splitext(path)[0] + "_s.jpg"
        r = jobs_mod.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vf", "scale=%d:-2" % max(320, min(int(width), 1920)), "-q:v", "4", jpg], capture_output=True)
        if r.returncode == 0 and os.path.isfile(jpg):
            return Image(data=open(jpg, "rb").read(), format="jpeg")
    return Image(data=open(path, "rb").read(), format="png")


def _pictures(job_id: str, names: list | None, max_width: int) -> list:
    """The shoot job's pictures as MCP images (JPEG, scaled with ffmpeg when it is on PATH; the raw PNG otherwise), each preceded by its label."""
    paths = jobs_mod.shots(job_id)
    if names:
        paths = [p for p in paths if os.path.splitext(os.path.basename(p))[0] in names]
    out, ffmpeg = [], shutil.which("ffmpeg")
    for p in paths[:8]:
        label = os.path.splitext(os.path.basename(p))[0]
        data, fmt = None, "png"
        if ffmpeg:
            jpg = p[:-4] + "_s.jpg"
            r = jobs_mod.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", p, "-vf", "scale=%d:-2" % max(320, min(int(max_width), 1920)), "-q:v", "4", jpg],
                               capture_output=True)
            if r.returncode == 0 and os.path.isfile(jpg):
                data, fmt = open(jpg, "rb").read(), "jpeg"
        if data is None:
            data = open(p, "rb").read()
        out += [label, Image(data=data, format=fmt)]
    if len(paths) > 8:
        out.append("(%d more pictures not shown: get_shots(job_id, names=[...]))" % (len(paths) - 8))
    return out


def _stale_note(build: str, freshness: dict) -> str:
    """The sentence shoot_game puts in front of its pictures when the package is older than the project: nothing, a note (only the level was re-saved), or a warning."""
    if freshness.get("stale") and freshness.get("only_level_resaved"):
        return "Note: %s. " % freshness["reasons"][0]
    if freshness.get("stale"):
        return ("WARNING: the %s package is STALE, so these pictures show the old game, not the project as it is now: %s. Run start_job('package') first. "
                % (build, "; ".join(freshness["reasons"])))
    return ""


@mcp.tool()
async def shoot_game(shots: list[str], build: str = "release", time_of_day: float = 12.0, allow_editor_close: bool = False,
                     wait_seconds: int = 180, max_width: int = 960) -> list:
    """SEE the game: launch the packaged build, place the player at each spot, and return the pictures (the game window's own pixels, 1280x720, UI included).
    Each shot is a line: 'label x y yaw [z]' (yaw 0 looks north +y, 90 west, -90 east, 180 south; z is the feet, default terrain+0.1; the camera is 1.6 m
    up), 'label house:<Name>:<S|N|W|E>:<metres>' (that side of a house, looking at it), '!console command' (run it, wait 3 s), '~console command' (no wait),
    '=label' (a picture where the player stands now). Labels are letters, digits, _ and -. Up to 60 lines; at most 8 pictures come back (get_shots fetches
    others). time_of_day is an hour (12 = noon, 0 = midnight).
    It uses the PACKAGED build (run the `package` job after changing assets or code). It closes the editor (graceful save-and-close) and launches the
    game, so it refuses while Sandbox.exe runs unless allow_editor_close=true: tell the user first. If it has not finished in wait_seconds it returns the job
    id: call get_shots(job_id) later. Pictures are also kept on disk under job_runs/<id>_shots/.
    """
    try:
        freshness = await asyncio.to_thread(health.package_freshness, build) if build in ("release", "profile") else {"stale": False, "reasons": []}
        started = jobs_mod.start("shoot", {"build": build, "shots": shots, "time_of_day": time_of_day}, allow_editor_close)
    except jobs_mod.JobError as exc:
        return [json.dumps(_refusal(exc))]
    stale_note = _stale_note(build, freshness)
    job_id = started["id"]
    deadline = time.time() + max(10, min(int(wait_seconds), 900))
    st = jobs_mod.status(job_id, 6)
    while st["state"] == "running" and time.time() < deadline:
        await asyncio.sleep(5)
        st = jobs_mod.status(job_id, 6)
    if st["state"] == "running":
        return [stale_note + "Still running after %d s. job_id=%s: call get_shots(job_id) when job_status says done. So far: %s" % (wait_seconds, job_id, st.get("verdict", ""))]
    head = stale_note + "job %s %s (%s)" % (job_id, st["state"], st.get("verdict", ""))
    pics = await asyncio.to_thread(_pictures, job_id, None, max_width)
    return [head + ("" if pics else ". No pictures: log tail: " + " | ".join(st.get("log_tail", [])[-4:]))] + pics


@mcp.tool()
async def get_shots(job_id: str, names: list[str] | None = None, max_width: int = 960) -> list:
    """The pictures of a finished shoot job (shoot_game's job_id), optionally only the named labels."""
    try:
        st = jobs_mod.status(job_id, 6)
    except jobs_mod.JobError as exc:
        return [json.dumps(_refusal(exc))]
    if st["state"] == "running":
        return ["still running (%s s): %s" % (st["elapsed_s"], st.get("verdict", ""))]
    return ["job %s %s (%s)" % (job_id, st["state"], st.get("verdict", ""))] + await asyncio.to_thread(_pictures, job_id, names, max_width)


@mcp.tool()
def texture_recipes() -> dict:
    """The procedural surface recipes make_texture can produce: bark, plaster, timber, thatch, stone, slate, planks, cobble, dirtroad, cloth, each with the
    metres one texture tile should cover and whether it also gets a height map (for offset bump / parallax occlusion)."""
    return pipeline.texture_recipes()


@mcp.tool()
async def make_texture(surface: str, name: str, size: int = 512, folder: str = "textures/generated", overwrite: bool = False) -> dict:
    """Make a tileable texture set (diffuse BC1, normal+gloss BC5, and for most recipes a height map BC4) from a procedural recipe and compile it with the
    ENGINE'S OWN rc.exe into Assets/<folder>/<name>_diff|_ddna|_displ.dds. surface: see texture_recipes. folder must be under textures/. Takes 10-60 s.
    The height map's BC4 format is verified (RC picks BC3 for any TIFF with alpha unless the preset is inside the file; this handles that).
    Returns the files with their real formats, the lint result, and the 'material_texture' to pass to write_material."""
    try:
        return await asyncio.to_thread(pipeline.make_texture, surface, name, size, folder, overwrite)
    except pipeline.PipelineError as exc:
        return _refusal(exc)


@mcp.tool()
async def write_material(path: str, submaterials: list[dict], overwrite: bool = False) -> dict:
    """Write an Illum multi-material .mtl at Assets/<path>.mtl ('materials/<folder>/<name>'). submaterials, IN INDEX ORDER (a mesh stores the index: append,
    never reorder): [{name, texture?, tint?, shininess?, surface_type?, height_depth?, tiles?, glass?, metal?}].
      texture: 'textures/<folder>/<name>' as make_texture returned it (diffuse + normal, and the height map when height_depth is given).
      height_depth: 0.01-0.05 turns on offset bump mapping (Low/Medium spec) AND parallax occlusion (High spec): GenMask 88020081 and a Heightmap slot.
      glass: true makes it nearly transparent (opacity 0.08), so you can see through a window.
      tint 'r,g,b'; with no texture the material is a flat colour. surface_type e.g. mat_wood, mat_rock, mat_glass (the types SurfaceTypes.xml defines: mat_soil, not mat_dirt).
      metal: true follows the manual's PBS rules (pbs_check S005): Diffuse near-black and the tint is the SPECULAR colour in linear (iron '0.55,0.54,0.52', gold '1.0,0.71,0.28').
    The result is linted (textures exist, flags agree with the Heightmap slot) and says whether the shader cache is affected."""
    try:
        return await asyncio.to_thread(pipeline.write_material, path, submaterials, overwrite)
    except pipeline.PipelineError as exc:
        return _refusal(exc)


@mcp.tool()
async def build_mesh(spec: dict, overwrite: bool = False) -> dict:
    """Build a static mesh (.cgf) from a description, through a binary FBX and the engine's own rc.exe, with metric UVs so a texture keeps its scale.
    spec = {name, folder? (default objects/generated), material_file: 'materials/<folder>/<name>' (the .mtl the mesh names), materials: [names in the .mtl's
    sub-material order], tiles?: {material: metres one texture tile covers}, parts: [...]}.
    Each part has a 'material' and exactly one shape, in metres (x right, y forward, z up):
      {box: [x0,y0,z0,x1,y1,z1], faces?: '-x +x -y +y -z +z'}   {bar: {a, b, half_width, half_thickness, plane_normal?}}
      {quad: [p0,p1,p2,p3], flip?, uv?}  (counter-clockwise seen from outside)   {cylinder: {center: [x,y,z_bottom], radius, height, sides?}}
    LEVELS OF DETAIL (the engine's own mechanism, from the manual's LODs page: child nodes named $lodN_<name> under the base mesh, which RC puts into the .cgf; the
    engine has NO LOD generator, so they are authored): spec 'lods': 1-3 extra levels; a part's 'detail': N leaves that part out of LOD N and every coarser one; a cylinder
    halves its sides per level (not below 6). Each LOD should save about half the triangles. Verified in the engine: with e_LodMin 1 the detail parts are gone from the picture.
    A folder name containing '_lod' is refused by the engine ("'_lod' meshes can be loaded only internally"): never name a folder zz_lod.
    The result reads the material name BACK from the compiled .cgf, measures the compiled mesh's real size against the spec, reads the LOD nodes and their triangle
    counts back (`lods`, `lod_warnings`), checks the .mtl exists and covers every material id used, and lints. ok=false lists what is wrong (a missing .mtl draws flat grey: write_material it). Place the mesh in the level
    with the editor's own object tools (or Prefab='<game_path>' in a layer), then shoot_game to look at it."""
    try:
        return await asyncio.to_thread(pipeline.build_mesh, spec, overwrite)
    except (pipeline.PipelineError, KeyError, TypeError) as exc:
        return _refusal("bad spec: %r" % (exc,) if not isinstance(exc, pipeline.PipelineError) else exc)


@mcp.tool()
async def preview_voice(text: str, voice: str = "north", length_scale: float = 1.0) -> dict:
    """(Ashvale example pipeline: the voices alan / north / alba are Ashvale's.) Render one line with the offline neural voice (Piper) into a scratch WAV (not into Assets) so the user can hear a voice or wording (an AI agent cannot).
    voice: alan | north | alba. length_scale > 1 is slower. If Piper is not installed this says so and what installing it would download (22 MB + 63 MB per
    voice): installing is a download, so ask the user first, naming the files and sizes."""
    try:
        return await asyncio.to_thread(pipeline.preview_voice, text, voice, length_scale)
    except pipeline.PipelineError as exc:
        return _refusal(exc)


@mcp.tool()
def render_voices(sapi: bool = False) -> dict:
    """(Ashvale example pipeline: it reads the Ashvale project's libs/ashvale/dialogue.xml; it does nothing useful for another project.) Start the `voices` job: every NPC line of libs/ashvale/dialogue.xml through Piper, shaped per character, to .ogg plus the audio library
    claude_voice.xml (~20 s of work, 34 lines). Refuses with instructions if Piper is not installed (a download: ask the user first); sapi=true uses
    Windows' voices instead. Package afterwards: the game plays what is in the package."""
    return start_job("voices", {"sapi": sapi})


@mcp.tool()
def make_music(tracks: list[str] | None = None) -> dict:
    """(Ashvale example pipeline: its five tracks and library name are the Ashvale project's.) Start the `music` job: synthesise the two-minute loops (menu, day, night, danger, end; default all) to .ogg and rewrite the audio library
    claude_music.xml. Package afterwards."""
    return start_job("music", {"tracks": tracks or []})


@mcp.tool()
async def health_check() -> dict:
    """Can the working state be trusted right now? One report: is the editor up and its bridge answering with a level loaded, is each PACKAGED build older than the
    project's code and assets (shoot_game and the tests show the package, not the project), is the shader cache older than a material, is the watchdog scheduled, is a
    stray lock blocking it, what jobs are running, how much disk is free. Reads only, about a second. Each item is ok | info | warn | fail with a fix. Run it before
    believing a picture or a test, and after anything odd (nothing visible changed? the package may be stale)."""
    return await asyncio.to_thread(health.check)


@mcp.tool()
async def wait_for_editor(timeout: int = 240, require_level: bool = True) -> dict:
    """Wait until the editor is back: Sandbox's bridge answers and (by default) the level is loaded. Use it after a job that closed the editor (package, tests, shader_cache, shoot, visual, cvar_probe):
    the watchdog reopens it within 5 minutes, the level loads in ~15 s. With CLAUDE_AUTO_RECOVER=1 (off by default) this call itself may start Sandbox.exe (and replace a crashed one, taskkill /F) via bridge_watchdog.recover. Returns at once when it is already up. On a timeout it says why: what the watchdog last did, whether
    Sandbox.exe exists, whether a lock holds the watchdog off, what jobs run. timeout is 1 to 900 seconds; require_level=false returns as soon as the bridge answers."""
    if not 1 <= timeout <= 900:
        return _refusal("timeout is 1 to 900 seconds")
    marker = bridge_py.closed_on_purpose()
    if os.environ.get("CLAUDE_AUTO_RECOVER") == "1" and not marker:
        # an editor that is GONE is started now (unless a deploy or an editor-closing job holds it off), not in up to five minutes
        import bridge_watchdog
        rep = await asyncio.to_thread(bridge_watchdog.recover, min(float(timeout), 300.0))
        if rep.get("state") == "recovered":
            bridge_py.record_recovery(rep)
    out = await asyncio.to_thread(health.wait_for_editor, timeout, 5, require_level)
    if marker:
        out = dict(out, editor_closed_on_purpose=marker, note="the marker file says the editor was closed on purpose, so it was not relaunched; this only waited for it to come back")
    return out


@mcp.tool()
async def compare_shots(a: str, b: str, control: str = "", region: list[float] | None = None) -> list:
    """Compare two pictures of the game by measurement. a, b, control: '<job id>:<label>' (a shoot job's picture, see shoot_game / list_jobs) or a path under
    job_runs. CONTROL = a second capture of the UNCHANGED view that `a` shows: the game never draws the same frame twice (the night sky moved ~19 levels
    between two captures of one view, grass ~4, a stone wall under 1), so without it a difference means nothing: the verdict then says the noise floor is unknown. With it,
    each cell of a 4x4 grid is judged against its own repeat difference, and the answer says WHERE it changed (e.g. the lamp pool at a door) and how many times the noise.
    Returns the measurements (mean abs diff, changed fraction, luminance, local contrast = the stipple measure, per-cell values) and a difference picture: red = cells
    changed beyond noise, blue = differences that are within it. region [x0,y0,x1,y1] as fractions restricts everything to that part. A picture compared with itself is
    always measured and must read exactly 0 (the instrument's own control)."""
    try:
        res = await asyncio.to_thread(shot_compare.compare, a, b, control or None, region, True)
    except shot_compare.CompareError as exc:
        return [json.dumps(_refusal(exc))]
    out = [res]
    pic = res.get("difference_picture")
    if pic and os.path.isfile(pic):
        out.append(_as_image(pic, 960))
    return out


@mcp.tool()
async def audio_report(path: str, voice: bool = False, loop: bool | None = None) -> dict:
    """Measure an audio file: duration, peak/RMS (dBFS), integrated loudness (LUFS), loudness range, true peak, clipped samples, silence at the start/end/inside, the loop
    seam (jump over a typical step, and the level change across it), spectrum (centroid and band shares), stereo correlation; voice=true adds periodicity, voiced share and
    pitch (a pitch measure for synthetic voices). path: a game path under Assets ('audio/sdlmixer/assets/ash_music_day.ogg') or a preview_voice render. loop
    defaults to true for ash_music_*, amb_* and loop* files. These are measurements and flags (clips, no headroom, very quiet, a seam that may be audible); a person has to listen to decide whether it sounds good. Checked against synthetic signals with known answers (tests/test_audio_report.py)."""
    try:
        return await asyncio.to_thread(audio_mod.report, path, voice, loop)
    except audio_mod.AudioError as exc:
        return _refusal(exc)


@mcp.tool()
async def place_object(kind: str, asset: str, name: str, position: list[float], rotation_deg: list[float] | None = None, scale: list[float] | None = None,
                       material: str = "", overwrite: bool = False) -> dict:
    """Place one object in the OPEN editor's level, checked before and read back after. kind 'brush': asset is a mesh path ('objects/props/.../x.cgf' or
    '%ENGINE%/EngineAssets/Objects/primitive_cube.cgf'; it must exist, loose or in a pak); kind 'entity': asset is an entity class name. name: letters, digits, _ (starting with
    a letter). position [x,y,z] metres (z is the object's origin: a brush's pivot is not always its base: cgf_bounds says where a mesh really is). rotation_deg [rx,ry,rz],
    scale [sx,sy,sz], material a game path. An existing name is refused unless overwrite=true. Returns the object as the editor reports it and any difference from what was
    asked. The level is NOT saved (save with execute_editor_command general.save_level when the phase is done). Needs Sandbox open with the level: health_check says."""
    try:
        return await asyncio.to_thread(objects_mod.place, kind, asset, name, position, rotation_deg or [0, 0, 0], scale or [1, 1, 1], material, overwrite)
    except objects_mod.ObjectError as exc:
        return _refusal(exc)


@mcp.tool()
async def delete_objects(names: list[str] | None = None, prefix: str = "", really: int | None = None) -> dict:
    """Delete level objects, safely. Give exactly one of names (a list of exact names) or prefix (at least 3 characters: every object whose name STARTS with it). It is a DRY RUN
    until `really` equals the number of objects that match: the first call lists what would go and how many, the second passes that number. A different number deletes
    nothing. More than ~490 matches (the editor's list is capped near 500) is refused: narrow the prefix. The level is not saved. Needs Sandbox open with the level."""
    try:
        return await asyncio.to_thread(objects_mod.delete, names, prefix or None, really)
    except objects_mod.ObjectError as exc:
        return _refusal(exc)


import debug_views as dv_mod  # noqa: E402
import object_props as props_mod
import object_catalog as object_catalog_mod  # noqa: E402


@mcp.tool()
def debug_views(group: str = "") -> dict:
    """The engine's own debug views, read from the scripts it ships with the editor (Editor/Scripts/viewmodes.py, environmentmodes.py, DebugViews/debug_views.py) and checked against the console
    variables' help: render modes (r_DebugGBuffer 1-9: normals, smoothness, reflectance, albedo, lighting model, translucency, sun self-shadowing, subsurface scattering, specular validation;
    wireframe, collision shapes, vertex normals, tangents, texel density at 256/512/1024 per metre, LOD index), environment toggles (without fog, clouds, sun, sky, shadows, vegetation,
    particles, decals, terrain, water ...), engine statistics (r_stats), AI (ai_DebugDraw), art (e_DebugDraw: texture memory, materials, physics triangles, LOD). Each entry says which variable
    to set to what; `notes` flags where the engine's own script is stale (its debug_views.py names r_DebugGBuffer values wrongly: 5 'Translucency' is really 'Lighting model'; some variables it names
    do not exist). Look at one with debug_view. group: 'render', 'environment' or 'debug_views'."""
    try:
        views = dv_mod.catalog()
    except (dv_mod.DebugViewError, OSError) as exc:
        return _refusal(exc)
    rows = [{"id": v["id"], "label": v["label"], "sets": ["%s=%s" % kv for kv in v["sets"]], "cycle": ["%s=%s" % kv for kv in v["cycle"]] or None, "notes": v["notes"] or None}
            for v in views if not group or v["group"].split(".")[0] == group]
    return {"count": len(rows), "views": rows}


@mcp.tool()
async def debug_view(views: list[str], eye: list[float] | None = None, target: list[float] | None = None, width: int = 640) -> list:
    """LOOK at the level through one or more engine debug views, in the EDITOR viewport (the game is not run): for each id from debug_views ('render:Normals', 'render:Albedo', 'render:wireframe',
    'environment:e_Fog'...) the variables are set, a picture of the Perspective viewport is taken, and every variable is put back and READ BACK (`restored` must be true). A picture 'plain' with no view
    is added first as the control. eye / target: world positions [x,y,z] for the camera (default: where the editor camera is now, looking along it). Up to 6 views per call (each costs ~7 s). The
    editor accepts the cheat and const variables these use; a Release game would refuse most of them. Needs Sandbox open with the level; changes nothing in the level."""
    if not views or len(views) > 6:
        return [{"ok": False, "refused": "give 1 to 6 view ids (debug_views lists them)"}]
    out = []
    try:
        plain = await asyncio.to_thread(dv_mod.shoot, "dv_plain", eye, target)
        out.append("plain (control): " + plain)
        out.append(_as_image(plain, width))
        for v in views:
            r = await asyncio.to_thread(dv_mod.look, v, eye, target)
            out.append({"view": r["view"], "label": r["label"], "set": r["set"], "restored": r["restored"], "not_restored": r["not_restored"]})
            out.append(_as_image(r["picture"], width))
    except (dv_mod.DebugViewError, OSError, RuntimeError) as exc:
        out.append(_refusal(exc))
    return out


@mcp.tool()
async def object_types(text: str = "") -> dict:
    """The kinds of object the editor can create (the engine's own list, 36 in this build): AI (AIPath, AIShape, NavigationArea, CoverSurface...), Area (AreaBox, AreaSphere, Shape, VisArea,
    Portal, OccluderArea, WaterVolume, ClipVolume...), Audio, Brush, Components (EntityWithComponent), Designer, Misc (Camera, Comment, Decal, DistanceCloud, EnvironmentProbe, River, Road, Rope,
    SplineDistributor, ReferencePicture), Static Mesh Entity... Create one with create_object. Needs Sandbox open. With `text`: the saved CATALOG of what the engine gives a fresh object of each type
    (every property with its default and the child nodes: engine_data/object_catalog.json, made by creating and deleting one scratch object of each type), searched by words in the type, the category
    or a property name ('fog', 'height', 'sound'): works with the editor closed."""
    if text:
        try:
            rows = object_catalog_mod.search(text)
        except (OSError, RuntimeError) as exc:
            return _refusal(exc)
        return {"matches": len(rows), "results": [{"type": r["type"], "category": r["category"], "properties": r.get("attributes", {}), "child_nodes": list(r.get("children", {})), "note": r.get("skipped") or r.get("error")} for r in rows[:12]]}
    try:
        types = await asyncio.to_thread(props_mod.object_types)
    except Exception as exc:  # noqa: BLE001 - the editor is not answering
        return _refusal(exc)
    by_cat = {}
    for t in types:
        by_cat.setdefault(t["category"], []).append(t["type"])
    return {"count": len(types), "by_category": by_cat}


@mcp.tool()
async def create_object(type: str, name: str, position: list[float]) -> dict:
    """Create an editor object of ANY engine type (see object_types: River, Road, Rope, WaterVolume, Decal, AreaBox, VisArea, Portal, OccluderArea, EnvironmentProbe, SplineDistributor, ...) at a world
    position. Refuses an unknown type, a bad or taken name. Returns the object's properties as the engine's own serializer reports them (every property a layer file would hold). The level is NOT saved:
    BUT a graceful editor close (a job, a plugin deploy) saves it, so delete what you create for an experiment (delete_objects) before anything restarts the editor. Splines (Road, River) start
    with no points: spline_points sets them; object_properties sets the rest. Brushes and entities with a mesh or a class: place_object. The creation is one editor UNDO step ("Claude: create <Type> (<name>)"): Ctrl+Z removes it (editor_undo).
    VIS AREAS: the 3D engine classifies an area by a word in its name, so a VisArea must be named with 'visarea' in it (and not 'portal'), or it silently does nothing; the answer carries `warnings` when the name you gave will not
    work (see visarea_check)."""
    try:
        return await asyncio.to_thread(props_mod.create, type, name, position, "", True)
    except (props_mod.ObjectPropsError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
async def object_properties(name: str, set: dict[str, str] | None = None) -> dict:
    """Read, and optionally set, every property of one editor object, through the engine's own serializer (the same XML a layer file holds): a River's Width, StepSize, VolumeDepth, Fog*,
    Caustics*; a Road's Width, TileLength, SortPriority, EraseVegWidth; a Rope's `Rope/mass`, `Rope/radius`, `Rope/num_seg`; a Decal's ProjectionType, SortPriority; a WaterVolume's depth and fog;
    plus Pos, Rotate, Scale, Material, MinSpec, ViewDistRatio. `set`: {key: value}; a key is an attribute of the object, or 'Child/key' for an attribute of a child node. The change is one undo
    step and is read back: `not_applied` lists every key the object did not end up holding (a misspelt key is reported, not ignored). Type, Id, Layer and LayerGUID cannot be set. Not saved."""
    try:
        if not set:
            node = await asyncio.to_thread(props_mod.get_xml, name)
            return {"name": name, "type": node.get("Type"), "properties": dict(node.attrib), "children": {c.tag: (dict(c.attrib) if len(c) == 0 else "%d child nodes" % len(c)) for c in node}}
        out = await asyncio.to_thread(props_mod.checked_set, name, set)
        return {"name": name, "changed": out["changed_values"], "not_applied": out["not_applied"], "undo": "Edit > Undo ('Claude: set properties')"}
    except (props_mod.ObjectPropsError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
async def spline_points(name: str, points: list[list[float]], widths: list[float | None] | None = None) -> dict:
    """Set the points of a spline or shape object from WORLD positions [[x,y,z], ...]. SPLINES (Road, River, SplineDistributor, WaterWave ...; at least 2 points) are stored the way the editor stores them
    (local to the object, with the editor's own Bezier smoothing, so the curve is what clicking the same points would give); widths: optional per-point widths (null = the default width of the object).
    SHAPES (WaterVolume, Shape, VisArea, Portal, OccluderArea, AIShape, AIPath, NavigationArea; at least 3 points) are a plain polygon: the editor RE-CENTRES the object on the minimum corner of its points
    (measured: the object's position moves, the world points stay as asked); the answer gives the new position and the world points it now holds. A river, road or water volume lies at z: one at the terrain's height
    is hidden inside it. Returns the number of points the object holds afterwards. Not saved."""
    try:
        if await asyncio.to_thread(props_mod.is_shape, name):
            out = await asyncio.to_thread(props_mod.set_shape, name, points)
            return {"name": name, "kind": "shape", "points": len(out["world_points"]), "pos": out["pos"], "world_points": out["world_points"]}
        out = await asyncio.to_thread(props_mod.set_spline, name, points, widths)
        return {"name": name, "kind": "spline", "points": await asyncio.to_thread(props_mod.point_count, name), "applied": out.get("changed")}
    except (props_mod.ObjectPropsError, OSError, KeyError) as exc:
        return _refusal(exc)


@mcp.tool()
def add_component(entity: str, component: str, values: dict = None) -> dict:
    """Add a component to a LEVEL entity the way the Inspector's 'Add Component' does (needs the editor): `component` is a class name, GUID or label from the Schematyc environment
    (schematyc_search lists them: "Particle Emitter", "Projector Light", "SensorVolume", "Cloth", "Decal", "Rigidbody", "Point Constraint" ...). `values` (optional) is a
    (possibly nested) dict of the component's members, applied after the add through component_properties (the complete property set is merged in). The answer holds the new instance's
    GUID, the properties the component now has, and `not_applied`: every requested value the component did not end up holding. There is no undo step for an add; the level is flagged
    modified (save_level to keep it). Check the result with entity_check (N002-N004) and component_properties. A singleton the entity already has is refused by the engine."""
    if not entity or " " in entity:
        raise RuntimeError("entity must be one name without spaces (the bridge splits on whitespace)")
    added = _send_structured("bridge.entity.add_component %s %s" % (entity, component))
    out = {"entity": entity, "component": added.get("component"), "label": added.get("label"), "guid": added.get("guid"), "undo": "none for an add: delete the component in the Inspector or delete the entity"}
    cls = (added.get("component") or "").split("::")[-1]
    if not cls or " " in cls:
        out["note"] = "the component's class name has a space or is empty; read it with component_properties using the label's class from schematyc_search"
        return out
    if values:
        component_properties(entity, cls, values)
    props = _send_structured("bridge.component.properties %s %s" % (entity, cls)).get("properties") or {}
    out["properties"] = props

    def missing(want, have, path=""):
        bad = []
        for k, v in want.items():
            if isinstance(v, dict) and isinstance(have, dict) and isinstance(have.get(k), dict):
                bad += missing(v, have[k], path + k + ".")
            elif not isinstance(have, dict) or str(have.get(k)) != str(v) and not (isinstance(v, (int, float)) and isinstance(have.get(k), (int, float)) and abs(float(have.get(k)) - float(v)) < 1e-4):
                bad.append(path + k)
        return bad
    out["not_applied"] = missing(values or {}, props)
    return out


@mcp.tool()
async def link_area_target(area: str, entities: list[str], replace: bool = False) -> dict:
    """Link entities as the TARGETS of an area object (AreaBox, AreaSphere, AreaShape/Shape): the entities then receive the engine's enter/leave/fade area events when something carrying
    ENTITY_FLAG_TRIGGER_AREAS (the player, through its audio listener) crosses the area; an AreaTrigger entity turns them into flow outputs. This is the Inspector's link between an area and its
    entities (the `<Entities><Entity Id=..>` child of the area object). The Python `general.add_entity_link` is a DIFFERENT link type. `replace=false` keeps the targets the area already has.
    The answer lists the Ids the area holds afterwards and `not_applied`: names that are no entity or that the area did not keep. Not saved."""
    try:
        ids = []
        for name in entities:
            node = await asyncio.to_thread(props_mod.get_xml, name)
            if not ((node.get("Type") or "").startswith("Entity") or node.get("EntityClass")) or node.get("HasEntity") == "0":
                raise props_mod.ObjectPropsError("'%s' is a %s%s that holds no game entity: only entities (Entity, EntityWithComponent, an AreaTrigger ...) can be area targets"
                                                 % (name, node.get("Type"), " (class %s)" % node.get("EntityClass") if node.get("EntityClass") else ""))
            ids.append((name, node.get("Id")))
        area_node = await asyncio.to_thread(props_mod.get_xml, area)
        if not (area_node.get("Type") or "").startswith("Area") and area_node.get("Type") not in ("Shape", "AreaShape", "AreaBox", "AreaSphere"):
            raise props_mod.ObjectPropsError("'%s' is a %s: area targets exist on AreaBox, AreaSphere and AreaShape objects" % (area, area_node.get("Type")))
        have = [] if replace else [e.get("Id") for e in (area_node.find("Entities") or [])]
        want = list(dict.fromkeys(have + [i for _, i in ids]))
        xml = "<Entities>" + "".join('<Entity Id="%s"/>' % i for i in want) + "</Entities>"
        await asyncio.to_thread(props_mod.set_properties, area, None, {"Entities": xml})
        after = await asyncio.to_thread(props_mod.get_xml, area)
        kept = [e.get("Id") for e in (after.find("Entities") or [])]
        return {"area": area, "type": after.get("Type"), "targets": kept, "not_applied": [n for n, i in ids if i not in kept]}
    except (props_mod.ObjectPropsError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
async def flow_graph_add(entity: str, nodes: list[dict], edges: list[dict], description: str = "", group: str = "", dry_run: bool = False) -> dict:
    """Put a flow graph INTO THE LEVEL: store it as the `<FlowGraph>` child of a FlowgraphEntity object (place_object kind='entity' asset='FlowgraphEntity' first), the way the editor does
    (the GameSDK sample's gameplay layers). Before anything is written the same nodes and edges are loaded by the engine's own loader as a throw-away standalone file (flow_check F001-F008:
    unknown node class, a port the node does not have, an input the engine did not keep) and the level is only touched if that finds no error (dry_run=true: check only).
    nodes: [{"id": "1", "class": "entity:AreaTrigger", "entity": "zone_trig" (entity NAME, resolved to its GUID; for nodes that target an entity), "inputs": {"Enable": "1"}, "pos": [x, y]}];
    edges: [{"from": "1.Enter", "to": "2.input"}] ("<node id>.<port>"). flow_nodes lists node classes and ports. The answer says what the engine check found and the node/edge counts read back
    from the object (`stored` is true only when they match). Not saved (save_level); whether the graph RUNS needs the game or editor game mode."""
    import flow_graph as fg
    try:
        return await asyncio.to_thread(fg.add, entity, nodes, edges, description, group, dry_run)
    except (fg.FlowGraphError, props_mod.ObjectPropsError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
async def terrain_hole(x: float, y: float, width: float, height: float, make: bool = True) -> dict:
    """Make (make=true) or fill (false) a rectangular HOLE in the open level's terrain, in world metres (x, y = the corner with the smaller coordinates): a cellar, a cave mouth, a well shaft, a place
    where a mesh replaces the ground. The editor's own call (CHeightmap::MakeHole) through the ClaudeBridge plugin; before this only the mouse brush could. The answer is read back (`as_asked`, holes
    counted in the rectangle, `centre_is_hole`). Holes are saved with the level and exported to terrain.dat. Not saved: save_level, then export_level. In the packaged game the ground has no collision there
    (probe: claude_ground x y). Needs the plugin build that contains bridge.terrain.hole (rebuild and deploy the plugin, see docs/installation.md)."""
    import world_build as wb
    try:
        return await asyncio.to_thread(wb.terrain_hole, x, y, width, height, make)
    except wb.WorldBuildError as exc:
        return _refusal(exc)


def _open_level_folder(level: str) -> str:
    """`level` as given, or (empty) the name of the level the editor has open; raises a RuntimeError saying how to name one."""
    if level:
        return level
    import world_build
    try:
        name = world_build.current_level().get("name") or ""
    except (OSError, RuntimeError, ValueError):
        name = ""
    if not name:
        raise RuntimeError("name a level folder under Assets/levels (level=...), or open a level in the editor")
    return name


@mcp.tool()
async def gametoken_check(level: str = "") -> dict:
    """The level's game token libraries (`<level>/GameTokens/*.xml`) against the loader's own rules, offline: root tag GameTokensLibrary with a Name, each token's Name/Type (Int, Float,
    EntityId, Vec3, String, Bool: case-sensitive; anything else is silently skipped by the engine) and Value, duplicates (the first wins), and every token the level's layers (flow graph
    `gametoken_Token`) and the DRS responses (`"Token"`) name that no library defines (it reads nothing and the engine does not say so). Returns libraries, references and findings
    (G001-G010: error/warn). A level without a GameTokens folder is a fact, not a fault. level: the folder under Assets/levels; empty = the level the editor has open."""
    import gametoken_check as gc
    try:
        lvl = await asyncio.to_thread(_open_level_folder, level)
        return await asyncio.to_thread(gc.check, lvl)
    except (OSError, ValueError, RuntimeError) as exc:
        return _refusal(exc)


@mcp.tool()
async def snapshot_project(label: str = "", note: str = "", full: bool = False) -> dict:
    """Back up the active project (Assets, Code, config) into a content-addressed store in _snapshots next to the project folder (override with CRYENGINE_SNAPSHOT_DIR). Opt-in extras can add
    the plugin source, the engine repository's git diff and a Claude memory folder (private notes: do not share such a snapshot). Incremental: each distinct file content is stored once, so a snapshot after a small
    change stores only what changed. Take one BEFORE anything that deletes, overwrites or rebuilds in bulk (a prefix delete, a regenerate, a restore, a refactor); a project under git does not need it. Build output,
    CI/job runs, generated solutions, logs and editor .bak files are left out (regenerable). The store is on the SAME disk as the project: it protects against mistakes, not a failed drive (copy it elsewhere, or restore '**' to a folder on another
    drive). Nothing ever deletes a snapshot. Files the editor holds locked are listed as skipped; files that changed while read as unstable (take it again with the editor idle).
    full=true re-hashes every file (the fast path trusts size + modification time)."""
    try:
        return await asyncio.to_thread(snapshot_mod.snapshot, label, note, full)
    except snapshot_mod.SnapshotError as exc:
        return _refusal(exc)


@mcp.tool()
async def list_snapshots(limit: int = 20, verify: str = "") -> dict:
    """The snapshots, newest first (id, time, label, files, size, megabytes it added), where the store is and how big it is. verify='<id>' re-hashes every stored file that snapshot
    refers to and reports any that is missing or corrupt."""
    try:
        return await asyncio.to_thread(snapshot_mod.list_snapshots, limit, verify or None)
    except snapshot_mod.SnapshotError as exc:
        return _refusal(exc)


@mcp.tool()
async def diff_snapshot(a: str, b: str = "current") -> dict:
    """What differs between snapshot `a` (an id, or 'latest') and snapshot `b` or the live files ('current', the default): files added, removed and modified, with the first names.
    Paths are as the snapshot stores them: 'project/Code/Components/Npc.cpp', 'docs/notes.md', 'plugin/...', 'memory/...', 'engine/local_changes.patch'."""
    try:
        return await asyncio.to_thread(snapshot_mod.diff, a, b)
    except snapshot_mod.SnapshotError as exc:
        return _refusal(exc)


@mcp.tool()
async def restore_files(snapshot: str, paths: list[str], to: str = "", overwrite: bool = False) -> dict:
    """Get files back from a snapshot. paths: exact snapshot paths or globs ('project/Code/**', 'project/Assets/levels/ashvale/layers/main.lyr', '**' = everything; diff_snapshot
    prints the exact names); a pattern that matches nothing is reported. Default: into a side folder (_snapshots/restore/<id>_<time>, or `to`), the live files untouched: compare, then copy.
    overwrite=true puts them back WHERE THEY WERE and replaces what is there, but first takes a snapshot named before_restore, so the restore can itself be undone (the result says
    which). Note: overwrite=true with '**' also writes back every non-project part of the snapshot (docs, plugin source, memory notes) where it was; list the paths you mean. Each file is checked against its hash as it is written; a corrupt stored copy aborts. With the editor open, restore level files with it closed (it keeps its own copy)."""
    try:
        res = await asyncio.to_thread(snapshot_mod.restore, snapshot, paths, to or None, overwrite)
    except snapshot_mod.SnapshotError as exc:
        return _refusal(exc)
    if overwrite and jobs_mod.sandbox_running():
        res["note"] = "Sandbox.exe is running: it caches loose assets and holds its own copy of the level, so restored files may not show (and the editor may re-save the old ones) until it is restarted"
    return res


@mcp.tool()
async def visual_check(accept: bool = False, inject_fault: bool = False, calibrate: bool = False, allow_editor_close: bool = False, wait_seconds: int = 420) -> list:
    """Visual regression (the Ashvale example project's 20 fixed views and ONE global baseline; accept=true REPLACES that baseline, so snapshot_project first): does the packaged game still LOOK the way it was accepted? Captures 20 fixed views (the green, the village, the roads, the camp, the nine houses by day; chapel,
    tavern, a cottage, the smithy, the green and the camp at 22:00), each TWICE in a row, and compares each with the accepted baseline picture. The two captures of one view are the
    noise floor (sky, swaying grass, a villager walking by): a view counts as changed only where it differs from the baseline clearly more than from its own repeat. Returns the
    verdict per view (where it changed and by how many times the noise) and difference pictures of the changed ones (red = beyond noise, blue = within it).
    accept=true stores this run as the new baseline: do it once, on a package you have looked at and approve of, and again whenever a change is MEANT to alter how things look.
    inject_fault=true darkens the picture by 30 percent: the day views must be flagged (it proves the harness can fail). It closes the editor and launches the game (~3 minutes):
    needs allow_editor_close=true, so tell the user first. It shows the PACKAGE: package first (health_check says if it is stale).
    The same unchanged game differs from launch to launch (trees, the near ground, the sword's sway): the render is made as deterministic as it can be (grass off, SMAA without temporal
    filtering, no volumetric clouds, each setting verified), the HUD is masked, and what is left is LEARNED: calibrate=true adds one launch of the UNCHANGED game (same package as the
    baseline, else refused) to a per-cell tolerance. After accept, run calibrate a few times (3 or more); a cell is then flagged only if it exceeds what unchanged launches showed there.
    It sees visible regressions (a house gone grey, a texture missing, light that moved), not small tuning."""
    try:
        started = jobs_mod.start("visual", {"accept": accept, "inject_fault": inject_fault, "calibrate": calibrate}, allow_editor_close)
    except jobs_mod.JobError as exc:
        return [json.dumps(_refusal(exc))]
    job_id = started["id"]
    deadline = time.time() + max(30, min(int(wait_seconds), 900))
    st = jobs_mod.status(job_id, 8)
    while st["state"] == "running" and time.time() < deadline:
        await asyncio.sleep(5)
        st = jobs_mod.status(job_id, 8)
    if st["state"] == "running":
        return ["Still running after %d s (job_id=%s). job_status shows it; the report is job_runs/%s_shots/visual_report.json when it ends." % (wait_seconds, job_id, job_id)]
    report_path = os.path.join(jobs_mod.JOBS, job_id + "_shots", "visual_report.json")
    summary = {"job": job_id, "state": st["state"], "verdict": st.get("verdict"), "result": st.get("summary") or " | ".join(st.get("log_tail", [])[-3:])}
    pics = []
    if os.path.isfile(report_path):
        rep = json.load(open(report_path, encoding="utf-8"))
        summary["compared"] = rep["compared"]
        summary["changed"] = [{"view": v["view"], "where": [c["where"] for c in v["changed_cells"]][:3], "noise_mad": v["noise_mad"]} for v in rep["views"] if v["status"] == "changed"]
        summary["noisy_views"] = [v["view"] for v in rep["views"] if v["noisy_cells"] >= 4]
        summary["missing"] = rep["missing"]
        pics = [v["difference_picture"] for v in rep["views"] if v.get("difference_picture")][:4]
    return [summary] + [_as_image(p, 800) for p in pics if os.path.isfile(p)]


@mcp.tool()
def visual_status() -> dict:
    """The visual baseline without running anything: when it was accepted, how many views, how old the package it was taken from was, whether the current release package differs from
    it (health.package_freshness), and the last visual_check's result. No baseline yet means: run visual_check(accept=true) once."""
    manifest = os.path.join(shot_compare.BASELINE, "manifest.json")
    last = [j for j in jobs_mod.list_jobs(50) if j["kind"] == "visual"][:1]
    out = {"baseline": None, "last_visual_job": last[0] if last else None}
    if os.path.isfile(manifest):
        m = json.load(open(manifest, encoding="utf-8"))
        tol_file = os.path.join(shot_compare.BASELINE, "tolerance.json")
        runs = json.load(open(tol_file, encoding="utf-8")).get("runs") if os.path.isfile(tol_file) else 0
        out["baseline"] = {"accepted": m.get("accepted"), "views": len(m.get("views", [])), "package_built": m.get("package_built"), "folder": shot_compare.BASELINE,
                           "tolerance_learned_from_launches": runs,
                           "note": "" if runs >= 3 else "fewer than 3 calibration launches: expect false alarms; run visual_check(calibrate=true) on the unchanged package"}
    out["package_now"] = health.package_freshness("release")
    return out


# ---------------------------------------------------------------------------
# Learn the engine from the engine's own documents and source (ENGINE_LEARNING_PLAN
# Wave 0): the manual, and catalogs built by reading what the engine ships. They only
# read files; nothing here changes the engine, downloads or runs anything.
# ---------------------------------------------------------------------------
import manual as manual_mod  # noqa: E402


@mcp.tool()
def search_manual(query: str, top: int = 5, section: str = "") -> dict:
    """Search Crytek's CRYENGINE V manual from a saved offline copy (not shipped with this repository: set CRYENGINE_DOCS, see catalogs/scrape_cryengine_docs.py). A good first stop for any engine feature.
    Returns the best pages with their section, breadcrumb, url and a snippet around the match; read one with read_manual_page(page_id). query_knowledge is
    different: it holds what THIS project measured, the manual holds what Crytek says; when they disagree, the measurement wins and the difference is a finding.
    section: restrict to one folder (see manual_toc(): editor-tools, animation, ai-and-navigation, graphics-rendering, ...). top: 1-25 pages."""
    try:
        return {"query": query, "hits": manual_mod.search(query, top, section or None)}
    except manual_mod.ManualError as exc:
        return _refusal(exc)


@mcp.tool()
def read_manual_page(page: str, heading: str = "", offset: int = 0, max_chars: int = 12000) -> dict:
    """Read one manual page from the saved offline copy (set CRYENGINE_DOCS; not shipped): `page` is the numeric id from search_manual or a title (an ambiguous title answers with the ids to choose from). Long pages come in parts:
    follow next_offset. `heading` returns only the part under the first heading containing that text. The answer also lists the page's headings and the CVar-like and
    class-like names it mentions (cvars, classes), which is where to look next in the source."""
    try:
        out = manual_mod.read_page(page, heading or None, offset, max_chars)
        out["mentions"] = manual_mod.identifiers(out["text"])
        return out
    except manual_mod.ManualError as exc:
        return _refusal(exc)


@mcp.tool()
def manual_toc(section: str = "", query: str = "") -> dict:
    """The table of contents of the saved offline manual copy (set CRYENGINE_DOCS; not shipped). No argument: the sections with their page counts. section: every page in that folder in manual order (id, title, depth,
    parent). query: only pages whose title or breadcrumb contains the text (e.g. query='particle')."""
    try:
        return manual_mod.toc(section or None, query or None)
    except manual_mod.ManualError as exc:
        return _refusal(exc)


import cvar_catalog as cvar_mod  # noqa: E402


def _cvar_view(e: dict, full: bool = False) -> dict:
    out = {"name": e["name"], "kind": e["kind"], "type": e["type"], "default_in_source": e["default"], "value_in_the_editor_dump": e["current"],
           "flags": e["flags"], "settable": e["settable"], "source": e["source"], "help": e["help"] if full else e["help"].split("\n")[0][:160]}
    if full:
        out.update(source_kind=e["source_kind"], also_registered_in=e["also_in"], spec_groups=e["spec_groups"], is_spec_group=e["is_group"],
                   release_condition=e["release_condition"])
    return out


@mcp.tool()
def cvar_search(text: str = "", kind: str = "", settable_in: str = "", flag: str = "", limit: int = 20) -> dict:
    """Search the engine's console variables and commands (2,484 variables, 278 commands, built from the engine's own `DumpCommandsVars` and `audit_cvars` plus a scan of
    its source: default value, file:line, flags, help, and WHERE each can be changed). text: words that must all appear in the name or help ('shadow cascade'); kind: 'cvar'
    or 'command'; settable_in: 'editor' | 'profile' | 'release' keeps only those that can be changed in that kind of build; flag: CHEAT, CONST_CVAR, READONLY, DEV_ONLY,
    DUMPTODISK, REQUIRE_APP_RESTART, REQUIRE_LEVEL_RELOAD, DEPRECATED, RESTRICTEDMODE.
    Rule (CXConsole::OnBeforeVarChange): cheat, const, read-only and deprecated variables are refused unless the build is in dev mode (every non-Release build outside the
    editor) or it is the editor; a Release build is never in dev mode, and *_DEV_ONLY variables do not exist there. So 'settable.release' = no/absent means a cfg line or a
    console command is silently ignored in the shipped game (e_DefaultMaterial is the example). The cvar_probe job measures this on the real package."""
    try:
        rows, total = cvar_mod.search(text, kind or None, settable_in or None, flag or None, max(1, min(int(limit), 100)))
    except RuntimeError as exc:
        return _refusal(exc)
    return {"matches": total, "shown": len(rows), "results": [_cvar_view(e) for e in rows]}


@mcp.tool()
def cvar_info(name: str) -> dict:
    """Everything the catalog knows about one console variable or command: type, default (as written in the source, so it can be a symbol), the value in the editor dump
    (which differs from the default when a config, spec group or script changed it), flags, full help text, where it is registered (file:line), where else, the sys_spec groups that
    set it at each spec level, and whether it can be changed in the editor, a Profile build and a Release build (with why)."""
    try:
        e = cvar_mod.get(name)
    except RuntimeError as exc:
        return _refusal(exc)
    if not e:
        rows, _ = cvar_mod.search(name, limit=6)
        return {"ok": False, "refused": "no variable or command named %r" % name, "did_you_mean": [r["name"] for r in rows]}
    return _cvar_view(e, full=True)


@mcp.tool()
def spec_group(group: str = "", level: int = 0) -> dict:
    """The engine's quality groups (Config/CVarGroups/sys_spec_*.cfg in engine.pak). No argument: the 15 groups, their default level and the variables each one sets.
    group: one group; level (1 low, 2 medium, 3 high, 4 very high; 5-8 console profiles): the values it applies at that level ([default] overlaid by [level]). Use this before
    hand-setting a quality variable: the spec level may already do it, and a group is privileged: it can set cheat/const variables a console command cannot."""
    try:
        groups = cvar_mod.spec_groups()
    except Exception as exc:  # noqa: BLE001 - a missing engine folder or pak is reported, not raised
        return _refusal(exc)
    if not group:
        return {"groups": [{"group": g, "default_level": v["default_level"], "levels": sorted(k for k in v["sections"] if k != "default"),
                            "variables": len(v["sections"].get("default", {}))} for g, v in sorted(groups.items())]}
    match = [g for g in groups if g.lower() == group.lower()]
    if not match:
        return _refusal("no group %r: %s" % (group, ", ".join(sorted(groups))))
    if not level:
        return {"group": match[0], "default_level": groups[match[0]]["default_level"], "sections": groups[match[0]]["sections"]}
    return {"group": match[0], "level": level, "values": cvar_mod.group_values(groups, match[0], level)}


import editor_api as api_mod  # noqa: E402


@mcp.tool()
def editor_api(text: str = "", module: str = "", unused_only: bool = False, read_only: bool = False, include_modal: bool = True, limit: int = 20) -> dict:
    """Search the Sandbox editor's Python API (586 functions in 24 modules: general, object, terrain, trackview, designer, prefab, layer, material, uvmapping, ...) BEFORE
    concluding the editor cannot do something, and before writing a workaround. Each row: module.function, argument types, return type, the engine's own description (and what each
    argument means), `modal` (it opens a dialog and would HANG the bridge until a person clicks: do not call it blind), `read_only` (a getter with no argument: safe to call), how many
    times this project's scripts call it (`project_calls`: most are 0, so say so rather than assuming it is hard) and, for the getters that were swept in the open level, what
    came back (`sample`). Call functions through execute_python (one line) or the bridge scripts; a function that is not listed here is not there (the list is the dump of the running editor).
    text: words that must all appear in the name or description; module: one module; unused_only: only functions the project has never called; read_only: only safe getters.
    No arguments: the modules with their counts."""
    try:
        if not text and not module and not unused_only and not read_only:
            return {"modules": api_mod.modules(), "panes": len(api_mod.panes()), "note": "pass text= or module= to list functions; editor_panes() lists the editor's tools"}
        rows, total = api_mod.search(text, module, unused_only, read_only, include_modal, max(1, min(int(limit), 100)))
    except (OSError, ValueError) as exc:
        return _refusal(exc)
    return {"matches": total, "shown": len(rows), "results": [
        {"function": r["name"], "args": ["%s %s" % (t, n) for n, t in r["args"]], "returns": r["returns"], "description": r["description"], "arguments_mean": r["param_help"],
         "modal": r["modal"], "read_only": r["read_only"], "project_calls": r["project_calls"], "sample": r["sample"]} for r in rows]}


@mcp.tool()
def editor_panes(refresh: bool = False) -> dict:
    """The tools (panes) the editor offers: 'Track View', 'Mannequin Editor', 'Dependency Graph', 'Console Variables', 'UQS Editor', 'Test Runner', ... (57 in this build), as the editor itself reports them
    (general.get_pane_class_names). Open one with general.open_or_focus_pane (it opens a dockable pane, not a modal dialog). refresh=true asks the open editor again."""
    try:
        names = api_mod.panes(refresh)
    except Exception as exc:  # noqa: BLE001 - no editor to ask: the saved list is used if there is one
        return _refusal(exc)
    return {"count": len(names), "panes": names}


import schematyc_catalog as schem_mod  # noqa: E402


@mcp.tool()
def schematyc_search(text: str = "", type: str = "", category: str = "", path_prefix: str = "", limit: int = 20) -> dict:
    """Search the engine's Schematyc environment: every entity COMPONENT, function, signal, data type, module, action and class that the engine and this game registered (705 elements:
    83 components, 520 functions, 46 signals, 42 data types), read from the engine's own registry in the open editor (bridge verb schematyc.dump), each with the file and line it was
    registered from. Use it to find a component before writing one ('is there already a rigidbody / camera / audio / nav-agent / perception component?') and to see what its members are.
    text: words in the name, path, description or label; type: Component | Function | Signal | DataType | Module | Action | Class; category: a component's editor category
    (Physics, Game, Audio, AI, Effects, Geometry, Lights, Cameras, ...); path_prefix: e.g. 'EntitySystem/Entity' or 'Standard/Math'. No arguments: the counts and the component categories. Needs engine_data/schematyc_env.json (not shipped): run python catalogs/schematyc_catalog.py --capture once with the editor open."""
    try:
        if not (text or type or category or path_prefix):
            return schem_mod.summary()
        rows, total = schem_mod.search(text, type, category, path_prefix, max(1, min(int(limit), 100)))
    except (OSError, RuntimeError) as exc:
        return _refusal(exc)
    out = []
    for it in rows:
        row = {"type": it["type"], "name": it["name"], "path": it["path"], "guid": it["guid"], "description": it["description"][:200], "registered_in": "%s:%s" % (it["source"], it["line"])}
        if it["type"] == "Component":
            row.update(category=it["category"], label=it["component"]["label"], members=[m["name"] for m in it["component"]["members"]], flags=it["component"]["flags"])
        elif it["type"] == "Function":
            row.update(inputs=[i["name"] + ":" + i["type"].split("::")[-1] for i in it["function"]["inputs"]], outputs=[o["name"] + ":" + o["type"].split("::")[-1] for o in it["function"]["outputs"]])
        out.append(row)
    return {"matches": total, "shown": len(out), "results": out}


@mcp.tool()
def schematyc_info(name: str, type: str = "") -> dict:
    """Everything the registry holds about one Schematyc element, by exact name, label or GUID: for a component its category, flags, interactions (dependencies/incompatibilities as GUIDs), size and EVERY member
    (id, name, label, description, type); for a function all inputs and outputs with their types. An ambiguous name answers with the candidates (pass type=). The values a component HAS on an entity in the level
    are component_properties (needs an entity name); this is what the component is, before any entity has it. Needs engine_data/schematyc_env.json (not shipped): run python catalogs/schematyc_catalog.py --capture once with the editor open."""
    try:
        hit = schem_mod.get(name, type)
    except (OSError, RuntimeError) as exc:
        return _refusal(exc)
    if isinstance(hit, list):
        if not hit:
            rows, _ = schem_mod.search(name, limit=6)
            return {"ok": False, "refused": "no element named %r" % name, "did_you_mean": [r["name"] for r in rows]}
        return {"ok": False, "refused": "%d elements match %r: pass type=" % (len(hit), name), "candidates": [{"type": h["type"], "path": h["path"], "name": h["name"], "guid": h["guid"]} for h in hit[:12]]}
    return hit


import shader_catalog as shader_mod  # noqa: E402


@mcp.tool()
def shader_info(shader: str = "", flags: list[str] | None = None, include_internal: bool = False, gen_mask: str = "") -> dict:
    """What a shader accepts, read from the engine's own shader scripts (engine/shaders.pak: Shaders/<Name>.ext and HWScripts/CryFX/*.cfx): its generation FLAGS (name, label, description, the
    local mask and the common-table mask the engine wrote to globals.txt, dependencies), its public PARAMETERS (type, UI name, default, min/max, and which `%FLAG` must be on for it to exist) and
    its TEXTURE SLOTS (the Material Editor's slot, the _suffix a texture needs, the register). No `shader`: the 65 shaders with their counts. `flags`: also RESOLVE a flag set the way the engine does.
    THE RULE (Cry3DEngine/MatMan.cpp:467, measured against the engine's own table): for Eye, Glass, Hair, HumanSkin, Illum, MultiLayeredMaterials, Terrain and Vegetation the mask is computed ONLY from the
    material's StringGenMask; a name that is not in the shader's table is silently IGNORED (the project's materials carry %ALLOW_SILHOUETTE_POM and %BUMP_MAP, which do nothing) and the numeric GenMask is
    not read at all. Every other shader uses GenMask. gen_mask='8000004B' DECODES a hex GenMask from a .mtl into the flags it switches on (the woodland river: 80000013 = realtime reflection + flow + specular map). Use this before writing a material by hand; write_material already does it for Illum."""
    try:
        if not shader:
            return {"shaders": [{"name": s["name"], "flags": len(s["flags"]), "public_params": sum(1 for p in s["params"] if p["public"]), "material_textures": sum(1 for t in s["textures"] if t["material_slot"]),
                                 "common_flag_table": s["uses_common_flags"]} for s in shader_mod.load().values() if s["flags"] or any(p["public"] for p in s["params"])],
                    "texture_slots": shader_mod.texture_slots()}
        sh = shader_mod.find(shader)
        if sh is None:
            near = [n for n in shader_mod.load() if shader.lower() in n.lower()][:8]
            return {"ok": False, "refused": "no shader %r" % shader, "did_you_mean": near}
        gp = shader_mod.find_globals()
        gflags = shader_mod.parse_globals(open(gp, encoding="utf-8").read())[0] if gp else {}
        out = {"shader": sh["name"], "common_flag_table": sh["uses_common_flags"],
               "flags": [{"name": "%" + f["name"], "label": f["label"], "description": f["description"], "local_mask": "0x%X" % f["mask"],
                          "common_mask": ("0x%X" % gflags[f["name"].upper()]) if f["name"].upper() in gflags else None, "hidden": f["hidden"],
                          "only_if": f["condition"] or None, "depends": f["depends_set"] + f["depends_reset"]} for f in sh["flags"]],
               "params": [{"name": p["name"], "type": p["type"], "ui_name": p["ui_name"], "default": p["default"], "min": p["min"], "max": p["max"], "step": p["step"],
                           "needs": p["conditions"] or None, "description": p["description"]} for p in sh["params"] if p["public"] or include_internal],
               "textures": [{"name": t["name"], "register": t["register"], "binds": t["tm"], "ui_name": t["ui_name"], "needs": t["conditions"] or None} for t in sh["textures"] if t["material_slot"] or include_internal]}
        if flags:
            out["resolved"] = shader_mod.resolve_flags(shader, flags)
        if gen_mask:
            out["decoded_gen_mask"] = shader_mod.decode_gen_mask(shader, gen_mask)
        return out
    except (OSError, KeyError, RuntimeError) as exc:
        return _refusal(exc)


@mcp.tool()
def shader_search(text: str, limit: int = 25) -> dict:
    """Find a shader flag, public parameter or texture slot by words in its name, label or description, across every shader ('parallax', 'tessellation', 'wind', 'subsurface', 'emittance',
    'blend'). Each hit says which shader has it. Then shader_info(shader) for the details and for what it needs."""
    try:
        rows, total = shader_mod.search(text, max(1, min(int(limit), 100)))
    except (OSError, RuntimeError) as exc:
        return _refusal(exc)
    return {"matches": total, "shown": len(rows), "results": rows}


import asset_catalog as asset_mod  # noqa: E402


@mcp.tool()
def asset_info(path: str) -> dict:
    """What the engine's asset system recorded about one asset (the `.cryasset` file next to it): its type, GUID, the files that make it up (a mesh's LOD chain), what it measured (triangle and vertex counts,
    material and texture counts, a texture's width/height/mips) and what it DEPENDS ON, plus what other recorded assets depend on it. `path` is relative to the project's Assets folder, or a GUID.
    Reads files only; works with the editor closed. An asset the engine has not generated metadata for is reported as such (asset_report('coverage') says how many)."""
    try:
        a = asset_mod.get(path)
        if not a:
            return {"ok": False, "refused": "no .cryasset metadata for %r (the engine has not recorded this asset: see asset_report('coverage'))" % path}
        return {"path": a["path"], "type": a["type"], "guid": a["guid"], "files": a["files"], "details": a["details"], "depends_on": [{"path": d["path"], "uses": d["uses"]} for d in a["deps"]],
                "used_by": asset_mod.users(a["path"]), "metadata_file": a["meta"]}
    except (OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
def asset_report(report: str = "summary", type: str = "", limit: int = 15) -> dict:
    """Questions about the project's assets answered from the engine's own metadata. report: 'summary' (assets by type, dependencies recorded) | 'coverage' (real asset files, by extension, that NO metadata
    covers: the engine has not recorded them) | 'heaviest' (the biggest of `type`: Mesh by triangles, Texture by pixels; default Mesh) | 'unused' (assets of `type` that no recorded asset depends on; default Material:
    a level layer may still use them, this does not read layers). Reads files only; works with the editor closed."""
    try:
        if report == "summary":
            return asset_mod.summary()
        if report == "coverage":
            return asset_mod.coverage()
        if report == "heaviest":
            return {"type": type or "Mesh", "heaviest": asset_mod.heaviest(type or "Mesh", max(1, min(int(limit), 100)))}
        if report == "unused":
            names, total = asset_mod.unused(type or "Material", limit=max(1, min(int(limit), 300)))
            return {"type": type or "Material", "unused_total": total, "shown": names}
        return {"ok": False, "refused": "report is one of summary, coverage, heaviest, unused"}
    except (OSError, ValueError) as exc:
        return _refusal(exc)


import asset_meta as meta_mod  # noqa: E402


@mcp.tool()
async def asset_metadata(apply: bool = False) -> dict:
    """Create the MISSING `.cryasset` metadata files with the ENGINE'S OWN pipeline: the Asset Browser's Edit > Generate/Repair All Metadata (the manual's 'Generating Metadata'), which the launcher's `metagen` action
    also runs: Tools/rc/rc.exe /job=Tools/cryassets/rcjob_cryassets.xml /src=<Assets>. The job REPLACES existing metadata (a new random GUID each time), so it is run with /targetroot into a scratch folder and only the files
    that do not exist yet are copied into the project (apply=true); an existing file is never touched. The `_lodN.cgf` files get none by design (part of their mesh). apply=false (default) is a dry run: how many it would
    create, by type, with examples. Afterwards asset_info and
    asset_report('coverage') cover every cgf/dds/mtl (what stays without metadata: the .adb and .bspace, which have no asset type). The metadata is for the editor's Asset Browser and the dependency queries; the game does not read it
    and the packager leaves it out. Writes only new files; nothing is overwritten, so no snapshot is needed."""
    try:
        r = await asyncio.to_thread(meta_mod.generate, bool(apply))
        r.pop("files", None)
        return r
    except (meta_mod.MetaError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import particle_catalog as particle_mod  # noqa: E402


@mcp.tool()
def particle_search(text: str = "", group: str = "", kind: str = "feature", limit: int = 30) -> dict:
    """Search the particle system (pfx2) the engine itself registered: the 54 FEATURES a .pfx effect's component can hold (Render: Sprites, Spawn: Rate, Field: Size, Location: Omni, Motion: Physics ...)
    and the 11 MODIFIER kinds a parameter can carry (Random, Curve, Noise, Wave, Linear, Attribute, Inherit ...), read from the engine's registry in the open editor, with every parameter and its DEFAULT.
    kind: 'feature' (default) | 'modifier'. group (features): Spawn, Life, Render, Field, Location, Motion, Appearance, Velocity, Angles, Child, Audio, Light, Project, Component, GPU Particles, General.
    No arguments: the groups. The file name of a feature is its group and name joined ('Render: Sprites' = "RenderSprites"). Use it BEFORE writing a .pfx by hand, then check the file with pfx_check."""
    try:
        if kind == "modifier":
            mods = particle_mod.modifiers()
            words = [w for w in text.lower().split() if w]
            rows = [m for m in mods if all(w in (m["name"] + " " + m["label"]).lower() for w in words)]
            return {"matches": len(rows), "results": [{"name": m["name"], "label": m["label"], "bases": m["bases"], "kept_by": [h for h, v in m["kept_by"].items() if v],
                                                       "parameters": sorted({k for d in m["defaults"].values() for k in d})} for m in rows[:max(1, min(int(limit), 100))]]}
        if not (text or group):
            return {"groups": particle_mod.groups(), "roles": particle_mod.FEATURE_TYPE_BITS}
        rows, total = particle_mod.search(text, group, max(1, min(int(limit), 100)))
    except (OSError, particle_mod.ParticleError) as exc:
        return _refusal(exc)
    return {"matches": total, "shown": len(rows), "results": [{"name": r["full"], "file_name": r["class_name"], "parameters": list(r["defaults"]),
                                                              "fills_role_by_default": r["role_default"] or None} for r in rows]}


@mcp.tool()
def particle_info(name: str, kind: str = "feature") -> dict:
    """One particle feature or modifier with every parameter and the DEFAULT the engine gives it (the JSON a .pfx holds), by editor name ('Render: Sprites'), file name ('RenderSprites') or, for kind='modifier',
    its name ('Random'). A feature also says which of the project's and the GameSDK sample's effects use it. Roles: a component needs a Life, a Spawn and a Size feature and may enable only ONE
    Life, Motion and Render feature (the engine disables a second one when it loads the effect; measured). Source of the registration is cited."""
    try:
        if kind == "modifier":
            hit = [m for m in particle_mod.modifiers() if m["name"].lower() == name.lower() or m["label"].lower() == name.lower()]
            if not hit:
                return {"ok": False, "refused": "no modifier named %r" % name, "modifiers": [m["name"] for m in particle_mod.modifiers()]}
            return hit[0]
        r = particle_mod.get(name)
        if not r:
            rows, _ = particle_mod.search(name, limit=6)
            return {"ok": False, "refused": "no feature named %r" % name, "did_you_mean": [x["full"] for x in rows]}
        uses = particle_mod.usage().get(r["class_name"], 0)
    except (OSError, particle_mod.ParticleError) as exc:
        return _refusal(exc)
    return {"name": r["full"], "file_name": r["class_name"], "group": r["group"], "connects_to_a_component": r["connector"], "fills_role_by_default": r["role_default"] or None,
            "defaults": r["defaults"], "used_by_effects": uses, "example_json": {r["class_name"]: r["defaults"]}}


@mcp.tool()
def pfx_check(path: str, keep_engine_version: bool = True) -> dict:
    """What the ENGINE holds of a particle effect file: the editor loads the .pfx with the engine's own loader and saves it back, and this compares the two (the editor must be open; the game is not run;
    nothing is replaced: the engine's rewrite goes to engine_data/pfx_scratch). `dropped`: features, modifiers and keys the engine did not recognise or whose names changed between versions: SILENTLY
    ignored, the parameter falls back to its default (the project's own dust/leaves/rain.pfx carry such keys); `changed`: values it clamped; `disabled_by_engine`: a second Life/Motion/Render feature
    in one component; `defaults_added`: parameters it filled in. Control: a file the engine wrote itself loads with no difference. A file of a version below the engine's (13) is CONVERTED while loading:
    its 'dropped' list then includes entries that were moved, not lost (see `note`). path: absolute or relative to the project."""
    try:
        r = particle_mod.check(path, keep_output=keep_engine_version)
    except (OSError, particle_mod.ParticleError) as exc:
        return _refusal(exc)
    kinds = {}
    for d in r["dropped"]:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    r["dropped_by_kind"] = kinds
    r["dropped"] = r["dropped"][:40]
    r["changed"] = r["changed"][:25]
    return r


import pfx_author as pfx_mod  # noqa: E402


@mcp.tool()
def pfx_write(path: str, components: list[dict], overwrite: bool = False) -> dict:
    """Write a particle effect (.pfx, the engine's JSON format) under Assets/Particles from a plain description, then LOAD it with the engine's own loader (the editor must be open) and report what the
    engine kept. Every value starts from the engine's DEFAULT for that feature (particle_info shows them); a feature or parameter the engine does not have is REFUSED up front, with the nearest names,
    instead of being silently dropped. components: [{"name": "embers", "parent": "", "features": [{"feature": "Spawn: Rate", "params": {"Amount": {"value": 80}}}, {"feature": "Render: Sprites",
    "params": {"FacingMode": "Camera"}}, {"feature": "Appearance: Material", "params": {"Texture": "textures/sprites/glow/glow_a.dds"}}, {"feature": "Appearance: Opacity", "params": {"BlendMode": "Additive"}}]}]}.
    A component needs a Spawn, a Life and a Size feature and may enable only one Life/Motion/Render feature (the engine switches a second one off: engine_check.disabled_by_engine). A parameter with modifiers is
    {"value": 0.35, "modifiers": [{"Random": {"Amount": 0.4}}]} (a colour parameter calls the list "Modifiers"). path: 'Particles/<folder>/<name>.pfx'. Never replaces a file unless overwrite=True; the editor's CryPak
    caches a loose asset, so use a new name instead of overwriting. Then look at it with pfx_preview."""
    try:
        return pfx_mod.write_effect(path, components, overwrite)
    except (OSError, particle_mod.ParticleError) as exc:
        return _refusal(exc)


@mcp.tool()
def pfx_preview(effect: str, position: list[float], distance: float = 6.0, wait_s: float = 4.0) -> dict:
    """Look at a particle effect in the EDITOR viewport (the game is NOT run): a control picture of the view, then a scratch entity with a Particle Emitter component at `position` (x, y, z metres; put it a
    little above the ground) is given the effect, a picture is taken after wait_s seconds, and the entity is deleted (the object count is checked; a graceful editor close would save a leftover). The camera looks at
    the emitter from `distance` metres to its south. Returns the picture, the pixels that changed against the control, their mean colour (and the control's there), the noise between two control pictures, and the
    component the engine used. A bright effect is bright against the background: a DARK result usually means a texture meant for additive blending drawn with Alpha. effect: 'particles/<folder>/<name>.pfx'."""
    try:
        return pfx_mod.preview(effect, position, distance, wait_s)
    except (OSError, RuntimeError, particle_mod.ParticleError, ValueError) as exc:
        return _refusal(exc)


import render_tour as tour_mod  # noqa: E402


@mcp.tool()
async def render_tour(settings: list[str] | None = None, eye: list[float] | None = None, target: list[float] | None = None) -> dict:
    """Which rendering switches visibly matter in THIS scene (the editor viewport, the game is NOT run): for each setting 'name=value' a fresh control picture of the view, the variable is set (and read back), a
    picture is taken, the variable is put back and read back. Returns per setting: the mean grey-level difference, the share of the picture that changed and where (3 x 3 grid), the `drift` (how far the
    UNCHANGED view moved since the previous control: shadows, streaming and eye adaptation make the scene drift after some switches) and `ratio` (difference over the larger of noise and drift: below 2 means
    'cannot tell', above 10 is plainly visible), the picture, the engine's own help text as label, and `restored`. No settings: the list of the 24 switches of the default tour (sun, shadows, SSDO, bloom,
    depth of field, motion blur, colour grading, flares, sun shafts, fog shadows, clouds, vegetation, merged meshes, decals, terrain detail, reflections, anti-aliasing, sharpening, wind ...). At most 12 per call
    (two pictures each). eye/target: world metres (default: the village from the south). The editor accepts cheat and const variables; a Release game refuses most (cvar_info says)."""
    try:
        if not settings:
            return {"default_tour": ["%s=%s" % (n, v) for n, v in tour_mod.DEFAULT_TOUR], "view": {"eye": tour_mod.DEFAULT_EYE, "target": tour_mod.DEFAULT_TARGET}}
        parsed = tour_mod.parse(settings)
        if len(parsed) > 12:
            return {"ok": False, "refused": "at most 12 settings per call (two pictures each); got %d" % len(parsed)}
        return await asyncio.to_thread(tour_mod.tour, parsed, eye, target)
    except (OSError, RuntimeError, tour_mod.TourError, ValueError) as exc:
        return _refusal(exc)


import sequence_check as seq_mod  # noqa: E402


@mcp.tool()
def sequence_check(path: str) -> dict:
    """What the ENGINE holds of a Track View sequence file (the editor must be open; the game is not run; the level's own sequences are not touched): the XML is loaded with the engine's own
    CMovieSystem::LoadSequence under a throw-away name, saved back with IAnimSequence::Serialize, and the two documents are compared. Returns the nodes, their tracks and key counts as the engine
    sees them, and what it DROPPED (an unknown attribute, a node of an unknown type: the whole node and its tracks vanish) or CHANGED (an unknown track parameter becomes 'Unknown Entity Parameter';
    a key value that is not a number). Numbers are compared as numbers (the engine writes 512 for 512.0000). `warnings`: keys after the sequence's end (the engine keeps them; they never play).
    The game's hand-written intro (libs/ashvale/sequences/ashvale_intro.xml) passes: 1 node, Position + Rotation, 15 keys each, 13 s. path: absolute or relative to the project."""
    try:
        return seq_mod.check(path)
    except (OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import ace_catalog as ace_mod  # noqa: E402


@mcp.tool()
def audio_triggers(text: str = "", problems: bool = True) -> dict:
    """The project's audio controls (the Audio Controls Editor's ACE libraries in Assets/audio/<middleware>/ace): every trigger with its events (sample, start/stop, volume, loops, fades, panning),
    parameters, switches and environments, found by words in the name; and, with problems=true, the check the editor does not make: the game refers to them by NAME through
    CryAudio::StringToId("name") and a misspelt or never-defined name produces NO error, only silence. Reports `undefined_in_code` (names the C++ uses that no library defines), `missing_samples`
    (an event names a file that is not there), `duplicates` (a trigger defined in two libraries), `broken_libraries`, `built_at_run_time` (triggers whose name starts with a literal prefix the code
    builds on, like 'ash_vox_' + dialogue id: the voice lines) and `unreferenced_triggers` (nothing refers to them). Reads files only; works with the editor closed."""
    try:
        out = {"libraries": ace_mod.load()["libraries"]}
        if text:
            rows = ace_mod.search(text)
            out["matches"] = len(rows)
            out["results"] = rows[:60]
        if problems:
            out["problems"] = ace_mod.problems()
        return out
    except (OSError, ValueError) as exc:    # a malformed library is already reported inside ace_catalog (broken_libraries)
        return _refusal(exc)


import editor_commands as cmds_mod  # noqa: E402


@mcp.tool()
def editor_commands(text: str = "", module: str = "", python_only: bool | None = None, limit: int = 40) -> dict:
    """Search the editor's own COMMAND list (ICommandManager: 642 commands in 40 modules, read from the open editor by the bridge verb bridge.commands.dump): module.name, the description, the parameters, and
    whether the command is also in the Python API catalog (editor_api). 114 commands are NOT in that catalog (ai.regenerate_agent_type_layer0..5, ai.regenerate_ignored, ai.generate_cover_surfaces,
    ai.set_navigation_update_*, asset.show_dependency_graph, camera.toggle_object_collisions ... menu and toolbar actions): run those with execute_editor_command, not Python. python_only: true = only those the
    Python catalog lists, false = only the others. No arguments: the modules with their counts."""
    try:
        if not (text or module) and python_only is None:
            return {"commands": len(cmds_mod.load()), "modules": cmds_mod.modules()}
        rows, total = cmds_mod.search(text, module, python_only, max(1, min(int(limit), 200)))
    except (OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)
    return {"matches": total, "shown": len(rows), "results": [{"command": r["full"], "description": r["description"], "parameters": [p["name"] + ": " + p["description"] for p in r["params"]],
                                                                  "in_python_api": r["in_python_api"], "ui_action": r["ui"], "deprecated": r["deprecated"]} for r in rows]}


import nav_check as nav_mod  # noqa: E402


@mcp.tool()
async def nav_reach(origin: str | list[float], targets: list[str | list[float]], agent: str = "-") -> dict:
    """Ask the NAVIGATION MESH with the engine's own connectivity test (INavigationSystem::IsPointReachableFromPosition), from the open editor, without running the game: is each target connected to the
    origin? origin and each target: an object's name from the level ('AshvaleStart', 'BanditYard', 'Tavern_Door') or 'x,y,z' in metres. Returns, per target, `reachable`, the mesh volume each end lies in
    (-1 = none), whether it is inside a volume, and a mesh ray cast. agent: an agent type name (default the first: 'Villager'). It does NOT know dynamic obstacles or OFF-MESH links: this project cuts every
    house door out of the mesh on purpose (a <House>_DoorCut area) and crosses it with the FarmGate's off-mesh link, so a house interior is an island here by design; the outdoors, doorsteps and
    open-sided buildings (barn, smithy) are what this answers. Controls that hold: start -> BanditYard connected; a point 3 km away is not."""
    def pos(v):
        return ",".join("%g" % c for c in v) if isinstance(v, (list, tuple)) else v      # [x, y, z] is accepted as well as 'x,y,z'
    try:
        return await asyncio.to_thread(nav_mod.reach, pos(origin), [pos(t) for t in targets], agent)
    except (nav_mod.NavError, OSError, RuntimeError, KeyError) as exc:
        return _refusal(exc)


@mcp.tool()
async def nav_map(x0: float, y0: float, x1: float, y1: float, step: float = 5.0, z: float | None = None, origin: str = "") -> dict:
    """An ASCII map of what the navmesh connects to `origin` (an object name or "x,y,z"; empty = the object named by the environment variable NAV_ORIGIN_OBJECT, default 'AshvaleStart') over a rectangle of the level, from the engine's own connectivity test (see nav_reach for what it does not know): '#'
    connected, '.' inside a mesh volume but NOT connected (a house interior behind its cut-out door, a sealed pocket), blank outside every volume. One character per `step` metres, z = the height the
    cells are tested at (empty = the origin's own z; the example village's floor is about 36.3-36.5). At most 4000 cells."""
    try:
        return await asyncio.to_thread(nav_mod.map_area, x0, y0, x1, y1, step, z, origin or None)
    except (nav_mod.NavError, OSError, RuntimeError, KeyError) as exc:
        return _refusal(exc)


import mannequin_check as mann_mod  # noqa: E402


@mcp.tool()
async def mannequin_check(adb: str = mann_mod.DEFAULT_ADB, controller: str = mann_mod.DEFAULT_CONTROLLER, character: str = mann_mod.DEFAULT_CHARACTER, reload: bool = False, confirm_reload: bool = False) -> dict:
    """Mannequin files (animation database .adb, controller definition, character) as the ENGINE reads them, from the open editor, without running the game. The files are loaded through the engine's own
    Mannequin loaders, written back out in memory with the editor manager's own Save calls (nothing touches the project) and compared with the file; the engine's own IAnimationDatabase::Validate runs
    against the character's animation set. Reports what the loader DROPPED silently (a fragment or option whose FragmentID or tag it does not know, a transition between unknown fragments, an unknown
    scope), every animation a fragment names that the character cannot play (`animations.missing`; the engine's Validate return value lies, the callbacks are counted), procedural clip types the engine
    does not register, and the manual's rules the engine does not check: scopes of one context sharing animation layers, a FragmentID with no entry in the controller definition, a database and
    controller definition using different tag/fragment files. Also checks the game's C++ against the database: every fragment name the code asks for (GetFragmentId, SetDefaultFragmentName, the villager's
    ActionFragment) must exist. Paths are relative to Assets/; pass '' to skip a file. reload=true makes the engine re-read the files (it caches a loaded database by name) but a live reload crashed
    Sandbox: it is refused unless confirm_reload=true too (restart the editor instead, then reload=false)."""
    if reload and not confirm_reload:
        return _refusal("reload=true re-reads the Mannequin files inside the running editor, which crashed Sandbox before (memory: never reload Mannequin files live; restart the editor, then check with reload=false). "
                        "If you really want it, pass confirm_reload=true as well")
    try:
        return await asyncio.to_thread(mann_mod.check, adb or None, controller or None, character or None, reload)
    except (mann_mod.MannequinError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
def mannequin_clips(text: str = "") -> dict:
    """The PROCEDURAL CLIP types Mannequin knows (what a fragment's procedural layer can run: Audio, ParticleEffect, PositionAdjust, AimPose, LookPose, SetParam, ActionEvent, AttachProp, SwapAttachment,
    FlowGraphEvent, AISignal, ForceFeedback, JointAdjust ...): 27 read from the engine's own IProceduralClipFactory with every parameter and its default (the clip's IProceduralParams written through
    the engine's JSON archive). text: part of a clip or parameter name ('' = all). Cached in engine_data/mannequin_clips.json; the editor is only needed the first time. A clip not in this list does nothing
    when a fragment names it (mannequin_check reports it)."""
    try:
        rows = mann_mod.clip_search(text)
    except (mann_mod.MannequinError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)
    return {"matches": len(rows), "clips": rows}


import bt_check as bt_mod  # noqa: E402


@mcp.tool()
async def bt_check(tree: str = bt_mod.DEFAULT_TREE) -> dict:
    """A BEHAVIOUR TREE file as the ENGINE loads it, from the open editor, without running the game: the engine's own loader steps (variables, events, signals, timestamps, the nodes through the engine's
    node factory) run STRICTLY (what the game does: any error refuses the tree and the entity then runs no behaviour) and LENIENTLY (what the Behavior Tree Editor does), and the engine's own description of
    the tree it kept is compared with the file. Measured: an unknown node type, a transition to a state that does not exist, a condition on an undeclared variable, an invalid structure and a GameSDK
    GameDll node (Melee, Shoot ... not loaded by this game) REFUSE the tree; an unknown ATTRIBUTE and a value that is not a number are accepted silently (the engine holds 0), and an event nothing sends is only
    a warning ('declared automatically'): the comparison with the file finds the first two. Also compares the events the game's C++ sends (SendBehaviorTreeEvent) and the blackboard keys it writes
    (BlackboardVariableId) with what the tree declares, handles and reads. tree: a file path, or a name in Assets/scripts/AI/BehaviorTrees (default Villager_Patrol, which passes). Nodes the engine cannot
    describe (Basic_MoveToPosition comes back as a bare <Node>) are listed in `not_comparable`."""
    try:
        return await asyncio.to_thread(bt_mod.check, tree)
    except (bt_mod.BtError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def bt_nodes(text: str = "", refresh: bool = False) -> dict:
    """The BEHAVIOUR TREE node types the engine's node factory can create (65 registered in the source, 56 created in this editor), each with the attributes and defaults the node reports about itself
    (Wait: duration, variation; Log: message; Aim: angleThreshold 90 ...). text: part of a node name, attribute or source file ('' = all). `created: false` = registered by the GameSDK's GameDll
    (Melee, HoldFormation, SquadScope ...), which this game does not load: a tree using one is refused. The component-safe Basic_MoveToPosition / Basic_MoveToCover report no attributes (the engine's
    description of them is empty). Cached in engine_data/bt_nodes.json; refresh=true asks the editor again."""
    try:
        rows = await asyncio.to_thread(bt_mod.node_search, text, refresh)
    except (bt_mod.BtError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)
    return {"matches": len(rows), "created": sum(1 for r in rows if r["created"]), "nodes": rows[:80]}


import uqs_check as uqs_mod  # noqa: E402


@mcp.tool()
async def uqs_check(query: str = uqs_mod.DEFAULT_QUERY) -> dict:
    """A UNIVERSAL QUERY SYSTEM query file checked by the ENGINE's own validator, from the open editor, without running the game: the UQS hub's editor service (what the Query Editor uses) loads libs/ai/uqs/<name>.uqs
    with the XML data source and validates it against everything the engine has registered, reporting each problem on the element it belongs to (an unknown generator, evaluator, function, item type or score
    transform; a generator input left out; a root that is not <Query>; XML that is not well-formed). Nothing is stored in the library and nothing runs. Measured: the engine's validation ACCEPTS a literal that is not
    a number (returnValue="forty" for a float: it fails only when the query starts: caught here as U015) and matches an <Input> by its 4-character paramID, so a renamed input still works. Also compares the
    game's C++ with the file: the query name it asks for (SetQueryBlueprint) must exist, and the runtime parameters it sets (AddOrReplace) must be the ones the query declares (U020/U021). query: a name in
    libs/ai/uqs (default BestSpotToStand, which passes) or the path of any .uqs file (checked as a temporary registered copy the engine deletes again)."""
    try:
        return await asyncio.to_thread(uqs_mod.check, query)
    except (uqs_mod.UqsError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def uqs_catalog(text: str = "", kind: str = "", refresh: bool = False) -> dict:
    """What the UNIVERSAL QUERY SYSTEM has registered with its hub in this editor: 16 item types, 33 functions, 11 generators (std::PointsOnGridProjectedOntoNavMesh, PointsOnNavMesh, PointsOnPureGrid, PropagateShuttledItems),
    6 instant evaluators (TestMinDistance, TestMaxDistance, TestLocationInNavMesh, ScoreDistance, ScoreDistanceInverse, ScoreRandom), 1 deferred evaluator (TestRaycast), 6 score transforms and the 3 query factories
    (Regular, Chained, Fallbacks), each with its description and, for functions, generators and evaluators, the input parameters (name, 4-character id, type, description) from the engine's own input parameter
    registry. text: part of a name, description, type or input name ('' = all); kind: items, functions, generators, instant_evaluators, deferred_evaluators, score_transforms or query_factories. Cached in
    engine_data/uqs_catalog.json; refresh=true asks the editor again. Use it before writing a .uqs file: a name that is not here is rejected by uqs_check."""
    try:
        rows = await asyncio.to_thread(uqs_mod.search, text, kind, refresh)
    except (uqs_mod.UqsError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)
    return {"matches": len(rows), "results": rows[:60]}


import level_stats as stats_mod  # noqa: E402


@mcp.tool()
async def level_stats(top: int = 10, sheet: str = "") -> dict:
    """The LEVEL'S RESOURCE REPORT as the engine writes it, from the open editor, without running the game: runs the editor command level.save_stats (the engine's SaveLevelStats: two Excel-format workbooks in
    user/testresults, which are not well-formed XML: this reads them anyway) and answers: the summary (CGF objects, texture and mesh counts and MB, game and virtual memory), the heaviest textures (engine render
    targets and pools are listed apart: the cached shadow maps are the largest), the heaviest geometry by texture + mesh KB, `project_meshes` = every mesh under the project's objects folder with the LODs and triangles per LOD AS THE
    ENGINE LOADED THEM and its physics triangles, `no_lod_meshes` (1,000+ triangles, no LOD), characters and entity count.
    sheet: one of Static Geometry, Characters, Meshes, Brushes, Textures, Materials, Physical Entities, Entities, Memory Stats, Modules Memory Info, Profiler ... to get its `top` rows by size instead. The editor has
    only what it has loaded: a resource census, no frame statistics (load time and fps read 0). Writes only user/testresults/<level>.xml and depends_<level>.xml."""
    try:
        stats, deps, log = await asyncio.to_thread(stats_mod.save_stats)
        if sheet:
            return await asyncio.to_thread(stats_mod.top, stats, sheet, max(1, min(int(top), 100)))
        out = await asyncio.to_thread(stats_mod.report, stats, max(1, min(int(top), 50)))
        out["files"] = {"statistics": stats.replace(chr(92), "/"), "dependencies": deps.replace(chr(92), "/")}
        return out
    except (stats_mod.StatsError, OSError, RuntimeError, ValueError, KeyError) as exc:
        return _refusal(exc)


@mcp.tool()
async def level_validate() -> dict:
    """The editor's own 'Check Level for Errors' (level.validate), from the open editor, without running the game and without changing the level: every object validates itself (a missing or undefined resource), every object's
    used resources are checked, duplicates are looked for. Returns the warnings grouped by kind with every message. Measured on Ashvale: ~20 materials name a surface type the engine does not have ('glass', 'stone', 'cobble' ...:
    physics and effects fall back to mat_default). 'Geometry File in Slot N not found' for the nine Bread pickups is a FALSE POSITIVE (control: a scratch entity with a plain Mesh component on the engine's default
    sphere gets the same line; the check flags the editor's own helper object in the slot, and a close-up shows the loaves rendered). The editor also has object.validate_positions (objects and vegetation placed
    inside other objects) as a separate command."""
    try:
        return await asyncio.to_thread(stats_mod.validate)
    except (stats_mod.StatsError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import rc_catalog as rc_mod  # noqa: E402


@mcp.tool()
def rc_presets(text: str = "", file_name: str = "") -> dict:
    """The RESOURCE COMPILER's texture presets, read from Tools/rc/rc.ini (the file RC itself loads; the editor need not be open): 44 presets with their pixel format, colour space, mip settings, the comment above each and the
    FILE NAME MASKS that choose them (*_diff* -> Albedo / AlbedoWithOpacity / AlbedoWithCoverage ..., *_ddna* -> NormalsWithSmoothness, *_displ* -> Displacement (BC4, swizzle aaa1), *_bump* -> NormalsFromDisplacement ...).
    text: part of a name, description or option value ('' = all). file_name: also tells which presets RC may pick for a file with that name (RC chooses among them by what the image holds: an alpha channel turns Albedo
    into AlbedoWithOpacity; CryTIF can store /preset=<name> inside the TIFF and `rc /preset=` names it explicitly: a _displ height map in the ALPHA channel needs the preset named, or RC picks BC3). `aliases` maps the
    37 legacy preset names to the current ones."""
    try:
        ini = rc_mod.parse_ini()
        out = {"matches": rc_mod.presets(text, ini)}
        out["count"] = len(out["matches"])
        if file_name:
            out["for_file"] = {"file": file_name, "presets": rc_mod.for_file(file_name, ini)}
        out["aliases"] = ini["aliases"] if not text else {k: v for k, v in ini["aliases"].items() if text.lower() in (k + v).lower()}
        out["global_settings"] = ini["globals"]
        return out
    except (rc_mod.RcError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
def rc_options(text: str = "", refresh: bool = False) -> dict:
    """The RESOURCE COMPILER's own command-line reference: the 176 options from `rc.exe /help` (key, which compiler reads it: [TIF] [CGF] [ABC] [cryasset] ..., the description) and the 13 converters RC registers
    with the file extensions each compiles (ImageConverter: tif hdr; TextureSplitter: dds; StatCGFCompiler: cga cgf i_cgf; CharacterCompiler: chr cdf skin; AnimationConverter: i_caf; FbxConverter: fbx; MetadataCompiler:
    cryasset; LuaCompiler: lua; ColladaCompiler: dae; SubstanceConverter: crysub; XMLConverter: xml; zip/pak). text: part of an option key or description ('' = all). Cached in engine_data/rc_options.json; refresh=true
    runs rc.exe /help again (it only prints text; nothing is compiled). Check an option here before passing it: RC reports an unknown key only as a warning."""
    try:
        d = rc_mod.options(text, refresh)
    except (rc_mod.RcError, OSError) as exc:
        return _refusal(exc)
    return {"version": d["version"], "converters": d["converters"], "matches": len(d["options"]), "options": d["options"][:60]}


import character_check as chr_mod  # noqa: E402


@mcp.tool()
async def character_check(paths: list[str] | None = None) -> dict:
    """CHARACTERS as the ENGINE resolves them, from the open editor, without running the game: each .cdf is created the way the game and the Character Tool create it (ICharacterManager::CreateInstance) and the engine's
    resolved state comes back: the skeleton and its joint count (the villagers' sdk_player: 163 joints, 1,692 animations), every attachment (type, joint, whether an object is bound and its file), plus the engine's own log
    lines of the load. Compared with the .cdf file and the game's C++: C001 the character cannot be created, C002 an attachment the engine did not build, C003 an attachment whose file the engine could not load (it
    then binds a PLACEHOLDER object, so 'bound' alone shows nothing: found from the engine's 'Failed to open file' line; an error when the game refers to the character, a warning when nothing does), C004 an attachment on a
    bone the skeleton lacks, C005 other engine warnings, C007 a character the C++ names that is missing. Measured: sdk_player.cdf (the one the code uses) is clean; the ten colour/hat variants copied from the GameSDK
    (beanie, glasses, helmet, hipster, sunglasses) point at attachment meshes that are not in the project: inert, but shipped. paths: .cdf paths relative to Assets (default: every .cdf in the project). A character
    is created and released at once (the AddRef/Release rule is kept); nothing is spawned in the level."""
    try:
        return await asyncio.to_thread(chr_mod.check, paths or None)
    except (chr_mod.CharacterError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import material_check as mtl_mod  # noqa: E402


@mcp.tool()
async def material_check(paths: list[str] | None = None) -> dict:
    """MATERIALS as the ENGINE resolves them, from the open editor, without running the game: each .mtl is loaded through the engine's own material manager (I3DEngine::GetMaterialManager()->LoadMaterial) and what
    it ended up with comes back: per (sub-)material the SURFACE TYPE (an undefined name silently becomes mat_default), the shader loaded and its generation mask, every texture slot with the file asked for and the one
    the engine bound, and the engine's own load log. Compared with the .mtl file: M001 the material does not load, M002 SurfaceType in the file is not the one the engine uses, M003 the shader did not load (the engine keeps
    the NAME; only its 'Failed to load shader' log line shows it), M004 a valid StringGenMask flag the final mask dropped, M005 sub-material count differs, M006 a texture the engine did not bind or replaced by its
    placeholder (a missing normal map becomes White_ddn), M007 other load warnings, M008 a texture file that is not in the project (a missing DIFFUSE map is not swapped by name: only the file shows it).
    Measured (editor, copies of one material): the engine ORs bits in that nothing asked for (Illum: NORMAL_MAP from a normal-map texture, SUBSURFACE_SCATTERING always), so %NORMAL_MAP / %BUMP_MAP in a string change
    nothing; %PARALLAX_OCCLUSION_MAPPING does; a numeric GenMask counts only when StringGenMask is absent. The engine logs a material's problems only the FIRST time it loads in a session (the editor must be restarted
    for a second look; engine_data/material_load_logs.json remembers what earlier runs saw). paths: .mtl paths relative to Assets (default: all). Loading registers a material in the editor's library for the
    session; no file is changed."""
    try:
        return await asyncio.to_thread(mtl_mod.check, paths or None)
    except (mtl_mod.MaterialError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import flow_check as flow_mod  # noqa: E402


@mcp.tool()
async def flow_nodes(text: str = "", type: str | None = None, refresh: bool = False, limit: int = 40) -> dict:
    """The FLOW GRAPH node catalog, read from the engine's own flow system (the open editor): all 642 registered node types (UI 110, Math 48, entity 42, AI 38, Physics 33, ...), each created in a scratch graph and read back:
    category (approved 545 / advanced 75 / debug 19 / obsolete 3, from the engine's flags), description, every input port (name, data type Void/Int/Float/EntityId/Vec3/String/Bool or Any, default, UI config) and
    every output port. text: words matched against the type name, description and port names/descriptions ('timer', 'door open'); type: ONE node type in full ('Time:Timer', case-insensitive). The game's own nodes
    (Claude:Lamp, Claude:VillagerSays) are in the list. Cached in engine_data/flow_nodes.json; refresh=true asks the editor again (a new node needs the game plugin loaded: restart the editor after a Game.dll build).
    Use it before writing a graph: a port name or node type that is not here is silence or a discarded graph (flow_check says which)."""
    try:
        if type:
            row = await asyncio.to_thread(flow_mod.node, type, refresh)
            return {"found": row is not None, "node": row} if row else {"found": False, "hint": "search with text= to find the exact type name"}
        rows, total = await asyncio.to_thread(flow_mod.search, text, max(1, min(limit, 200)), refresh)
        return {"matches": total, "shown": len(rows), "nodes": rows}
    except (flow_mod.FlowError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def flow_check(paths: list[str] | None = None) -> dict:
    """FLOW GRAPH files as the ENGINE loads them, from the open editor, without running the game: each graph XML is read with IFlowGraph::SerializeXML (the call the game's claude_flowgraph command and the level loader
    use) into a scratch graph that is neither enabled nor initialised (no node runs), and what the engine kept comes back: every node with the value each input port holds, every edge by node and port NAME. Compared
    with the file and the node catalog: F001 the XML cannot be read, F002 the engine DISCARDED the graph (a bad node Class or edge makes it throw the whole graph away; its reason is quoted), F003 a Class that is not a
    registered node type, F004 an <Inputs> attribute that is not a port (SILENTLY ignored: the graph loads), F005 an edge to a node or port that does not exist, F006 a value that did not convert (kept the default,
    silently), F007 node/edge counts differ, F008 a debug or obsolete node, F009 a node the game's C++ registers (REGISTER_FLOW_NODE) that the engine does not list. paths: graph files relative to Assets (default every
    XML under libs/FlowGraphs; the project has one, libs/FlowGraphs/claude_farm.xml: 4 nodes, 3 edges, loads clean). Level graphs are stored inside the level's layers and are not read here."""
    try:
        return await asyncio.to_thread(flow_mod.check, paths or None)
    except (flow_mod.FlowError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import anim_check as anim_mod  # noqa: E402
import anim_sample as sample_mod  # noqa: E402
import cryproject_check as proj_mod  # noqa: E402


@mcp.tool()
async def anim_check(character: str = "Objects/Characters/Human/sdk_player/sdk_player.cdf") -> dict:
    """CHARACTER ANIMATION DATA against the names the ENGINE resolved, from the open editor: the character is created (ICharacterManager::CreateInstance, verb bridge.character.names) and its skeleton's joint names (163 for the
    villager) and animation set (1,692 names) are compared with the project's own files. A001 a .chrparams names a joint the skeleton lacks (IK end effector/root, aim/look rotation and position lists, weapon joints, IK
    target and blend bones; a LOD joint list entry that matches nothing is only a warning), A002 a chrparams $-entry (events database, include, face library) whose file does not exist, A003 a blend space (.bspace)
    example that is not in the animation set (the engine silently leaves it out), A004 a blend-space index out of range (Pseudo/Face), A005 a dimension or extraction parameter that is not one of the engine's 14 motion
    parameters (MoveSpeed, TurnSpeed, TravelAngle ...), A006 a blend space with too few points or no faces. Measured: the villager's villager_move.bspace is clean (7 examples, all in the set; it joins the set by its
    file name) and so are the IK/aim joints of skeleton_player_generic.chrparams; its LOD list names two twist joints the skeleton lacks (a warning). character: any .cdf/.chr the engine can create."""
    try:
        return await asyncio.to_thread(anim_mod.check, character)
    except (anim_mod.AnimError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def anim_sample(clip: str, times: list[float] | None = None, joints: list[str] | None = None,
                      character: str = "Objects/Characters/Human/sdk_player/sdk_player.cdf") -> dict:
    """WHAT THE ENGINE COMPUTES FROM A CLIP, from the open editor, without running the game: the clip is started on a fresh instance of the character (verb bridge.character.samplepose), stepped to each normalized time
    (0 = first frame, 1 = last; default 0, .25, .5, .75) and the animation job run, then the absolute position of the key joints (pelvis, spine, head, hands, feet, toes, the root Bip01 and the locator; or `joints`) is read from the
    engine's own pose: metres, Z up, +Y forward, the ground at 0. Also the clip's duration, the root's ground speed (the root joint Bip01, else the pelvis, between the first and the last time), and how low the soles get
    (lowest_sole_m: about 0 for a clip that stands on the ground, negative = sunk, positive = floating). A clip that is not yet in the animation set (a .caf written since the skeleton loaded) is found by rescanning
    the animation folders. Use it to check a converted FBX, a retargeted motion or any library clip against what you think it does: it is how generated (for example MotionBricks) clips are proven to be what they were built to be (to
    0.3-1.3 mm over 18 joints). The pose per joint also carries its parent-relative rotation in the plugin's raw file; anim_sample.raw() returns it."""
    try:
        return await asyncio.to_thread(sample_mod.sample, clip, tuple(times) if times else (0.0, 0.25, 0.5, 0.75), character, joints)
    except (sample_mod.SampleError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def cryproject_check(paths: list[str] | None = None) -> dict:
    """A .cryproject FILE as the engine reads it. The engine's own parse (Cry::IProjectManager, verb bridge.project.info, from the open editor) is compared with the file for the project that is open (name, GUID, engine id,
    every plug-in entry with type and platforms: P009 if the engine read something else), and the file is checked against the parser's rules and the engine's data: P001 not JSON or a required section missing (version,
    info, content, require), P002 older version (migrated on load), P003 not a GUID, P004 require.engine not in the launcher's engine list (C:\\ProgramData\\Crytek\\CRYENGINE\\cryengine.json), P005 an assets/code directory
    that does not exist, P006 a plug-in type the parser does not know (EType::Native/Managed and the older EPluginType::Native/Managed are both read) or a platform that is not an EPlatform, P007 a plug-in DLL that is not
    where the plug-in manager looks (a bare name under the engine's bin/win_x64, a path ending .dll under the project), P008 a console variable that is not in the engine's catalog (a typo is silence), not a number for
    a numeric variable, or not settable in Release, P010 a plug-in listed twice. Measured: Game.cryproject is clean and the engine read it exactly (7 plug-ins, CryGamePlatform for PS4 only); GenerateShaderCache.cryproject
    sets five variables this engine build does not have (d3d9_NullRefDevice, e_profile_level_loading, log_FileVerbosity (this engine has log_WriteToFileVerbosity), r_shaderscacheinmemory, sys_rendersplashscreen). paths: relative to the project folder (default every *.cryproject)."""
    try:
        return await asyncio.to_thread(proj_mod.check, paths or None)
    except (proj_mod.ProjectError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import drs_check as drs_mod  # noqa: E402


@mcp.tool()
async def drs_check(reload: bool = False) -> dict:
    """The DYNAMIC RESPONSE SYSTEM (the villagers' barks and reactions) as the running ENGINE holds it, from the open editor, without the game: the action and condition TYPES its class factories know (12 actions: SpeakLine,
    ChangeVariable, SendSignal, Wait, SetGameToken ...; 12 conditions: 'Variable equal to ' with its trailing space, Random, TimeSince, GameToken ...), its dialog line database, and each response tree AS THE ENGINE SERIALISES
    IT (what it KEPT) compared with the .response / .dialog files. D001 a response the engine holds nothing for, or a file that is not valid JSON (the engine then makes an EMPTY response and logs NOTHING), D002 an action
    or condition class the engine does not know (dropped silently), D003 a SpeakLine for a line the database lacks, D004 a signal the C++ sends that no .response answers (and the reverse as information), D005 a dialog
    line's audio trigger that no audio library defines, D006 a value the engine did not keep, D007 a dialog line that differs from the engine's copy or is not loaded, D008 a problem logged on reload. A response's signal
    name is its FILE'S BASE NAME. reload=true re-initialises the DRS first (IDynamicResponseSystem::ReInit, the call the game's Dialogue.cpp makes: reloads the files, resets only the editor's DRS state).."""
    try:
        return await asyncio.to_thread(drs_mod.check, bool(reload))
    except (drs_mod.DrsError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import ui_check as ui_mod  # noqa: E402


@mcp.tool()
async def ui_check(init_movie: bool = True) -> dict:
    """The project's UI (FlashUI / Scaleform: the menus, map, loading page) as the running ENGINE holds it, from the open editor, without the game. FlashUI reads Libs/UI/UIElements/*.xml at start; the game then calls the
    movie BY NAME (Call("SetupScreen", ...), GetUIElement("MainMenu"), SameName(event.sName, "onButton")) and a name the XML does not declare is NOT an error: the engine makes a 'dynamic' definition and calls the
    movie by that name, so a typo is silence. The plugin verb bridge.ui.dump writes what the engine KEPT of each element (movie file, layer, flags, constraints, functions, events, arrays, movie clips with parameters,
    UI actions, event systems) and, with init_movie, loads the movie in the editor (IUIElement::Init, the call the game makes when a screen is shown) and asks it IFlashPlayer::IsAvailable for every kept name, with a
    positive control (_root) and a negative one (a made-up name). Compared with the XML read by the engine's rules and with the C++: U001 bad XML / no GFx, U002 element missing in the engine or asked for in code but
    undeclared, U003 .gfx missing, U004 the engine kept something else (a duplicate dropped, layer, flags, constraints, parameter count), U005 a call to an undeclared function (dynamic definition; says when the
    name is the movie's name and not the XML name), U006 a call to a function the LOADED MOVIE lacks (error; a declared function the movie lacks is information), U007 argument count or kind differs from the
    declaration, U008 an event the code handles that is not declared, U009 the movie query failed its controls, U010 a Libs/UI file the code names that does not exist, U011 declared array/clip not in the movie at load,
    U012 UI action files the engine did not load, U013 attributes/values the engine ignores. init_movie=false only reads the engine's records."""
    try:
        return await asyncio.to_thread(ui_mod.check, ui_mod.UI_DIR, ui_mod.CODE_DIR, None, True, ui_mod.ASSETS_DIR, bool(init_movie))
    except (ui_mod.UiError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import editor_ux as ux_mod  # noqa: E402


@mcp.tool()
async def editor_notify(title: str, message: str = "", level: str = "info") -> dict:
    """Tell the user something INSIDE the open editor: a non-modal message in the Notification Center (INotificationCenter::ShowInfo / ShowWarning / ShowCritical, the manual's preferred way: no dialog, no
    focus change). level: info | warning | critical. title up to 80 characters, message up to 400. Use it when a long job ends or a check finds something while the user is looking at the editor, not instead of
    answering in the conversation. Returns the notification's id (it cannot read back whether the user saw it)."""
    try:
        return await asyncio.to_thread(ux_mod.notify, level, title, message)
    except (ux_mod.UxError, OSError) as exc:
        return _refusal(exc)


@mcp.tool()
async def editor_undo(action: str = "info", steps: int = 1, expect: str = "") -> dict:
    """The editor's own UNDO stack (IUndoManager; what Ctrl+Z / Ctrl+Y use). action 'info': mode, undo/redo step counts and the last 10 undo step names, newest first. 'undo': takes back the newest `steps` steps, but
    ONLY if every one of them contains `expect` in its name (the stack also holds the user's own edits: an undo of the wrong step is refused, nothing is changed). 'redo': re-applies what an undo took back. Steps
    recorded by the tools: place_object ('Create new object (name)', by the editor's own Python function), create_object ('Claude: create <Type> (name)'), object_properties ('Claude: set properties'). The bridge's
    other object verbs (used by the test tools' scratch objects) record nothing unless asked. Does not save the level."""
    try:
        return await asyncio.to_thread(ux_mod.undo, action, steps, expect)
    except (ux_mod.UxError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def editor_panel(action: str = "status") -> dict:
    """A "Claude Status" PANE inside the Sandbox editor, written in Python/PySide2 and run by the editor's own interpreter (no C++, no file added to the engine folder): the open level and its object count, the
    bridge plugin file, the last accepted project-audit baseline, the last editor-suite run and the newest jobs, refreshed every 3 seconds. action 'status' says whether the pane class is registered and how many
    panels are open; 'open' registers the class once per editor session (SandboxBridge.register_window; the registration is lost when the editor restarts) and opens the pane as a floating window in the user's
    editor: do it when the user wants to watch, not unasked; 'close' closes that window and nothing else. The pane only reads small files and cheap editor calls: it never calls the bridge socket or starts a process
    (a blocking call from inside the editor's Python deadlocks the editor). Sandbox auto-loads Python plugins only from the ENGINE install (Editor/Python/plugins/*/startup.py), which this does not touch."""
    try:
        return await asyncio.to_thread(ux_mod.panel, action)
    except (ux_mod.UxError, OSError, ValueError) as exc:
        return _refusal(exc)


import level_check as lvl_mod  # noqa: E402


@mcp.tool()
async def level_check(level: str = "") -> dict:
    """The level's SETTINGS and its EXPORTED copy against what the engine reads, without the game. The level holds (Assets/levels/<level>/leveldata/) environment.xml (Level Settings: Fog, Terrain, EnvState, VolumetricCloud,
    Ocean ...), timeofday.xml (the level's LIST of environment presets and its default) and level.pak (the exported level: the game reads mission_*.xml in it, never the source files). The game looks its presets up BY NAME
    in the level's list (WorldTools.cpp FindPreset 'ashvale_clear' / 'ashvale_rain'); a level without them is not an error anywhere, the sky just stays on the list's default. V001 a preset the game code looks for is not
    in the SAVED list (error: the next export drops it), V002 the open editor's list differs from the saved one, V003 the EXPORTED level lacks a preset the game looks for (error) or lists others than the source,
    V004 the exported Environment differs from environment.xml, V005 an element/attribute the 3D engine does not read (loader contract of 3dEngineLoad.cpp: ignored silently), V006 a value clamped or read as 0 (a word
    where a number goes) or a view distance under 1000 (manual), V007 a path that does not exist (ocean material, cloud textures, the preset files), V008 entities in the exported level that differ from the source layers
    (added, removed, changed property; moved = information), V009 the level's default preset is the engine's default.env (the editor opens with a look the game never uses). The level's timeofday.xml can list only the engine's default.env while the exported level.pak still lists the level's own presets; the next export would then ship a game that finds no preset to switch to. A packaging step can refuse on V001/V003/V007 errors. The editor-free
    part works with the editor closed; with it open V002 compares the live list."""
    try:
        return await asyncio.to_thread(lvl_mod.check, level or None)
    except (lvl_mod.LevelError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import frame_profile as fp_mod  # noqa: E402


@mcp.tool()
async def frame_profile(frames: int = 240, compare_cvars: dict[str, str] | None = None, top: int = 10) -> dict:
    """Where the CPU time of a frame goes, per thread and per function, from the ENGINE'S OWN frame profiler (the boot profiler: `sys_bp_frames <n>` records the next n frames as bp_frames.xml in
    user/TestResults, the file ProfVis.exe opens), on the OPEN EDITOR's viewport, without the game. Needs the editor; switches the profiler on and keeps the editor active for about a second per capture
    (a long hold can crash a graphics driver), then puts both back. 240 frames are about 1.2 s: enough to catch something that happens once a second. Returns the summary (main-thread ms per frame,
    median and longest frame, the `top` functions by SELF time with ms per frame, calls per frame and how many threads ran them) and findings: P001 the frame time and the largest cost, P002 log writes
    (CLog::LogV) inside the frame, P003 HITCH frames (much longer than the median) with the functions that made them so, P004 how long the render thread waited for the main thread. compare_cvars
    {'e_MergedMeshes': '0'}: captures the state as it is, with the variables set, and as it is again (the REPEAT is the noise a difference must exceed 3 x) and returns what changed per frame, the variables put
    back.
    Observed on the author's Ashvale example with CRYENGINE 5.7.1 on Windows: it found a once-per-second hitch caused by lamps writing trace lines. This is the EDITOR's frame, not the shipped game's: use it to compare states and to find work that repeats."""
    try:
        if compare_cvars:
            a = await asyncio.to_thread(fp_mod.capture, frames)
            b = await asyncio.to_thread(fp_mod.capture, frames, 40.0, compare_cvars)
            a2 = await asyncio.to_thread(fp_mod.capture, frames)
            rep = fp_mod.compare(a, b, a2, top)
            return {"cvars": compare_cvars, "frames": [a["frames"], b["frames"], a2["frames"]], "main_ms_per_frame": [fp_mod.summary(p)["main_ms_per_frame"] for p in (a, b, a2)],
                    "changed_ms_per_frame": rep["changed"], "functions_compared": rep["functions_compared"], "note": "'noise' is the same-state repeat; a change under 3 x noise (or 0.05 ms) is not listed"}
        prof = await asyncio.to_thread(fp_mod.capture, frames)
        rep = fp_mod.check(prof)
        rep["summary"] = fp_mod.summary(prof, top)
        return rep
    except (fp_mod.ProfileError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import visarea_check as va_mod  # noqa: E402


@mcp.tool()
async def visarea_check(level: str = "", probes: list[list[float]] | None = None) -> dict:
    """The level's VIS AREAS, PORTALS and OCCLUDER AREAS as the 3D ENGINE holds them (plugin verb bridge.visarea.dump: I3DEngine::GetIVisAreaManager lists every area with its name, flags, box, shape points and the
    areas it connects), against the layer files and the exported level pack's terrain/indoor.dat, without the game. The manual says objects inside a Vis Area are not drawn from outside, a Portal cuts an entrance, a closed
    area is invisible from outside; the ENGINE adds rules it does not state (VisAreaMan.cpp UpdateVisArea): (1) an area is classified by a WORD IN ITS LOWER-CASED NAME: 'portal', then 'visarea', then
    'occlarea', and a name with none of them is kept in NO list (a VisArea called Reeve_interior silently does nothing; one called visarea_portal_room is a portal; the editor adds 'Portal_' / 'OcclArea_' to a Portal's /
    OccluderArea's own name, so those always classify); (2) the editor registers an area only with MORE THAN 3 points; (3) a portal connects areas only when its first 4 points form a quad whose opposite edges cross a wall
    of the area, 1 connection = to the outdoors, 2 = room to room, more is invalid. V001 name not classified as its kind (error), V002 under 4 points (error), V003 portal that connects nothing (warning), V004 vis area no
    portal reaches (closed: warning), V005 portal with 3+ connections (error), V006 layer object the engine does not hold, V007 the exported pack's counts differ from the layer (stale export, error), V008 engine area no
    layer object explains, V009 the level has no areas at all (information), V010 portal with more than 4 points. probes: [[x,y,z], ...] positions to look up: the area that contains each (the engine's
    GetVisAreaFromPos after forcing its tree to rebuild, and the direct per-area test; both are null/empty outdoors). With the editor closed only the file checks run. create_object warns when a new area's name will not be classified."""
    try:
        level = await asyncio.to_thread(_open_level_folder, level)      # empty = the level the editor has open
        engine = None
        if probes:
            engine = await asyncio.to_thread(va_mod.engine_dump, probes)
        rep = await asyncio.to_thread(va_mod.check, level, va_mod.LEVELS, engine)
        if engine:
            rep["probes"] = engine.get("probes", [])
        return rep
    except (va_mod.VisAreaError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import object_check as obj_mod  # noqa: E402


@mcp.tool()
async def object_check(level: str = "") -> dict:
    """The level's NON-ENTITY objects (brushes, navigation areas, environment probes, shapes ...) as the open EDITOR kept them against the layer files (layers/*.lyr), without the game: the sibling of entity_check.
    Each object reads the attributes it knows from its node when the level loads; a name it does not know is IGNORED WITHOUT A MESSAGE, an unreadable value leaves the default, an out-of-range value is clamped.
    `bridge.object.xml` writes the live object back through the same serializer; this compares attribute by attribute (numbers within 0.001, comma lists member by member) and child by child:
    O001 an object the editor does not hold (error), O002 an attribute in the file the live object does not have (warning: ignored), O003 a value that differs (warning, both shown: clamped, not read, or another type),
    O004 an attribute only the live object has (information: the file predates it), O005 a child node that differs, O006 the editor's object count differs from the layer files' (information), O007 a name used twice.
    A component's instance `guid` is NOT compared: the editor makes a new one at every level load. Needs the editor
    with the level open."""
    try:
        return await asyncio.to_thread(obj_mod.check, level or None)
    except (obj_mod.ObjectCheckError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


import entity_check as ent_mod  # noqa: E402


@mcp.tool()
async def entity_check(level: str = "") -> dict:
    """The level's ENTITIES and their components' properties as the running ENGINE kept them, from the open editor, without the game. A layer file (layers/*.lyr) stores each entity's class and, per component, a
    <properties .../> element; when the level loads, a property the component does not have (a typo, or a member renamed in the C++ Reflect) is IGNORED WITHOUT A MESSAGE, an unregistered component type is dropped,
    an unregistered entity class makes no entity, and an unreadable value leaves the default. bridge.component.properties serialises each live component with the engine's own serialization; this compares it with the
    layer: N001 an entity the editor did not load (error), N002 a component the live entity lacks (error; the engine's own list of the ones it has), N003 a property in the file the component does not have (warning,
    says what it has), N004 a value that differs (warning, both shown; nested structures such as MeshScale are compared member by member), N005 an editor entity no layer holds (information), N006 not compared
    (legacy Lua <Properties>, a component type twice, a name with a space). Unsaved edits in the open editor show up as N004. level: the folder under Assets/levels; empty = the level the editor has open (a fixed level name would make every object read as missing while another level is open)."""
    try:
        return await asyncio.to_thread(ent_mod.check, level or None)
    except (ent_mod.EntityError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


# ---------------------------------------------------------------- world building (world_build.py): a level that is not Ashvale
import world_build as wb_mod  # noqa: E402


@mcp.tool()
async def new_level(name: str, resolution: int = 1024, unit_size: float = 1.0, terrain: bool = True) -> dict:
    """Create a NEW level and open it in the editor (the editor's own general.create_level; 0.6 s): flat terrain at 32 m, water at 16 m, no objects. The level that is open is SAVED first (leaving a modified level
    opens a blocking 'Save Level?' prompt). Nothing is overwritten: an existing name is refused. name: a letter, then letters/digits/_ (40 at most); resolution: samples per side, 128 to 4096; unit_size: metres per sample
    (0.25 to 16), so the terrain is resolution x unit_size metres square (1024 at 1.0 = 1 km). The new terrain draws BLACK until its colour texture is baked: terrain_shape, terrain_layers(action='bake'). Next steps
    for a game level: terrain_shape -> terrain_layers (add layers, bake) -> vegetation -> place_object / create_object with z from terrain_height -> level_validate / level_check -> save_level -> export_level."""
    try:
        return await asyncio.to_thread(wb_mod.new_level, name, resolution, unit_size, terrain)
    except (wb_mod.WorldBuildError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def terrain_shape(style: str = "hills", seed: int = 1, base_height: float | None = None, max_height: float | None = None, flat_zones: list[dict] | None = None,
                        erosion: int = 0, preview: bool = False, restore: str = "") -> dict:
    """Shape the OPEN level's terrain from a style, without the game: style flat | hills | mountains | island; seed picks the variation; base_height / max_height are the lowest and highest ground in metres
    (default: a few metres over the water, and a range that scales with the terrain's size so a small map is not a cliff); flat_zones = [{"x", "y", "radius", "blend", "height"}] in WORLD METRES cuts level platforms for
    buildings (height defaults to the ground's mean there; blend defaults to radius/2); erosion = droplets for drainage valleys (0 = none; ~250 per 1,000 samples is visible). preview=True reports the numbers
    (min/max, share steeper than 30 degrees, each zone's real height and spread) and changes nothing. The old ground is BACKED UP first (engine_data/terrain_backups): pass restore=<that path> to put it back.
    The heightmap is indexed [x, y] with x = world X. Applies unless preview=True: snapshot_project first (only the heights are backed up, not layers or objects). Not saved (save_level); the ground draws black or flat-coloured until terrain_layers(action='bake')."""
    try:
        if restore:
            return await asyncio.to_thread(wb_mod.restore_heights, restore)
        return await asyncio.to_thread(wb_mod.shape_terrain, style, seed, base_height, max_height, flat_zones, erosion, preview)
    except (wb_mod.WorldBuildError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def terrain_height(points: list[list[float]] | None = None) -> dict:
    """Ground height of the OPEN level at world points [[x, y], ...] in metres (bilinear on the heightmap, from the engine's own data; the terrain starts at 0, 0): the z to give place_object / create_object so a
    building or a lamp stands ON the ground and not in it. Each answer says `inside` (false = outside the terrain, clamped to the edge). With no points: the terrain's numbers (samples, unit, size in metres,
    min / max / average height, water level, the max-height setting)."""
    try:
        if not points:
            return await asyncio.to_thread(wb_mod.terrain_info)
        return {"heights": await asyncio.to_thread(wb_mod.height_at, points)}
    except (wb_mod.WorldBuildError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def terrain_layers(action: str = "list", material: str = "", texture: str = "", altitude: list[float] | None = None, slope: list[float] | None = None,
                         repeat: float | None = None, export: bool = True, resolution: int = 2048, multiplier: float = 8.0) -> dict:
    """The OPEN level's ground layers. action 'list' = the layers (index, name, material, rules). 'add' = create a layer (the editor's terrain.create_layer), optionally with a `material` (a .mtl under Assets,
    e.g. materials/ashvale/grass) or a `texture`, `altitude` = [min, max] metres and `slope` = [min, max] degrees (the layer shows only there). 'bake' = paint every layer into the terrain's colour texture
    (generate_surface_texture: reports the measured coverage per layer, a layer near 0 % never opened) and, with export=True, compile it at `resolution` (power of two, 512..16384) with brightness `multiplier`
    (export_texture: WITHOUT IT a created level's terrain is BLACK). Not saved (save_level). Check the result by looking: debug_view / take_screenshot, or compare_shots."""
    try:
        if action == "list":
            return {"layers": await asyncio.to_thread(wb_mod.layers_list)}
        if action == "add":
            return await asyncio.to_thread(wb_mod.layers_add, material or None, texture or None, None, altitude, slope)
        if action == "bake":
            return await asyncio.to_thread(wb_mod.bake, repeat, export, resolution, multiplier)
        raise wb_mod.WorldBuildError("action is list, add or bake")
    except (wb_mod.WorldBuildError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def vegetation(action: str = "list", mesh: str = "", index: int = -1, spacing: float = 8.0, size: float = 1.0, size_var: float = 0.0, min_alt: float | None = None,
                     max_alt: float | None = None, min_slope: float | None = None, max_slope: float | None = None, area: list[float] | None = None, params: dict | None = None) -> dict:
    """Vegetation brushes of the OPEN level (trees, grass, rocks painted over the terrain). 'list' = the brushes (index, mesh, density, size, rules, instance count). 'add' = a brush from a `mesh` (.cgf path under Assets,
    the GameSDK sample's paks or the engine's, e.g. objects/natural/trees/ashvale_pine.cgf; the path is CHECKED FIRST, the engine makes a broken brush for a mesh that failed to load and nothing removes a brush);
    spacing = average metres between instances (smaller = denser), size/size_var, and min_alt/max_alt (m) / min_slope/max_slope (degrees) where it may grow. 'paint' = scatter brush `index` over `area`
    = [x1, y1, x2, y2] in world metres, honouring its rules; 'clear' = remove its instances there. 'params' = change brush `index` with `params` {size, sizevar, density, bending, airadius, hideable, castshadow,
    elevmin, elevmax, slopemin, slopemax, hidden}. Not saved (save_level)."""
    try:
        if action == "list":
            return await asyncio.to_thread(wb_mod.veg_list)
        if action == "add":
            return await asyncio.to_thread(wb_mod.veg_add, mesh, spacing, size, size_var, min_alt, max_alt, min_slope, max_slope)
        if action in ("paint", "clear"):
            if not area or len(area) != 4 or index < 0:
                raise wb_mod.WorldBuildError("paint / clear need `index` and `area` = [x1, y1, x2, y2] in world metres")
            return await asyncio.to_thread(wb_mod.veg_paint, index, area[0], area[1], area[2], area[3], action == "clear")
        if action == "params":
            if index < 0:
                raise wb_mod.WorldBuildError("params needs the brush `index`")
            return await asyncio.to_thread(lambda: wb_mod.veg_params(index, **(params or {})))
        raise wb_mod.WorldBuildError("action is list, add, paint, clear or params")
    except (wb_mod.WorldBuildError, OSError, ValueError, TypeError) as exc:
        return _refusal(exc)


@mcp.tool()
async def save_level() -> dict:
    """Save the level the editor has open (the editor's own general.save_level): terrain, layers, vegetation, objects. Does NOT write the navmesh or level.pak: export_level does. Answers {saved, level, objects}."""
    try:
        return await asyncio.to_thread(wb_mod.save_level)
    except (wb_mod.WorldBuildError, OSError) as exc:
        return _refusal(exc)


import pbs_check as pbs_mod  # noqa: E402


@mcp.tool()
async def pbs_check(paths: list[str] | None = None, include_imports: bool = False) -> dict:
    """MATERIALS against the manual's PHYSICALLY BASED SHADING rules (no editor, no game; about 1 s): S001 a Diffuse colour channel above 1.0 (an albedo above 100%), S002 a textured material whose Diffuse colour is not 255,255,255
    (the manual keeps it white and puts the colour in the texture), S003 a non-metal whose Specular colour is outside sRGB 40..60 (error above 80, 'never higher'), S004 a Specular colour that is not white while a specular MAP
    is used (it multiplies the map), S005 a metal (surface type mat_metal*, a metal name, or Specular above 150) whose Specular is below sRGB 180 or whose Diffuse is not black/very dark, S006 Shininess below 255 with a _ddna
    normal map, S007 a coloured Specular on a non-metal. The engine's own shader settles what Smoothness means (Illum.cfx: attribs.Smoothness = MatSpecColor.w * GetSmoothnessTex(...)): the FINAL smoothness is the material's
    Shininess/255 TIMES the alpha of the _ddna map, so Shininess 50 with a map scales the map to 20% of itself (a material without a map uses the slider alone). Only the Illum shader is judged. The findings are deviations from guidelines, not bugs. include_imports adds the GameSDK materials under Assets/objects."""
    return await asyncio.to_thread(pbs_mod.check, paths or None, bool(include_imports))


import decal_lab as decal_mod  # noqa: E402


@mcp.tool()
async def decal_materials(text: str = "", limit: int = 40) -> dict:
    """The GameSDK sample's DECAL materials (159: mud 35, dirt 32, rubbish 16, shadow 12, blood 10, water 8, metal 7, concrete 6, paint 6, vegetation 5, glass 4, rock 4, snow 3, footsteps 2, ice 2, sand 2, wood 2 ...),
    read from its paks (nothing is copied): path, folder, textures and public parameters (DecalDiffuseOpacity, DecalFalloff, DecalAlphaMult). 158 are Illum materials with the %DECAL flag the manual requires. text:
    words matched against the path and texture names ('footprint', 'mud 12', 'blood'). To use one, copy it into the project with `python level/import_gamesdk_asset.py Materials/decals/<folder>/<name>.mtl` (a local copy
    of the sample's files with every texture; nothing is downloaded), then look at it with decal_preview. A material is a decal only because of %DECAL in its StringGenMask."""
    rows, total = await asyncio.to_thread(decal_mod.search, text, max(1, min(limit, 200)))
    return {"matches": total, "shown": len(rows), "materials": rows, "folders": (await asyncio.to_thread(decal_mod.load))["folders"]}


@mcp.tool()
async def decal_preview(material: str, x: float, y: float, projection: int = 2, deferred: int = 0, size: float = 3.0, lift: float = 0.3) -> dict:
    """LOOK at a decal in the EDITOR viewport (the game is not run): two control pictures of the ground at (x, y) with no decal, then a scratch Decal object with `material` (a project path without extension, for
    example materials/decals/footsteps/footstep_dirt: it must be in the project, see decal_materials), ProjectionType (0 Planar, 1 StaticObjects, 2 Terrain, 3 both), Deferred, `size` metres, `lift` metres above the ground,
    one picture, then the object is deleted (the count is checked). Returns the picture, the changed pixels against the two-control noise and the mean colour there. MEASURED in this editor: a decal over terrain
    darkens it clearly within ProjectionDepth (1 m) of the ground; with the decal 2 m above the ground NOTHING draws for any type including Planar; all four projection types drew the same over terrain; a decal over
    a brush slab (default or project material) did not draw: the manual's differences between the types are NOT reproduced in the editor viewport. Needs the editor open; takes about 20 s."""
    full = os.path.join(decal_mod.ASSETS, material.replace("/", os.sep) + ".mtl")
    if not os.path.isfile(full):
        return _refusal(RuntimeError("the material %s is not in the project's Assets: copy it first with `python level/import_gamesdk_asset.py Materials/decals/<folder>/<name>.mtl`" % material))
    try:
        return await asyncio.to_thread(decal_mod.preview, material, float(x), float(y), int(projection), int(deferred), float(size), float(lift))
    except (decal_mod.DecalError, RuntimeError, OSError, ValueError) as exc:
        return _refusal(exc)


import gamesdk_audio as gsa_mod  # noqa: E402


@mcp.tool()
async def gamesdk_audio(text: str = "", real_only: bool = True, limit: int = 40, refresh: bool = False) -> dict:
    """The GameSDK sample's SDL-MIXER audio as a catalog (the middleware this project plays through), read from its audio.pak (nothing is copied): 611 Audio Controls Editor triggers in 37 libraries, 203 with a REAL sample
    and 408 that only play the silent placeholder do_nothing.ogg (a trigger that exists and plays nothing). Real content: footsteps by surface (40 triggers: concrete, dirt, fabric, grass, grass_tall, gravel, leaves, metal
    chainlink/grate/sheet/thick, mud, wood hollow/solid ...), the woodland ambience loops (18-31 s), projectile impacts, weapons (pistol, rifle, shotgun, hmg, grenade), player and NPC foley. NOT real: the 105 per-surface
    collision sounds (p_col_mat), melee (all 13 melee triggers incl. the per-surface hits), vehicles, UI: those live in the Wwise and FMOD sets. Each row: trigger, library, folder, events (sample, seconds, bytes, volume,
    loop, whether the sample is already in the project). text: words matched against trigger, library, folder and sample ('footstep' is understood as the sample's 'fts'; 'wood', 'woodland amb', 'pistol'). To use a
    sample, copy it from the pak into Assets/audio/sdlmixer/assets and define a trigger in a project ACE library (audio_triggers checks the names)."""
    rows, total = await asyncio.to_thread(gsa_mod.search, text, bool(real_only), max(1, min(limit, 200)), bool(refresh))
    d = await asyncio.to_thread(gsa_mod.load)
    return {"matches": total, "shown": len(rows), "triggers": rows, "summary": {k: d["summary"][k] for k in ("triggers", "real", "placeholder_only", "assets")}}


import engine_tests as etest_mod  # noqa: E402


@mcp.tool()
async def engine_tests(run: bool = True, exe: str | None = None, filter: str | None = None, accept_baseline: bool = False) -> dict:
    """The engine's OWN unit tests: Crytek ships seven GoogleTest programs in the installed engine's bin/win_x64 (CryCommonUnitTest 95 tests, CrySystemUnitTest 17, CryEntitySystemUnitTest 8, CryAISystemUnitTest 10 (navmesh
    geometry), CryAnimationUnitTest 8 (incl. the chrparams animation-list patterns), Cry3DEngineUnitTest 6, CryAudioSystemUnitTest 0): 144 tests, all passing, about half a second in total. They start neither the editor nor the
    game. run=false lists the tests only. exe: one of the seven names (anything else is refused: nothing else can be run); filter: a GoogleTest filter such as CryStringTest.* . The full run is compared with a saved baseline
    (engine_data/engine_tests_baseline.json): a test that now fails, vanished or is new is listed, so a change to the environment (a driver, a Windows update, a rebuilt DLL) that breaks the engine's own tests shows up.
    accept_baseline=true saves a full run as the new baseline. Use it before and after anything that touches the machine's setup."""
    try:
        if not run:
            return {"executables": {e: {"suites": {k: v for k, v in (await asyncio.to_thread(etest_mod.list_tests, e)).items()}} for e in ([exe] if exe else etest_mod.EXES)}}
        r = await asyncio.to_thread(etest_mod.run, [exe] if exe else None, filter)
        out = {"ok": r["ok"], "passed": r["passed"], "failed": r["failed"], "executables": {k: {kk: vv for kk, vv in v.items() if kk != "tests"} for k, v in r["exes"].items()}}
        failed = {k: [t for t, d in v["tests"].items() if d["status"] != "ok"] for k, v in r["exes"].items() if any(d["status"] != "ok" for d in v["tests"].values())}
        if failed:
            out["not_ok"] = failed
        if not exe and not filter:
            base = etest_mod.baseline()
            out["baseline"] = {"saved": base["saved"], "differences": etest_mod.compare(r, base)} if base else "no baseline yet: accept_baseline=true saves this run"
            if accept_baseline:
                etest_mod.baseline(r, accept=True)
                out["baseline_saved"] = True
        return out
    except (etest_mod.TestsError, OSError) as exc:
        return _refusal(exc)


import project_audit as audit_mod  # noqa: E402


@mcp.tool()
async def project_audit(details: bool = False, editor_free: bool = False, accept_baseline: bool = False) -> dict:
    """ONE CALL that runs every engine-owned validator of this server over the project (about 5 seconds with the editor open, no game): asset lint, Crytek's own 144 unit tests, the PBS material rules (advisory:
    its errors count as warnings), the audio libraries against the code, the time-of-day presets, and, from the open editor, the engine's own loaders for materials, characters, Mannequin databases, the behaviour tree, the
    UQS query, flow graphs, the Dynamic Response System, the character animation data, the .cryproject files, the Track View sequence, the particle effects and the editor's 'Check Level for Errors'. Returns each
    validator's errors/warnings/info and what it checked, which validators could NOT run and why (a validator that raises never stops the others; with the editor closed the 12 editor ones are listed as not run), and what is
    NEW or GONE since the baseline (engine_data/audit_baseline.json: errors and warnings by code, place and text). accept_baseline=true saves this run as the baseline: do it after you have looked at the findings
    and accept them. details=true adds the findings (errors and warnings, 8 per validator). editor_free=true skips the validators that need the editor. Use it before packaging, after any asset or code change and after
    an engine or driver update.."""
    try:
        r = await asyncio.to_thread(audit_mod.run, None, bool(editor_free))
    except OSError as exc:
        return _refusal(exc)
    base = audit_mod.baseline()
    out = {"ok": r["ok"], "errors": r["errors"], "warnings": r["warnings"], "validators_run": r["ran"], "editor_open": r["editor_open"], "not_run": r["not_run"],
           "validators": [{k: v.get(k) for k in ("name", "ran", "errors", "warnings", "info", "seconds", "detail", "reason")} for v in r["validators"]]}
    if details:
        for v, full in zip(out["validators"], r["validators"]):
            v["findings"] = [f for f in full["findings"] if f["severity"] in ("error", "warning")][:8]
    if base:
        out["since_baseline"] = dict(audit_mod.compare(r, base), baseline_saved=base["saved"])
    else:
        out["since_baseline"] = "no baseline yet: accept_baseline=true saves this run"
    if accept_baseline:
        audit_mod.accept(r)
        out["baseline_saved"] = True
    return out


import env_check as env_mod  # noqa: E402


@mcp.tool()
async def env_variables(text: str = "", limit: int = 60) -> dict:
    """The TIME-OF-DAY (environment) variables, as the engine's own table defines them: all 145 (Sun, Fog, Volumetric Fog, Sky light, Night sky, Clouds, Sun rays, HDR, Color grading ...), each with group, display name, type
    (float or colour), default and the range the engine clamps to. Read from the engine source (EnvironmentPreset.cpp CTimeOfDayVariables::Reset + ITimeOfDay.h) and checked name by name against the running engine's
    list (145 of 145, same order). text: words matched against id, name, group ('fog density', 'moon'). Use it before authoring a preset: a value outside the range is CLAMPED, never reached."""
    rows = await asyncio.to_thread(env_mod.variables, text)
    return {"matches": len(rows), "shown": min(len(rows), max(1, limit)), "variables": rows[:max(1, min(limit, 200))]}


@mcp.tool()
async def env_check(paths: list[str] | None = None) -> dict:
    """ENVIRONMENT PRESETS (.env) against the rules of the engine's own loader, without the game or the editor. How a version-4 file is read (EnvironmentPreset.cpp:627): POSITIONALLY, the i-th <var> into variable i, so a
    missing, extra or reordered <var> shifts every later variable with no error; an older file is converted by id; every evaluated spline value is CLAMPED to the file's minValue/maxValue. Findings: E001 not a preset,
    E002 older version (converted), E003 the <var> sequence differs from the engine's 145 (misload), E004 a key value outside the range (clamped: never reached), E005 key times outside 0..144000 or out of order (a day
    is 144000, 6:00 is 36000), E006 a colour with keys in red and none in green/blue, E007 the file's range differs from the engine's table, E008 no Constants block. A known case: PARAM_NIGHSKY_MOON_COLOR_MULTIPLIER is 0.4 in the engine's own template while its range ends at 0.1, so the engine uses 0.1. paths: relative to Assets
    (default every .env in libs/environmentpresets). Note: the editor's CURRENT preset is the engine default (%ENGINE%/EngineAssets/Shading/default.env) after a restart; the game can switch presets itself."""
    try:
        return await asyncio.to_thread(env_mod.check, paths or None)
    except (env_mod.EnvError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
async def env_diff(a: str = "libs/environmentpresets/ashvale_clear.env", b: str = "libs/environmentpresets/ashvale_rain.env", hours: list[float] | None = None) -> dict:
    """Which time-of-day variables DIFFER between two presets, sampled at hours of the day (default 0, 6, 9, 12, 15, 18, 21; linear between keys, the engine uses Bezier with the stored tangents, so values are approximate
    but a difference is real). Default compares the project's clear and rain presets: 16 variables differ (sun colour and intensity, the three fog colours, volumetric fog density, sky light, cloudiness, cloud thickness and
    absorption, sun rays, saturation). Each entry gives the variable, its group, at how many hours it differs and the two values at each such hour."""
    try:
        d = await asyncio.to_thread(env_mod.diff, a, b, tuple(hours) if hours else (0, 6, 9, 12, 15, 18, 21))
    except (env_mod.EnvError, OSError, RuntimeError, ValueError) as exc:
        return _refusal(exc)
    return {"a": a, "b": b, "differing": len(d), "variables": d}


# Tool families for tool_guide: every registered tool must be listed here (test_mcp_tools fails on an unclassified one, so a new tool forces a decision).
FAMILIES = {
    "start here": ["tool_guide", "query_knowledge", "health_check", "project_audit", "wait_for_editor", "project_overview"],
    "learn the engine (docs and source)": ["search_manual", "read_manual_page", "manual_toc", "cvar_search", "cvar_info", "spec_group", "editor_api", "editor_panes", "schematyc_search", "schematyc_info",
                                           "shader_info", "shader_search", "asset_info", "asset_report", "asset_metadata", "debug_views", "debug_view", "particle_search", "particle_info", "pfx_check", "pfx_write", "pfx_preview", "render_tour", "sequence_check", "audio_triggers", "editor_commands", "nav_reach", "nav_map", "mannequin_check", "mannequin_clips", "bt_check", "bt_nodes", "uqs_check", "uqs_catalog", "level_stats", "level_validate", "rc_presets", "rc_options", "character_check", "material_check", "flow_nodes", "flow_check", "env_variables", "env_check", "env_diff", "anim_check", "anim_sample", "cryproject_check", "ui_check", "entity_check", "level_check", "visarea_check", "frame_profile", "editor_panel", "object_check", "editor_notify", "editor_undo", "drs_check", "pbs_check", "decal_materials", "decal_preview", "gamesdk_audio", "engine_tests"],
    "verify without the editor": ["lint_assets", "shoot_game", "get_shots", "compare_shots", "visual_check", "visual_status", "audio_report"],
    "protect the work": ["snapshot_project", "list_snapshots", "diff_snapshot", "restore_files"],
    "long jobs": ["job_kinds", "test_plan", "start_job", "job_status", "read_job_log", "list_jobs", "stop_job"],
    "make assets": ["texture_recipes", "make_texture", "write_material", "build_mesh", "preview_voice", "render_voices", "make_music"],
    "level objects (editor open)": ["list_objects", "list_entities", "place_object", "delete_objects", "object_types", "create_object", "object_properties", "spline_points", "add_component", "link_area_target", "flow_graph_add", "gametoken_check", "terrain_hole", "spawn_archetype", "list_archetypes", "teleport_entity",
                                    "create_collision_wall", "goto_position"],
    "build a world (editor open)": ["new_level", "terrain_shape", "terrain_height", "terrain_layers", "vegetation", "save_level"],
    "terrain (editor open)": ["generate_terrain_height", "list_terrain_layers", "select_terrain_layer", "set_terrain_layer_texture"],
    "navigation and AI (editor open)": ["create_navigation_area", "create_ai_path", "create_nav_seed_point", "regenerate_navigation", "set_ai_debug", "plan_character_collider"],
    "editor and engine control": ["server_info", "read_output", "ping", "run_console_command", "execute_editor_command", "execute_python", "execute_python_script", "get_cvar", "set_cvar",
                                  "take_screenshot", "list_windows", "list_dialogs", "click_dialog_button", "load_level", "export_level"],
    "study and inspect": ["study_level", "study_gamedata", "inspect_level", "outline_asset_xml", "read_asset_file", "entity_class_properties", "component_properties",
                          "get_entity_physics", "sample_entity_physics"],
    "project selection": ["project_info", "use_project"],
}


@mcp.tool()
def server_info() -> dict:
    """About THIS running MCP server: when it started, its pid, and which of its modules were EDITED after it started (the running server keeps the old code until the MCP connector is restarted:
    `restart_note` says "restart the MCP connector to load: ..." with the files). Also: whether auto-recovery is on, whether the editor was closed on purpose (marker file), how many times this server
    brought the editor back, the state of the catalog dumps (stamped against the installed plugin build), and how many saved tool outputs exist. Reads only."""
    return server_support.server_info()


@mcp.tool()
def read_output(id: str, key: str = "", offset: int = 0, limit: int = 50) -> dict:
    """Page through a tool result that was too big to return (over ~20,000 characters): the tool answered with `output_truncated`, an `output_id` and the saved file. key: a path into the saved result
    ('items', 'items.3', 'rows.0.name'; empty = the top level). A list is paged by items, a dict by keys, a text by lines: offset and limit select the page; `next_offset` says where the next page
    starts (null at the end). A single item that is itself too large comes back described: give a deeper key. Files are in job_runs/out/."""
    try:
        return server_support.read_output(id, key, offset, limit)
    except (server_support.OutputError, OSError, ValueError) as exc:
        return _refusal(exc)


@mcp.tool()
def tool_guide(family: str = "") -> dict:
    """The map of every tool in this server, by family, each with the first sentence of its description. family: part of a family name to show only that one ('verify', 'jobs',
    'make', 'objects', 'terrain', 'navigation', 'editor', 'study', 'start'). Built from the registered tools themselves; a tool no family claims is listed
    under 'unclassified'."""
    tools = {t.name: (t.description or "") for t in mcp._tool_manager.list_tools()}
    first = lambda text: (text.strip().split("\n\n")[0].replace("\n", " ").strip()[:130])
    out, seen = {}, set()
    for fam, names in FAMILIES.items():
        if family and family.lower() not in fam.lower():
            seen.update(names)
            continue
        out[fam] = [{"tool": n, "what": first(tools[n])} for n in names if n in tools]
        seen.update(names)
    leftover = sorted(set(tools) - seen)
    if leftover and not family:
        out["unclassified"] = [{"tool": n, "what": first(tools[n])} for n in leftover]
    return {"tools": len(tools), "families": out}


server_support.install(mcp)         # bounded output, worker threads for blocking tools, the relaunch report, the module snapshot behind server_info


if __name__ == "__main__":
    mcp.run(transport="stdio")
