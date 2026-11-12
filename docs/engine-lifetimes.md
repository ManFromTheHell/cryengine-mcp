# The four lifetimes of a CRYENGINE session

CRYENGINE does not have one lifetime. It has four, nested and overlapping, and each one destroys a
different part of the world while leaving the rest looking intact. Most of the hard bugs recorded
in this project's [knowledge base](knowledge-base.md) have the same cause: code held a pointer, a
registration, an entity id, a baked file or a bound singleton across a boundary that invalidated it.

This page is the model; [troubleshooting.md](troubleshooting.md) is the list of symptoms. The
examples are from game code written against CRYENGINE 5.7.1 LTS and are given as illustrations of
the mechanism, not as engine documentation.

| boundary | what crosses it | what is destroyed | how it looks when you get it wrong |
|---|---|---|---|
| **Frame** | everything | nothing | jitter, one-frame lag |
| **Game mode** (enter and leave, Ctrl+G) | the level, editor objects, your component instances | spawned entities, runtime-created components, AI registrations, the local player binding | works the first time, breaks the second |
| **Level reload** (open a level, reload, export with reload) | files on disk | every entity, every loaded resource (particle effects, materials), the navmesh in memory | a crash inside an engine update, minutes later |
| **Editor restart** (crash, deploy, watchdog) | files on disk | everything in memory, plus unsaved and unexported work: the navmesh, environment (time-of-day) edits, layer changes | "it worked before I restarted" |

## 1. The frame

The only boundary that destroys nothing, and the only one where ordering still matters.

- **Read state after the thing that writes it.** A held item that lagged behind the camera was
  positioned from the camera's world matrix, which holds last frame's value. Making it a child of
  the player entity and writing a local transform removed the lag, because the engine composes
  parent and local transforms at the same moment for both.
- **The editor ticks only while it draws.** A menu, a modal dialog, a minimised window or a long
  synchronous script stops every update, including the bridge's command queue. The plugin therefore
  drains its queue from a Win32 timer as well, and handles screenshots, window control and input
  "out of band" on its network thread.
- **Some engine systems update once per frame.** A character's animation is updated once per frame;
  a second update in the same frame is skipped, so code that samples poses must advance one frame
  between samples (`trap-animation-sampler-one-update-per-frame`).

## 2. Game mode

Entering game mode spawns a second population of objects on top of the editor's, and leaving it
destroys that population. This is where "works once" bugs come from.

What is destroyed: entities spawned with `ENTITY_FLAG_NO_SAVE`, components created at run time, AI
registrations (movement, collision avoidance, perception), and any binding the game made to the
player.

Four patterns seen in practice:

- **A pointer to a component that a rebuild deletes.** A function that rebuilt an entity's owned
  components removed every static-mesh component, which included a held weapon's mesh, but nulled
  only three of four pointers. The second entry into game mode wrote through freed memory and
  crashed the editor inside the animation update.
- **An entity that the session removes.** A held item spawned with `NO_SAVE` was hidden and its
  handle dropped. The orphan stayed parented to the player and the next session built a second
  one beside it. Remove it, rebuild it on demand, and hold it by `EntityId` that you resolve each
  time, never as a raw pointer.
- **A binding that only the first session makes.** A game singleton's player pointer was assigned
  from a revive flow that runs once. From the second session it was null, so respawn placed nobody
  and left the player at zero health, where damage returns early ("already dead") and regeneration
  is skipped, so nothing could move the health again. Bind lazily when you need the object, and
  treat an impossible state as a bug to detect and repair.
- **Duplicated components.** A layer's saved component copies are deserialized after `Initialize`
  has created its own set. For navigation, the last component to process the start-of-game event
  owns the movement plan. Remove all copies and create one fresh set; keeping one is not enough,
  because removing a navigation component unregisters the entity for the survivor too
  (`npc-duplicate-components`).

**Rule:** at this boundary, drop every handle and rebuild. Test the second iteration; the first
passing proves nothing.

**Testing it:** `game.toggle_game_mode` returns `ok` when it has only queued the switch. The editor
carries it out on its next update, and it updates only while a Sandbox window is active, or when
`ed_keepEditorActive` was changed from 0 to 1 while the editor was running (the same value in
`user.cfg` does not take effect). Anything automated must check the state it asked for, using the
editor's own answer (`game.is_in_game_mode`), not the return value. A game-side tick counter is
not a safe substitute: it also stops when the game is merely frozen, and toggling on that evidence
switches a running game off (`editor-game-mode-toggle-deferred`).

