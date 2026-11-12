# Troubleshooting

Symptom, cause, fix. Each row cites the knowledge-base chunk that holds the full text and the
measurements; read it with `query_knowledge("<id>")` or `python rag/rag_query.py --id <id>`.
The chunks are field notes from one project on CRYENGINE 5.7.1 LTS (Windows); see
[knowledge-base.md](knowledge-base.md) for how far to trust them. A row marked "no chunk" comes from
the plugin's operating notes ([CLAUDE_BRIDGE_OPS.md](CLAUDE_BRIDGE_OPS.md)) only. Rows are observations on
CRYENGINE 5.7.1 LTS on Windows; a cited chunk's `status` and `verified_in` fields say whether it was
measured, read from source, or neither (many are unclassified). Re-check on your version.

Contents: [Connection and control](#connection-and-control) |
[Python and console text](#python-and-console-text) |
[Editor and assets](#editor-and-assets) | [Navigation](#navigation) |
[Terrain, levels and lighting](#terrain-levels-and-lighting) |
[Materials, textures and meshes](#materials-textures-and-meshes) |
[Packaging and Release builds](#packaging-and-release-builds) | [Animation](#animation) |
[Building C++ code](#building-c-code) | [Measuring and screenshots](#measuring-and-screenshots)

## Connection and control

| symptom | cause | fix | chunk |
|---|---|---|---|
| Every command times out ("timed out waiting for the editor main thread") | Commands run on the editor's main thread. The tick stops while a menu is open, and a command that opened a modal dialog or a long synchronous script holds the queue until it returns. (The plugin also drains its queue from a Win32 timer, so a minimised window or a nested modal loop no longer stalls it, and dialog verbs have their own queue.) | Call `list_dialogs`, then `click_dialog_button` with the button name. Close any open menu. If a script is running, wait: it recovers when the script returns | `trap-main-thread`, `bridge-dialog-verbs` |
| A command you did not expect opens a dialog and hangs the bridge | About 30 Python-API editor commands open modal dialogs (`terrain.generate_terrain`, `terrain.import_heightmap`, `general.open_level` and others; the list is in [CRYENGINE_BRIDGE.md](CRYENGINE_BRIDGE.md)). The command-manager command `ai.regenerate_agent_type_all` does too, and is not in that list | Do not call them unattended. Prefer the non-modal equivalent (`regenerate_navigation`, the terrain tools) or answer the dialog with `click_dialog_button` | `nav-regen-no-modal` |
| "Connection refused" or "connection closed" | The editor is not running, crashed, or has not finished starting; the plugin did not load | `wait_for_editor`. Confirm the plugin loaded by looking for its "Listening on 127.0.0.1:37778" log line. After a restart the editor opens an empty "Untitled" level, where commands answer normally but report zeros (for example terrain width 0): reload your level and re-apply any settings that live outside the level | `bridge-socket` |
| The connection is dropped at once, or the reply is "not a bridge request (expected a JSON object per line)" | The plugin accepts one JSON object per line and rejects a connection whose first non-blank byte is not `{` (an HTTP request from a browser, a probe or a client speaking another protocol). A request line over 256 MiB without a newline is dropped | Send `{"id": 1, "cmd": "..."}` followed by a newline; see [plugin-protocol.md](plugin-protocol.md). Do not point a browser or an HTTP client at port 37778 | no chunk |
| The tools answer, but about the wrong project (wrong assets, level or `Game.cryproject`) | The server picks its project from `CRYENGINE_PROJECT`, then `active_project.txt` (written by `use_project`), then the folder the server lives in; the folder must hold a `.cryproject` file. The choice is cached until the MCP server restarts. The editor has its own project, chosen when it started | Call `project_info` to see what the server chose, `use_project` or set `CRYENGINE_PROJECT` to change it, then restart the MCP server. Make sure the running editor has the same project open | no chunk; [limitations.md](limitations.md) |
| The editor stays down after a crash and tools keep failing | Nothing restarts the editor by default: `CLAUDE_AUTO_RECOVER` is 0 unless you set it to 1 in the server's environment, and the watchdog task exists only if you installed it | Start Sandbox yourself and `wait_for_editor`, or opt in (see [limitations.md](limitations.md), "Editor relaunch") | no chunk |
| `Unknown command` for a command that exists | Sandbox has three separate registries: the console (`run_console_command`: CVars and console commands), the editor command manager (`execute_editor_command`: `module.command`) and the Python interpreter (`execute_python`: `module.function`). Each only knows its own names | Try the other registries. Python is the widest | `cmd-three-registries` |
| A command returns ok and nothing changed | Several editor calls report success without acting (`terrain.flood_layer`, `object.generate_all_cubemaps`, and any mistyped `bridge.` verb on old plugin builds). `ok` often means only "did not throw" or "queued" | Always read the result back (position, file bytes, object list, level check). Use the tool that verifies for you (`entity_check`, `level_check`, `terrain_layers action=bake`) | `light-cubemap-silent-fail`, `trap-new-level-terrain-is-black-until-terrain-layers-bake` |
| `toggle_game_mode` returns ok but the editor never enters game mode | The call only queues the request. The editor carries it out on its next update, which runs only while a Sandbox window is active. Setting `ed_keepEditorActive` in `user.cfg` has no effect; changing the CVar from 0 to 1 while the editor runs does | Verify with `game.is_in_game_mode`, never with the return value. Keep the editor window active, or toggle the CVar live for the length of an automated run | `editor-game-mode-toggle-deferred`, `cmd-game-mode` |
| The editor freezes when a script it runs calls the bridge | `execute_python` runs on the editor's main thread; a script that starts a subprocess and waits, or that talks back to the bridge, waits for the thread it is occupying | Start such work detached (`subprocess.Popen(..., creationflags=0x208)` (DETACHED_PROCESS plus CREATE_NEW_PROCESS_GROUP)) and poll for its result. A detached child still dies with the editor, so use this only for work that finishes while the editor runs | `trap-main-thread` |
| A process started from the editor's Python dies when the editor exits, or `sys.executable` is `Sandbox.exe` | Sandbox runs inside a Windows job object; children inherit it. Inside the editor, `sys.executable` is the editor itself | Launch helpers that must outlive the editor through Task Scheduler (`schtasks`), not as children; build the interpreter path explicitly | `trap-sys-executable` |
| A child process started by the MCP server hangs | A subprocess inherits the protocol's standard input pipe | Start every child with `stdin=subprocess.DEVNULL` | `trap-mcp-stdio-child-inherits-protocol-stdin` |
| An object list is missing objects, and a delete-by-name step leaves some behind | `bridge.object.list` returns at most about 500 objects | Delete in batches and repeat until the list holds none; after saving, count the names in the layer file. Wrap each delete in try/except and re-list | `trap-bridge-object-list-is-capped` |
| Objects you made in a scratch step are now in your saved level | A graceful editor close saves the open level, including temporary objects you left in it | Wrap experiments in try/finally deletes, and snapshot first (`snapshot_project`) | no chunk |
| The rebuilt plugin DLL "did nothing" | The installed DLL is locked while the editor runs, plugins load once at startup (restarting the MCP connector does not reload C++), and a build can succeed without containing your change | Close the editor, copy the DLL, restart. Check the file contains your verb (search its bytes for the verb name) before deploying; see [CLAUDE_BRIDGE_OPS.md](CLAUDE_BRIDGE_OPS.md) | no chunk |
| An automated close or deploy closed the wrong window and the editor never exited | The plugin picked the largest visible window of the process, and a floating editor window (for example the Particle Editor opened by `particle.show_effect`) was larger than the main window | Fixed in the plugin's window selection. With an older build, close the stray window first (`bridge.window.list`, then `bridge.window.close`) | `trap-floating-editor-window-hijacks-the-deploy-close` |

## Python and console text

| symptom | cause | fix | chunk |
|---|---|---|---|
| Indentation errors, or a `SyntaxError` about an unterminated string, in `execute_python` | The command line is split on whitespace and rejoined with single spaces, so indentation is lost. (The engine's own Python entry point truncates a command near 1000 characters; the plugin works around that by running the script from a file, so length no longer matters) | Use `execute_python_script` (multi-line, sent through the `bridge.execute_python.b64` verb), or send one-line code | `trap-python-whitespace` |
| `%s`, `%d` or a path with `%` is mangled in script text or in the log, or the editor crashes on `%n` | The engine passes some already-formatted text to a printf-style logger as the format string. `%n` writes through a pointer that was never supplied | Use f-strings or concatenation. The plugin redirects script output so it does not reach the logger; engine-side messages are returned separately | `engine-format-string-bug`, `invalid-matrix-warning` |
| A huge result comes back doubled or truncated | The log is echoed twice | Write large results to a file from the script and read the file | no chunk |
| An editor Python function raises `TypeError`, `SystemError` or `AttributeError` | The embedded interpreter is Python 3.7. Some bindings are broken (for example `object.get_all_objects` rejects string arguments, `object.get_object_layer` raises `SystemError`), some documented names are absent (there is no `ai` module in the globals; `general.get_root_path` does not exist) | Check `dir(module)`. Use `list_objects` or `inspect_level` instead of the broken object getters | `trap-python-broken-bindings` |
| Per-frame `CryLog` output from your C++ code never shows up | The log file is held open and unflushed, and the bridge only captures what is logged during the one command it runs | Write diagnostics to a plain file with C stdio, opening and closing it on each write | `trap-crylog` |

## Editor and assets

| symptom | cause | fix | chunk |
|---|---|---|---|
| A rewritten loose file (texture, mesh, material) still shows the old content | The engine's file system caches a loose file it has already opened until the editor restarts. Overwriting the file changes nothing | Write the new content to a new file name and update references; delete the old file once nothing uses it | `trap-rebuilt-mesh-or-material-unchanged-editor-caches-loose-files-by-name` |
| A file you just wrote cannot be opened or replaced for a moment | The editor's asset scanner locks new files briefly | Retry after a short wait | `trap-just-written-file-locked-by-the-editor-asset-scanner` |
| A component property you set is ignored | A misspelt property name is dropped silently; a component copy saved in a layer is deserialized after `Initialize`, so a default component's saved properties arrive late | Read the property back with `component_properties` or `entity_check`, which compares the layer file with the engine's live values | `trap-component-properties-after-initialize` |
| An NPC or entity has two copies of a component and behaves oddly (for navigation, the last one to process the start-of-game event owns movement) | The layer's saved component copies are deserialized after the code created its own set | Remove all copies and create one fresh set; keeping one is not enough because removing a navigation component unregisters the entity for the survivor too | `npc-duplicate-components` |
| Adding a component twice creates two lights; a light setting did not persist | `add_component` is not idempotent, and one optics setting was not saved | Check `list_objects` / `component_properties` before adding and after | `trap-add-component-twice-adds-a-second-point-light-and-light-optics-enable-is-not-saved-2026-10-27` |
| Level export hangs on "pending unapplied changes to navigation data"; Escape does nothing | The export asks a Yes/No question with no cancel button while navigation regeneration requests are buffered | Apply the buffered changes first (`ai.regenerate_ignored`), then export; answer either export dialog with its default button | `trap-export-navigation-dialog-hang` |
| Exporting the terrain texture twice in one session stops the editor ("Error spawning SHLighting thread") | The exporter's auto-export mode forces high quality (16384 px, indirect lighting). The indirect-lighting pass starts one thread per CPU core that is never told to exit, and a second export can collide with the first export's thread registrations | Do not call the exporter in auto/high-quality mode more than once per editor session; use the full level export instead. If the "Crash - Proceed with Save?" prompt appears, answer No (never save after a crash) and restart the editor | `trap-export-texture-sh-threads` |
| Re-reading Mannequin files in the running editor hangs, then crashes it | Live NPC action controllers still hold the old database | After changing the Mannequin database or controller definition, restart the editor, then run `mannequin_check` with `reload=false` | `trap-mannequin-reload-after-change-crashes-editor` |
| A crash minutes after a level reload, inside an engine update | A pointer to a resource (for example a particle effect) was cached across the reload | Look resources up on every use. See [engine-lifetimes.md](engine-lifetimes.md) | `ashvale-particle-static` |
| A plugin that creates a character only to read its animation set freezes the editor with a fatal-error dialog | `ICharacterManager::CreateInstance` returns an instance with a reference count of zero, and `Release()` without a prior `AddRef()` is fatal | Hold the instance in a smart pointer (or `AddRef()`) before releasing it | `trap-character-instance-from-createinstance-has-refcount-zero` |

## Navigation

| symptom | cause | fix | chunk |
|---|---|---|---|
| Every destination is unreachable after an editor restart ("0 of N waypoints usable") | A navmesh that was only generated lives in memory. It survives only if the level was exported | Regenerate the navmesh and export the level. Also check that the level really contains a Navigation Area | `nav-inmemory`, `nav-area-missing-check` |
| Navmesh regeneration opens a modal dialog | `ai.regenerate_agent_type_all` asks for confirmation | Use `regenerate_navigation` (agent type index), which calls the non-modal per-type command | `nav-regen-no-modal` |
| The navmesh is empty although the area looks right in the editor | A Navigation Area is a prism that extends upward from its own Z by its Height. If its floor is above the terrain, nothing is voxelised | Put the area's Z below the terrain surface and give it enough height | `nav-area-prism` |
| A point inserted into a navigation area lands in the wrong place | `nav_insert_point` takes coordinates relative to the object, not world coordinates | Convert to object-relative coordinates | `nav-insert-point-relative` |
| Most of an area's navmesh disappears after adding a seed point | With no seed points in an area every island is accessible; with any seed, islands not connected to a seed are removed (when the area's "remove inaccessible triangles" option is on, the editor default). A seed on a rooftop or inside a rock removes the ground | Put seed points on the main walkable ground, or remove them | `nav-seed-prune-rule` |

## Terrain, levels and lighting

| symptom | cause | fix | chunk |
|---|---|---|---|
| Terrain renders black however bright the layers are | A newly created level has a stub terrain texture (colour multiplier 0). The terrain texture is compiled separately and level export does not recompile it | Bake: `terrain_layers action=bake` (generates the surface texture and compiles `cover.ctc`) | `trap-new-level-terrain-is-black-until-terrain-layers-bake` |
| Terrain height does not follow your parameters | The editor's `GenerateTerrain` always normalizes and resets the maximum height; its cover and sharpness arguments are accepted and never used (read from the editor source) | Design the heightmap elsewhere and install it (`terrain_height`, `terrain_shape`) | no chunk; [CRYENGINE_BRIDGE.md](CRYENGINE_BRIDGE.md) |
| A script-created terrain layer shows a green checkerboard close up | A layer made by script starts on the engine's default terrain material, and no Python or command-manager command assigns another | Give the layer a material (`terrain_layers action=add material=...`, or the plugin verb `bridge.terrain.set_layer_material`) | `terrain-layer-detail-material` |
| Terrain layer selection or edits seem lost after a reload | Layer selection does not survive a level reload, and terrain changes are invisible until the level is saved and reloaded | Select the layer again; save the level and reload to see terrain changes | no chunk; [CRYENGINE_BRIDGE.md](CRYENGINE_BRIDGE.md) |
| A vis area or portal is ignored | The 3D engine classifies an area by a word in its name (`visarea`, `portal`, `occlarea`); anything else is silently not an area | Name it accordingly and check with `visarea_check`. Also: a null answer from `GetVisAreaFromPos` can mean the area tree is stale, not "outdoors" | `traps-visarea-name-must-contain-visarea-engine-classifies-by-name`, `traps-visarea-probe-null-when-tree-stale-and-add-points-displacement` |
| Night looks as bright as day; a light cannot be seen at night | Three causes stack: `default.env` keys the sun at about 244,000 lux at 06, 12 and 18 h and the night only moves the light to the moon without dimming it; `r_HDREyeAdaptationMode` defaults to 2 (legacy), which ignores EV Min, EV Max and compensation; and the ambient probe is constant. Fixing one cause alone does not darken the night | Author your own time-of-day preset, list it in the level's preset list, and set `r_HDREyeAdaptationMode` to 1 in the `.cryproject` | `trap-bright-night-default-env`, `trap-playground-night-is-not-night-default-env-explains-the-projector-negative-2026-10-27` |
| Open-air scenes are overexposed | A preset copied from `default.env` keeps an auto-exposure compensation of +1.5 | Reset the compensation and verify with a grey card measurement | `trap-default-ev-compensation-overexposes-open-air` |
| The editor viewport looks brighter than the game | The viewport shows the ambient probe at full strength at every hour | Judge lighting in the game, not in the viewport | `trap-editor-viewport-has-no-game-ambient-scaling` |
| Time-of-day edits made in the editor are missing in the packaged game | Time of day lives in the environment preset file, not the level; the game reads the preset list exported with the level | Check the saved preset list against the exported level with `level_check`; apply atmosphere from code if the game needs it | `traps-level-saved-preset-list-lost-vs-exported-pak` |
| Smoke drifts along the ground, or a flame lies on its side | A legacy library effect is converted at load with a +Y emission direction; a particle component, unlike the old emitter, does not aim Y up, so +Y is the entity's forward axis | Orient the entity so its forward axis points the way the effect should go | `trap-legacy-particle-emits-along-y` |
| GPU particles show nothing in the editor viewport | The editor viewport draws neither GPU effect type; equal pixel counts with and without an emitter mean a dead instrument | Judge GPU particles in the game | `trap-gpu-particles-the-editor-viewport-draws-neither-effect-a-zero-without-a-control-2026-10-27` |
| The rain-ripple water shader never compiles | The stock water shader with rain ripples fails on D3D11 in 5.7.1 | Do not enable that shader variant | `trap-water-rain-ripples-shader-x4500` |

## Materials, textures and meshes

| symptom | cause | fix | chunk |
|---|---|---|---|
| Every surface of a mesh is a flat colour (the editor shows the red "Replace Me" texture) | A `.cgf` stores its material name at build time. If the material was renamed afterwards and the mesh not rebuilt, the engine finds no file and uses its default | Rebuild every mesh after changing the material name it points at; run `material_check` | `trap-cgf-names-a-material-that-does-not-exist` |
| A terrain material is black on a mesh | `materials/terrain/*` use the terrain-layer shader, which draws black on an ordinary mesh | Use a general (Illum) material for meshes | `trap-cave-floor-or-terrain-material-renders-black-on-a-mesh` |
| A displacement (`_displ`) texture compiles to the wrong format (BC3 instead of BC4) | The resource compiler ignores `/preset` on the command line for a TIFF with an alpha channel and auto-selects an albedo preset | Embed the preset in the TIFF as a Photoshop image-resource block (TIFF tag 34377), as Crytek's own TIFF plugin does; check with `lint_assets` | `trap-rc-ignores-preset-for-alpha-tiff-use-crytif-block` |
| `level_validate`: "undefined surface type, using mat_default" | A tool's hard-coded surface list did not match the project's `libs/MaterialEffects/SurfaceTypes.xml` | Use only surface types defined in the project's own `SurfaceTypes.xml` | `trap-write-material-surface-list` |
| A normal map looks fine by day and sparkles under a close light | Strength times texture size is too high | Lower the normal strength | `trap-normal-map-strength-sparkles-at-night` |
| `level_validate`: "Geometry file ... Failed to Load"; a brush draws nothing | The sample project's paks are not mounted by your project. A prop works only if it was copied into your `Assets/objects` | Copy the prop (and its materials and textures) into the project first | `trap-prop-not-copied-into-project` |
| Speech sounds destroyed after pitch shifting | Resample plus overlap-add smears synthetic speech | Resample only | `trap-pitch-shift-overlap-add-destroys-synthetic-speech` |

## Packaging and Release builds

| symptom | cause | fix | chunk |
|---|---|---|---|
| `map <level> s` (the C++ template's default) never loads in a Release build: black screen, zero entities | The `s` option makes a multiplayer listen server; the Release engine's networking is a pure client and drops its own connection. The failure is only logged by code compiled out of Release | Start the level without the server option | `trap-map-s-pure-client` |
| A cheat CVar changes nothing in the packaged game | Cheat CVars are dead in Release, so an A/B through one reads zero | Do not use cheat CVars to test a Release build | `trap-cheat-cvar-dead-in-release` |
| The packaged game has no shadows | The launcher registers `sys_spec` as 0 ("custom") and loads spec configs only for 1-4, so the shadow-cast spec stays at 0 and nothing casts shadows (the editor applies a spec) | Set `"sys_spec": "2"` (or another spec) in the project's `console_variables` in `Game.cryproject` | `trap-launcher-spec0-no-shadows`, `packaging-release-config` |
| Red "Replace Me" surfaces; the package job stops with "textures missing from the package" | The packager compiles nothing, and the resource compiler drops pre-split sample textures | Add the missing textures (`packaging/pak_textures.py`) | `trap-packager-compiles-nothing-presplit-dds`, `trap-package-missing-presplit-textures` |
| Audio missing in the package | The packager's copy list has `*.ogg` and no `*.wav` | Encode audio to Ogg | `trap-packager-drops-wav` |
| An edited XML file does not change in the package | A malformed XML (for example `--` inside a comment) is silently not converted, and the previous converted copy ships | Run `lint_assets`; fix the XML | `trap-rc-malformed-xml-ships-stale-copy` |
| Surfaces look wrong after changing a material | Any change to a material's flags or texture slots makes the shipped shader cache stale | Regenerate the shader cache; a shader tour is the check | `trap-shader-cache-stale-after-any-material-change` |
| The packaged game crashes on every quit | A thread started by the game DLL keeps the DLL loaded until process exit, so its static objects are destroyed after the engine modules they use are gone | Stop and join every thread the plugin started when the engine's shutdown events arrive, not in the destructor | `trap-plugin-threads-pin-dll-quit-crash` |
| The Release package fails or loads the wrong code | Package Build neither builds the game DLL nor exports the level | Build the game DLL for the configuration you package (for Release: `cmake --build <solution> --target Game --config Release`) and export the level first | `packaging-release-config` |
| A loading page flashes for a moment, then the screen is black for the whole level load | On level unload a running game unloads every UI element not flagged force-no-unload; the load-time renderer then has nothing to draw. At game start nothing has been unloaded yet, which hides the problem | Flag the loader's UI element force-no-unload (and show-at-loading-time); verify by measuring frames, not trace lines | `trap-level-change-black-screen-flashui-unload` |
| Menus are missing from engine screenshots | The engine's own screenshot has no 2D UI | Capture the window instead | `trap-engine-screenshot-has-no-2d-ui` |

## Animation

| symptom | cause | fix | chunk |
|---|---|---|---|
| A character is offset or turned by the first frame of a clip | The engine takes the locator's first frame (position and heading) from a clip; a locator that does not start at the origin facing zero moves every joint | Subtract the first-frame position and heading when building the clip | `trap-locator-first-frame-position-and-heading` |
| Sampling a clip's pose returns the same pose twice | The animation system updates a character once per frame; a second update in the same frame is skipped | One update per frame when sampling | `trap-animation-sampler-one-update-per-frame` |
| Knees and elbows end up several degrees off after compiling a clip (hands and feet centimetres off) | The template FBX has rotation limits active on hinge joints (calf, forearm); the FBX SDK inside the resource compiler silently clamps anything outside them | Put the twist on the thigh or upper arm instead of the hinge joint | `trap-fbx-rotation-limits-clip-hinge-joints` |
| An NPC plays a first-person or weapon clip | Clip names carry meaning (`_1p_` first person, weapon words mean a held weapon) | Choose clips by name | `trap-first-person-and-weapon-clips-on-npcs` |

## Building C++ code

| symptom | cause | fix | chunk |
|---|---|---|---|
| A new `.cpp` compiles alone but fails in the full build | The build merges sources (unity build); an anonymous-namespace name collides with another file's | Rename the symbol | `trap-unity-build-name-collision` |
| A warning breaks the build | Warnings are errors in this configuration | Fix the warning | `trap-warnings-errors` |
| `IConsole::AddCommand` does not compile | It is protected | Use the registration macro | `trap-addcommand-protected` |

## Measuring and screenshots

| symptom | cause | fix | chunk |
|---|---|---|---|
| A screenshot shows the previous frame or a half-resolved terrain | The editor renders lazily | Use the viewport capture with a settle delay | `bridge-socket` |
| A picture comparison reports no difference, or noise | Two captures of the same view differ by noise, and the same build does not draw the same picture on every launch | Measure a noise floor per region and compare against a control | `trap-shot-comparison-whole-picture-hides-change-in-noise`, `trap-visual-regression-same-game-differs-between-launches` |
| A screen grab contains another program's window | A screen grab copies whatever is in front | Capture only the game's own window | `trap-screen-grab-copies-another-window` |
| An A/B of a CVar reads zero difference | The game was running under a frame cap, or the CVar does nothing in this build | Remove the cap for the test, and show the instrument can read a difference first | `trap-cheat-cvar-dead-in-release`; see the control rule in [engine-lifetimes.md](engine-lifetimes.md) |
| Frame times swing between runs | Fullscreen vsync | Compare under identical settings; repeat | `trap-vsync-fullscreen-frame-times-unstable` |
| A test passes because it never looked | The test's own walk skipped the folder where its fixture lived; a regression test that has never failed proves nothing | Plant a known failure and confirm the test finds it | `lesson-fixture-under-skipped-folder-reads-as-clean` |
