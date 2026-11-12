"""Put the editor in a known state for automation: active, and in (or out of) game mode.

Why this exists (observed on 5.7.1):

* `game.toggle_game_mode` does not switch anything. It stores a pending request
  (CGameEngine::RequestSetGameMode) that CGameEngine::Update carries out -- and the editor only
  calls Update from CCryEditApp::IdleProcessing while a Sandbox window is ACTIVE or the
  keep-active flag is set (CryEdit.cpp:885-923). The bridge answers from its own WM_TIMER pump
  either way, so `ok` means "queued". Minimised: 0 of 2 toggles entered in 5 s; restoring the
  window with no further command carried the queued one out within 0.02 s.
* `ed_keepEditorActive = 1` in user.cfg does NOT set that flag. user.cfg is applied before
  CGameEngine registers the CVar, the saved value is applied before the change callback is
  attached, and setting the same value again is a no-op. Matched test: CVar reading 1, set 1
  again -> a minimised toggle never entered; set 0 then 1 -> entered in 0.09 s and the game
  ticked while minimised.
* The Ashvale tick counter cannot tell "not in game mode" from "in game mode but frozen"
  (minimised in game mode: is_in_game_mode true, ticks 2059 -> 2059). Toggling because the
  counter is flat switches a frozen game OFF. `game.is_in_game_mode` is the arbiter.

The keep-active flag is held only while a harness runs and released afterwards: kept on
permanently the editor renders flat out in the background, which has led to AMD driver
crashes during long unattended runs.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import re
import time

import bridge_py as B          # only B.send is used; build_ashvale loaded an Ashvale heightmap at import


def _out(command, timeout=30):
    r = B.send(command, timeout=timeout)
    return r.get("output", "") if isinstance(r, dict) else str(r)


def in_game_mode():
    """The editor's own answer (GameEngine.cpp:1910), not a game-side proxy."""
    found = re.findall(r"Returned: (\w+)", _out("bridge.execute_command game.is_in_game_mode"))
    if not found:
        raise RuntimeError("game.is_in_game_mode gave no 'Returned:' line")
    return found[-1] == "true"


def hold_active():
    """Make the editor update while unfocused or minimised, for the length of a run. A live
    0 -> 1 change is what fires the CVar callback that sets CCryEditApp::m_bKeepEditorActive."""
    _out("bridge.cvar_set ed_keepEditorActive 0")
    _out("bridge.cvar_set ed_keepEditorActive 1")


def release_active():
    """Back to Crytek's default: idle when nobody is looking at the editor."""
    _out("bridge.cvar_set ed_keepEditorActive 0")


def set_game_mode(want, timeout=8.0):
    """Toggle at most once, then wait for the editor to report the wanted state. Returns
    seconds taken, or None if it never got there."""
    t = time.time()
    if in_game_mode() == want:
        return 0.0
    _out("bridge.execute_command game.toggle_game_mode")
    while time.time() - t < timeout:
        if in_game_mode() == want:
            return round(time.time() - t, 2)
        time.sleep(0.25)
    return None