## 3. Level reload

Everything from game mode, plus every loaded resource and every entity, including ones the editor
appears to still show.

- **A cached resource pointer is a time bomb.** A particle effect held in a function-level
  `static IParticleEffect*` became invalid when a level reload unloaded the particle library; the
  next use crashed the editor reading a near-null address. Look resources up on each use or
  re-resolve them at the boundary (`ashvale-particle-static`).
- **Baked data is a file, not a memory state.** The navmesh, the terrain texture and the
  time-of-day preset each live outside the level's in-memory state, and each is lost or stale in a
  different way (next section).
- **The editor may reopen a different level.** A recovery script that reopens "the configured level"
  reopened an old one after a crash, so a whole build phase was created in the wrong level, and
  answering the save prompt wrote it in. Check what the recovery path will open before trusting it.

## 4. Editor restart

The widest boundary: only files survive, and several important things are not files yet when you
assume they are.

- **The navmesh** exists in memory until the level is exported. Regenerate it
  (`ai.regenerate_agent_type_*`) and export the level, or it is gone with the process
  (`nav-inmemory`).
- **Time of day and fog live in the environment preset** (for example
  `%ENGINE%/EngineAssets/Shading/default.env`), not in the level. They survive a level reload and
  die with the editor, and a packaged game never sees editor edits at all, so a game that needs a
  particular atmosphere must apply it from its own code or ship its own preset
  (`traps-level-saved-preset-list-lost-vs-exported-pak`).
- **The terrain texture is compiled separately.** A newly created level has a stub `cover.ctc` with
  colour multiplier 0, and level export does not recompile it, so the terrain renders black however
  bright the layers are. The load log says so in one line: `RGB multiplier: 0, Texture indices: 5`
  against a healthy `8 / 341` in the author's tests
  (`trap-new-level-terrain-is-black-until-terrain-layers-bake`).
- **Loose assets are cached by the file system while the editor runs.** Overwriting one changes
  nothing until a restart; write a new file name instead
  (`trap-rebuilt-mesh-or-material-unchanged-editor-caches-loose-files-by-name`).

## The shape of the mistake

Each bug above is the same sentence: something held a reference across a boundary that invalidated
it. The type varies (pointer, entity id, registration, cached file, bound singleton); the mistake
does not. Three habits remove it:

1. **Own the rebuild.** At a boundary, assume everything you hold is gone. Re-resolve by id or by
   lookup, null what you cannot own, and rebuild on demand rather than reusing.
2. **Make the invalid state impossible or recoverable.** A player at zero health who can be neither
   damaged nor healed is not a rare edge case, it is a state with no exit. If the model allows it,
   something must notice and repair it.
3. **Test across the boundary, not up to it.** Enter game mode twice. Reload the level and play
   again. Restart the editor and check that the thing still exists. A test that runs once in a
   fresh session cannot see any of these bugs.

## What the engine will not tell you

The engine fails silently in a specific, learnable way: a call returns `true` and does nothing
(`terrain.flood_layer`, `object.generate_all_cubemaps`, saving a game without an actor), a
screenshot returns the last frame, a log line is written seconds after the event. So the closing
rule:

**A result is not evidence until the instrument has shown it can produce the opposite result.**

Examples of an instrument that could not:

- Counting warnings in the editor log right after a run showed zero, while the rotated copy of the
  log held hundreds.
- A foot-slide metric read 0.000 with a feature switched on and off, because it measured the
  request and not the motion.
- A cheat CVar changed in a Release build changes nothing, so an A/B through it reads zero
  (`trap-cheat-cvar-dead-in-release`). A frame cap makes every frame-time A/B read zero for the
  same reason.
- Equal bright-pixel counts with and without a GPU particle emitter meant the editor viewport
  does not draw GPU particles, not that the emitter was invisible in the game
  (`trap-gpu-particles-the-editor-viewport-draws-neither-effect-a-zero-without-a-control-2026-10-27`).
- A regression harness that has never failed has proven nothing. Break something on purpose and
  confirm the harness reports it, and break level state rather than a game flag that the test's own
  setup would reset.

Practical consequences for the tools here: read the value back after a write, compare against a
control, and prefer the tools that ask the engine itself (`entity_check`, `level_check`,
`material_check`, `mannequin_check`, `flow_check`) over a tool that only reports what it sent. Many
chunks in the knowledge base carry `verified_in: none` for exactly this reason: the thing was built
but never seen working.
