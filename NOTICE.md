# Third-party notices

This repository is licensed under the MIT licence ([LICENSE](LICENSE)). That licence covers the code and documentation written for this project only. It does not cover, and this repository does not include, the third-party products named below. Each keeps its own licence and terms, which you must obtain and accept yourself.

This project is **not affiliated with, endorsed by or sponsored by Crytek, NVIDIA, Meta, Autodesk or any other company named here.** Product names and trademarks belong to their owners and are used only to say what this software works with.

## CRYENGINE (Crytek)

CRYENGINE is a product and trademark of Crytek GmbH. This project is a tool for CRYENGINE 5.7.1: the C++ plugin in `plugin/ClaudeBridgePlugin/` is compiled against, and loaded into, the CRYENGINE Sandbox editor, and the Python modules read and write CRYENGINE project files.

- You need your own CRYENGINE installation and licence. Obtain the engine and its source from Crytek under Crytek's terms. This repository does not contain the engine, its binaries, SDKs, shaders or tools (for example the resource compiler), and it does not download them.
- The plugin sources include CRYENGINE and Qt headers at build time. The headers are not included here.
- `engine-patches/` contains two small unified diffs (954 and 6,036 bytes) that change CRYENGINE build files (CMake). They hold only the changed lines with a few lines of context, not whole engine files. They are meant to be applied to a CRYENGINE source checkout that you are licensed to use:
  - `0001-build-claude-bridge-plugin.patch` adds an option that builds the plugin (`Tools/CMake/BuildSandbox.cmake`).
  - `0002-optional-cmake4-and-missing-fbx-sdk.patch` lets the build configure with CMake 4.x and without the Autodesk FBX SDK (`Tools/CMake/InitialSetup.cmake`, `Tools/CMake/modules/FbxSdk.cmake`, `Code/Sandbox/EditorQt/CMakeLists.txt`). It is optional.

  Whether distributing these diffs is compatible with the licence you hold for the engine source was not verified here; check it yourself.
- The CRYENGINE manual and the GameSDK sample project (levels, models, textures, sounds, code) are not included. Several tools read them from your own installation, or from an offline copy of the manual that you provide (`catalogs/manual.py` and `catalogs/docs_search.py` search such a copy; `catalogs/scrape_cryengine_docs.py` downloads pages of Crytek's online manual over HTTP when you run it by hand, and `catalogs/docs_link_index.py` are helpers for building one). Check Crytek's terms of use before running the downloader, copying documentation or sharing what it saves.
- `rag/cryengine_project_rag.jsonl` and the files in `docs/` are notes written for this project about how the engine behaves. They name engine settings, identifiers and messages; they are not a copy of the manual.

## Autodesk FBX SDK

Not included and not used by the Python code: `animation/fbx_binary.py` and `assets/mesh_fbx.py` read and write binary FBX with the Python standard library. The FBX files they write are converted by the engine's own resource compiler, which carries its own FBX SDK under Crytek's licence. Only the Sandbox build refers to the SDK. Patch 0002 lets the engine configure without it; FBX import inside Sandbox is then unavailable unless you install the SDK under Autodesk's terms.

## NVIDIA MotionBricks and Kimodo

The `motionbricks_*.py` and `kimodo_*.py` scripts, `animation/anim_oneshot.py`, `examples/ashvale/villager_anim.py` and `docs/animation-pipeline.md` convert motion produced by NVIDIA's MotionBricks (locomotion) and Kimodo (text-to-motion) models into animation clips for CRYENGINE. They are optional.

- The models, weights, Python packages and generated motion are not included. You obtain and run them yourself under NVIDIA's terms, in a separate Python environment (the scripts name the interpreter they expect).
- Generated motion data may be subject to the licence of the model that produced it.

## Meta Llama

Llama is mentioned only in `docs/animation-pipeline.md` and in a few knowledge-base chunks in `rag/cryengine_project_rag.jsonl`: they describe running Kimodo's text encoder through a locally built llama.cpp with Meta Llama 3 weights, a gated download that requires accepting Meta's licence. No code in this repository loads Llama and no weights are included.

## Piper text-to-speech

`assets/make_voices.py` and the tools `preview_voice` and `render_voices` can use the Piper offline voice engine and its voice models. Neither is included. If they are missing the tools say so and say what would have to be downloaded; they do not download it. Piper and each voice model have their own licences; check them before using generated speech.

## ffmpeg

`assets/make_music.py`, `assets/make_sounds.py`, `assets/make_voices.py`, `checks/audio_report.py`, `checks/asset_lint.py`, `checks/voice_quality.py`, `screens/shot_compare.py` and `server.py` call `ffmpeg` (and in two modules, `checks/audio_report.py` and `screens/shot_compare.py`, `ffprobe`) from `PATH`, for example to encode Ogg Vorbis or to scale screenshots. ffmpeg is not included. Its licence depends on the build you install.

## Python packages

Installed by you from PyPI under their own licences. `mcp` (the Model Context Protocol Python SDK) and `numpy` are required (both are listed in `requirements.txt`). Some optional scripts use other packages (for example PyTorch in the MotionBricks scripts). None is bundled.

## Content in this repository

- It contains no models, voices, music, textures or game assets from third parties.
- Most tests build their fixtures in temporary folders while they run. Some tests read data from an engine installation or a game project that you provide.
