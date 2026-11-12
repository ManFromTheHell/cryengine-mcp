"""Self-test for the ClaudeBridge Sandbox plugin.

Runs OUTSIDE Sandbox and talks to the plugin over its TCP port. That is not a
detail -- a test driven from inside Sandbox's own Python would deadlock, because
the interpreter runs on the main thread inside the very queue-drain the test's
commands need in order to be serviced.

Usage:
    python bridge/bridge_selftest.py [--destructive] [--json OUT.json] [--md OUT.md]

Default runs only checks that leave the level as they found it. Anything that
regenerates terrain, repaints vegetation or rewrites a layer texture is behind
--destructive, because those are slow and not reversible from here. So is
the "dialog" group (it opens the navigation-rebuild modal and answers No; if the
modal does not appear the navigation is rebuilt). Several checks also assume the
GameSDK sample's sdk_player character is available. Scratch files go to the
OS temp folder (BRIDGE_SELFTEST_SCRATCH overrides).

Exit code is the number of failures, so it can gate a deploy.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import argparse
import json
import os
import socket
import sys
import tempfile
import time

# Scratch files go to the OS temp folder (override with BRIDGE_SELFTEST_SCRATCH).
SCRATCH = os.environ.get("BRIDGE_SELFTEST_SCRATCH") or os.path.join(tempfile.gettempdir(), "bridge_selftest")
os.makedirs(SCRATCH, exist_ok=True)


def _scratch(name):
    return os.path.join(SCRATCH, name).replace(os.sep, "/")


HOST = "127.0.0.1"
PORT = 37778
TIMEOUT = 40.0


class Bridge:
    """One connection per command: the plugin spawns a thread per client and
    the protocol is one JSON line each way, so there is nothing to keep open."""

    def __init__(self, host=HOST, port=PORT, timeout=TIMEOUT):
        self.host, self.port, self.timeout = host, port, timeout
        self._id = 0

    def send(self, cmd):
        self._id += 1
        payload = json.dumps({"id": self._id, "cmd": cmd}) + "\n"
        with socket.create_connection((self.host, self.port), self.timeout) as s:
            s.settimeout(self.timeout)
            s.sendall(payload.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        line = buf.split(b"\n", 1)[0].decode("utf-8", "replace")
        return json.loads(line)


class Suite:
    def __init__(self, bridge, destructive=False):
        self.b = bridge
        self.destructive = destructive
        self.results = []

    def check(self, name, cmd, expect_ok=True, contains=None, verify=None, group="misc"):
        """verify: callable(parsed_output_or_raw_string) -> None, raising on failure.

        A verb that answers is not the same as a verb that works -- five separate
        silent-success bugs in this project reported ok and changed nothing -- so
        prefer passing `verify` over trusting `ok`."""
        started = time.time()
        entry = {"name": name, "group": group, "cmd": cmd, "ms": 0, "raw": ""}

        # One retry on a transport error. The editor is restarted immediately
        # before this suite runs and its listener comes up a beat after the
        # window does, so the very first connection can legitimately lose a
        # race; a second failure is a real failure.
        resp = None
        last_exc = None
        for attempt in range(2):
            try:
                resp = self.b.send(cmd)
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(1.5)
        if resp is None:
            entry.update(status="FAIL", ms=int((time.time() - started) * 1000),
                         detail="transport: %s: %s" % (type(last_exc).__name__, last_exc))
            self.results.append(entry)
            return None

        ok = bool(resp.get("ok"))
        out = resp.get("output", resp.get("error", ""))
        entry["raw"] = out if isinstance(out, str) else json.dumps(out)

        parsed = None
        if ok and isinstance(out, str):
            try:
                parsed = json.loads(out)
            except Exception:
                parsed = None

        if ok != expect_ok:
            entry.update(status="FAIL", detail="expected ok=%s, got ok=%s: %s" % (expect_ok, ok, str(out)[:300]))
        elif contains is not None and contains not in str(out):
            entry.update(status="FAIL", detail="expected %r in output: %s" % (contains, str(out)[:300]))
        else:
            entry.update(status="PASS", detail="")
            if verify is not None:
                try:
                    verify(parsed if parsed is not None else out)
                except Exception as exc:
                    entry.update(status="FAIL", detail="verify: %s" % exc)

        entry["ms"] = int((time.time() - started) * 1000)
        self.results.append(entry)
        return parsed if parsed is not None else out

    def skip(self, name, reason, group="misc"):
        self.results.append({"name": name, "group": group, "cmd": "", "status": "SKIP",
                             "detail": reason, "raw": "", "ms": 0})

    # ---------------------------------------------------------------- groups

    def core(self):
        g = "core"
        self.check("cvar read", "bridge.cvar_get r_DisplayInfo", group=g,
                   verify=lambda p: _need(isinstance(p, dict) and "value" in p, "no value field"))
        # NOT general.get_root_path -- reported to take the editor down. Any
        # check in this suite runs unattended against a live editor, so a command
        # with a crash report against it does not belong here however convenient
        # it looks.
        self.check("console command", "bridge.execute_command general.get_current_level_name", group=g)
        self.check("unknown verb rejected", "bridge.definitely_not_a_verb", expect_ok=False, group=g)

    def window(self):
        g = "window"
        info = self.check("window info", "bridge.window.info", group=g,
                          verify=lambda p: _need(p.get("client", [0, 0])[0] > 0, "zero-width client area"))
        # The pump fix means a minimised editor must still service the queue.
        self.check("minimise", "bridge.window.minimise", group=g)
        self.check("queue drains while minimised", "bridge.cvar_get r_DisplayInfo", group=g,
                   verify=lambda p: _need("value" in p, "no value while minimised"))
        self.check("restore", "bridge.window.restore", group=g,
                   verify=lambda p: _need(p.get("minimised") is False, "still minimised after restore"))
        return info

    def python(self):
        g = "python"
        self.check("python one-liner", "bridge.execute_python print(6*7)", group=g,
                   verify=lambda p: _need("42" in p.get("log", ""), "no 42 in log"))
        # The reason .b64 exists: newlines and indentation cannot survive a
        # whitespace-split command line.
        import base64
        src = "vals = []\nfor i in range(3):\n    vals.append(i * i)\nprint('squares', vals)\n"
        self.check("python multi-line via b64",
                   "bridge.execute_python.b64 " + base64.b64encode(src.encode()).decode(), group=g,
                   verify=lambda p: _need("squares [0, 1, 4]" in p.get("log", ""), "indented block did not run"))
        # Output containing printf specifiers used to be fed to CryLog AS a
        # format string: "%s:%d" came back as ":1" and "%n" crashed the editor.
        pct = "print('pct %s %d %n %% end')"
        self.check("printf specifiers survive output",
                   "bridge.execute_python.b64 " + base64.b64encode(pct.encode()).decode(), group=g,
                   verify=lambda p: _need("%s %d %n %% end" in p.get("log", ""),
                                          "format specifiers were eaten: " + repr(p.get("log", ""))[:120]))
        self.check("python traceback is captured, not thrown",
                   "bridge.execute_python.b64 " + base64.b64encode(b"raise ValueError('boom')").decode(), group=g,
                   verify=lambda p: _need("ValueError: boom" in p.get("log", ""), "traceback not captured"))
        self.check("raw fallback still works", "bridge.execute_python.raw print(1+1)", group=g,
                   verify=lambda p: _need("2" in p.get("log", ""), "raw path broken"))

    def files(self):
        g = "files"
        import base64
        path = _scratch("claude_selftest_scratch.txt")
        body = b"round trip \x00\x01\xff binary and text\r\n"
        self.check("file write", "bridge.file.write_b64 " + base64.b64encode(body).decode() + " " + path, group=g,
                   verify=lambda p: _need(p.get("match") is True, "write did not verify"))
        self.check("file read", "bridge.file.read_b64 " + path, group=g,
                   verify=lambda p: _need(base64.b64decode(p["b64"]) == body, "read back differs from written"))

    def camera(self):
        g = "camera"
        before = self.check("camera get", "bridge.camera.get", group=g)
        self.check("camera set", "bridge.camera.set 512 512 120 45 -20", group=g)
        self.check("camera moved", "bridge.camera.get", group=g,
                   verify=lambda p: _need(abs(_pos(p)[2] - 120) < 2.0, "camera z did not take: %s" % (_pos(p),)))
        self.check("camera look_at", "bridge.camera.look_at 600 600 150 512 512 40", group=g)
        if isinstance(before, dict):
            x, y, z = _pos(before)
            self.check("camera restored", "bridge.camera.set %g %g %g" % (x, y, z), group=g)

    def terrain(self):
        g = "terrain"
        self.check("terrain info", "bridge.terrain.info", group=g,
                   verify=lambda p: _need(p.get("heightmap", {}).get("width", 0) > 0,
                                          "no heightmap width in terrain info"))
        # list_layers answers with a bare JSON array, not an object.
        self.check("list layers", "bridge.terrain.list_layers", group=g,
                   verify=lambda p: _need(isinstance(p, list) and len(p) >= 1, "no terrain layers"))
        self.check("select layer 0", "bridge.terrain.select_layer 0", group=g)
        if self.destructive:
            self.check("generate height", "bridge.terrain.generate_height 6 4.0 2.0 0.6 128 3 0.995 1", group=g)
            self.check("generate surface texture", "bridge.terrain.generate_surface_texture", group=g)
        else:
            self.skip("generate height", "destructive: replaces the heightmap", g)
            self.skip("generate surface texture", "destructive and slow: rebakes terraintexture.pak", g)
            self.skip("set_layer_texture / delete_layer", "destructive: rewrites terrain layers", g)

    def objects(self):
        g = "objects"
        name = "ClaudeSelfTestObj"
        self.check("object types", "bridge.object.types", group=g)
        self.check("object list", "bridge.object.list", group=g)
        self.check("create object", "bridge.object.create EntityWithStaticMeshComponent 512 512 60 %s objects/sphere.cgf" % name, group=g)
        self.check("transform object", "bridge.object.transform %s 520 520 70 0 0 45 2 2 2" % name, group=g)
        self.check("select object", "bridge.object.select %s" % name, group=g)
        self.check("delete object", "bridge.object.delete %s" % name, group=g)
        self.check("object really gone", "bridge.object.list", group=g,
                   verify=lambda p: _need(name not in json.dumps(p), "object survived delete"))

    def entities(self):
        g = "entities"
        self.check("entities list", "bridge.entities.list", group=g)

    def env(self):
        g = "env"
        self.check("env list", "bridge.env.list", group=g,
                   verify=lambda p: _need(len(json.dumps(p)) > 50, "env list looks empty"))
        t = self.check("env time", "bridge.env.time", group=g)

        # Time-of-day variables are SPLINES over the day, not plain values:
        # SetVariableValue is overwritten by the next Update(), so a naive set
        # reports success and changes nothing. env.set rewrites the spline keys
        # and reads the value back, which is what "applied" reports.
        #
        # Variable names contain spaces and the command line splits on
        # whitespace, so address it by index. Look the index up rather than
        # hardcoding it -- the table differs between presets.
        env = self.check("env list (for index lookup)", "bridge.env.list", group=g)
        idx, original = None, None
        if isinstance(env, dict):
            for v in env.get("variables", []):
                if v.get("name") == "Bloom amount" and v.get("type") == "float":
                    idx, original = v["index"], v["value"]
                    break
        if idx is None:
            self.skip("env set applies", "no float 'Bloom amount' in this preset to test against", g)
        else:
            self.check("env set actually applies", "bridge.env.set %d 0.35" % idx, group=g,
                       verify=lambda p: _need(p.get("applied") is True,
                                              "env.set reported applied=%r" % (p.get("applied"),)))
            self.check("env set is readable back", "bridge.env.list", group=g,
                       verify=lambda p: _need(
                           abs(_env_value(p, idx) - 0.35) < 0.01,
                           "value read back as %r, not 0.35" % (_env_value(p, idx),)))
            self.check("env restored", "bridge.env.set %d %g" % (idx, original), group=g)

        if isinstance(t, dict) and "time" in t:
            self.check("env time restored", "bridge.env.time %s" % t["time"], group=g)

    def vegetation(self):
        g = "vegetation"
        self.check("vegetation list", "bridge.vegetation.list", group=g)
        if self.destructive:
            self.check("vegetation add", "bridge.vegetation.add objects/sphere.cgf 1 1 0 0 4096 0 90", group=g)
        else:
            self.skip("vegetation add/paint/clear", "destructive: changes the vegetation map", g)

    def schematyc(self):
        g = "schematyc"
        out = os.path.join(tempfile.gettempdir(), "bridge selftest schematyc.json")      # a space in the path on purpose: the verb takes the rest of the line

        def verify(p):
            _need(p.get("elements", 0) > 300, "the registry dump is nearly empty: %s" % p.get("elements"))
            data = json.loads(open(out, encoding="utf-8").read())                        # strict UTF-8: a Latin-1 byte in an engine description broke the first version
            _need(data["elements"] == p["elements"] == len(data["items"]), "the file disagrees with the reply")
            _need(any(i["type"] == "Component" and i["name"] == "Rigidbody" for i in data["items"]), "the engine's Rigidbody component is not in the dump")
        self.check("schematyc dump (path with a space, strict UTF-8, Rigidbody present)", "bridge.schematyc.dump " + out.replace(os.sep, "/"), group=g, verify=verify)
        try:
            os.remove(out)
        except OSError:
            pass

    def particles(self):
        g = "particles"
        tmp = tempfile.gettempdir()
        feats = os.path.join(tmp, "bridge selftest particle features.json")       # a space in the path on purpose
        src = os.path.join(tmp, "bridge selftest effect.pfx")
        out = os.path.join(tmp, "bridge selftest effect out.pfx")

        def verify_features(p):
            _need(p.get("features", 0) >= 50, "the particle feature registry is nearly empty: %s" % p.get("features"))
            rows = json.loads(open(feats, encoding="utf-8").read())
            by = {r["full"]: r for r in rows}
            body = json.loads(by["Render: Sprites"]["json"])["Components"][0]["Component"]["Features"][0]
            _need("RenderSprites" in body, "Render: Sprites is not written under its file name: %s" % list(body))
            _need("FieldOpacity" in json.loads(by["Appearance: Opacity"]["json"])["Components"][0]["Component"]["Features"][0], "the legacy name the engine writes for Appearance: Opacity is gone")
        self.check("particle features (path with a space; written under the names a .pfx file holds)", "bridge.particles.features " + feats.replace(os.sep, "/"), group=g, verify=verify_features)
        doc = {"Version": 13, "Attributes": {"Attributes": []}, "Components": [{"Component": {"Name": "t", "Features": [
            {"RenderSprites": {}}, {"NoSuchFeature": {"A": 1}}, {"SpawnRate": {"Amount": {"value": 7.0}, "Bogus": 2}}]}}]}
        json.dump(doc, open(src, "w"))

        def verify_roundtrip(p):
            _need(p["loaded"] and p["saved"], "the engine did not load/save the effect: %s" % p)
            names = [f["full"] for f in p["components"][0]["features"]]
            _need(names == ["Render: Sprites", "Spawn: Rate"], "the engine kept %s (the unknown feature must be dropped, the two real ones kept)" % names)
            kept = json.loads(open(out, encoding="utf-8").read())["Components"][0]["Component"]["Features"]
            _need("Bogus" not in json.dumps(kept) and kept[1]["SpawnRate"]["Amount"]["value"] == 7.0, "the unknown key survived or the known value was lost")
        self.check("particle roundtrip (the engine's own loader drops the unknown feature and key, keeps the rest)", "bridge.particles.roundtrip %s | %s" % (src.replace(os.sep, "/"), out.replace(os.sep, "/")), group=g, verify=verify_roundtrip)
        out2 = os.path.join(tmp, "bridge selftest effect out2.pfx")

        def verify_fixed(p):
            _need(open(out, encoding="utf-8").read() == open(out2, encoding="utf-8").read(), "what the engine wrote does not load back to the same text")
        self.check("particle roundtrip control: the engine's own output is a fixed point", "bridge.particles.roundtrip %s | %s" % (out.replace(os.sep, "/"), out2.replace(os.sep, "/")), group=g, verify=verify_fixed)
        self.check("particle roundtrip refuses a missing file", "bridge.particles.roundtrip %s | %s" % ((src + ".nope").replace(os.sep, "/"), out2.replace(os.sep, "/")), expect_ok=False, group=g)
        name = "ClaudeSelfTestEmitter"
        self.check("create entity for the component test", "bridge.object.create EntityWithComponent 512 512 60 " + name, group=g)

        def verify_add(p):
            _need(p["component"].endswith("CParticleComponent") and p["label"] == "Particle Emitter" and p["guid"], "unexpected answer: %s" % p)
        self.check("add a component by its label (with a space)", "bridge.entity.add_component %s Particle Emitter" % name, group=g, verify=verify_add)
        self.check("the entity now has the component (properties readable)", "bridge.component.properties %s CParticleComponent" % name, group=g,
                   verify=lambda p: _need("FilePath" in p["properties"], "the added component has no FilePath member"))
        self.check("add_component control: an unknown component is refused", "bridge.entity.add_component %s NoSuchComponent" % name, expect_ok=False, group=g)
        self.check("add_component control: an unknown entity is refused", "bridge.entity.add_component NoSuchEntityAnywhere Particle Emitter", expect_ok=False, group=g)
        self.check("delete the entity again", "bridge.object.delete " + name, group=g)
        for f in (feats, src, out, out2):
            try:
                os.remove(f)
            except OSError:
                pass

    def nav(self):
        g = "navigation"
        import base64

        def verify_types(p):
            _need(p["agent_types"] and p["agent_types"][0]["meshes"], "the editor has no navigation agent type with a mesh: %s" % p["agent_types"])
        self.check("nav agent types and meshes", "bridge.nav.query", group=g, verify=verify_types)
        lines = base64.b64encode(b"self 512 172 31.2 512 172 31.2\nfar 512 172 31.2 3900 3900 10").decode()

        def verify_reach(p):
            seg = {s["label"]: s for s in p["segments"]}
            _need(seg["self"]["reachable"], "a point is not connected to itself")
            _need(not seg["far"]["reachable"] and seg["far"]["end_mesh"] == -1, "a point 3 km outside the area came back connected or inside a mesh")
        self.check("nav connectivity (control: a point connects to itself, a far point does not)", "bridge.nav.query - " + lines, group=g, verify=verify_reach)
        self.check("nav query refuses an unknown agent type", "bridge.nav.query NoSuchAgentType " + lines, expect_ok=False, group=g)

    def commands(self):
        g = "commands"
        out = os.path.join(tempfile.gettempdir(), "bridge selftest commands.json")            # a space in the path on purpose

        def verify(p):
            _need(p["commands"] > 500, "the command manager list is nearly empty: %s" % p["commands"])
            rows = json.loads(open(out, encoding="utf-8").read())
            names = {r["module"] + "." + r["name"] for r in rows}
            _need({"object.set_position", "ai.regenerate_agent_type_layer0", "trackview.add_node"} <= names, "known commands are missing from the dump")
        self.check("commands dump (path with a space; strict UTF-8; known commands present)", "bridge.commands.dump " + out.replace(os.sep, "/"), group=g, verify=verify)
        try:
            os.remove(out)
        except OSError:
            pass

    def sequence(self):
        g = "sequence"
        tmp = tempfile.gettempdir()
        src = os.path.join(tmp, "bridge selftest sequence.xml")                    # a space in the path on purpose
        out = os.path.join(tmp, "bridge selftest sequence out.xml")
        open(src, "w", encoding="utf-8").write('<Sequence SequenceVersion="4" Name="claude_selftest_seq" Flags="0" endTimeTicks="12000" GUIDLo="1A2B" GUIDHi="3C4D" ID="777">'
                                               '<Nodes><Node Id="1" Type="Entity" Name="NoSuchEntity" Flags="0" paramIdVersion="9"/></Nodes></Sequence>')

        def verify(p):
            _need(p["loaded"] and abs(p["end"] - 2.0) < 0.01, "the engine did not load the 2 s sequence: %s" % p)
            _need([n["name"] for n in p["nodes"]] == ["NoSuchEntity"], "the node is gone: %s" % p["nodes"])
            saved = open(out, encoding="utf-8").read()
            _need("claude_selftest_seq" in saved and "zz_claude_seq_" not in saved, "the saved XML does not carry the original name back")
        self.check("sequence roundtrip (path with a space; the engine's own loader, a throw-away name)", "bridge.sequence.roundtrip %s | %s" % (src.replace(os.sep, "/"), out.replace(os.sep, "/")), group=g, verify=verify)
        self.check("sequence roundtrip refuses a missing file", "bridge.sequence.roundtrip %s | %s" % ((src + ".nope").replace(os.sep, "/"), out.replace(os.sep, "/")), expect_ok=False, group=g)
        for f in (src, out):
            try:
                os.remove(f)
            except OSError:
                pass

    def mannequin(self):
        g = "mannequin"
        tmp = tempfile.gettempdir()
        clips = os.path.join(tmp, "bridge selftest clips.json")                     # a space in the path on purpose
        rep = os.path.join(tmp, "bridge selftest mannequin.json")

        def verify_clips(p):
            rows = json.loads(open(clips, encoding="utf-8").read())
            names = {r["name"] for r in rows}
            _need(p["clips"] >= 20 and {"Audio", "ParticleEffect", "PositionAdjust", "AimPose"} <= names, "the procedural clip registry is missing known clips: %s" % sorted(names))
            audio = [r for r in rows if r["name"] == "Audio"][0]
            _need("StartTrigger" in audio["json"], "the Audio clip's parameters did not serialise: %r" % audio["json"][:80])
        self.check("mannequin clip registry (path with a space; the engine's IProceduralClipFactory with parameters)", "bridge.mannequin.clips " + clips.replace(os.sep, "/"), group=g, verify=verify_clips)

        def verify_check(p):
            _need(p["loaded"] and p["controller_loaded"], "the villager's database or controller definition did not load: %s" % p)
            d = json.loads(open(rep, encoding="utf-8").read())
            _need(d["character_loaded"] and d["animation_set"] and d["animation_count"] > 1000, "the character's animation set is missing: %s" % {k: d[k] for k in ("character_loaded", "animation_set", "animation_count")})
            _need([f["name"] for f in d["fragments"]][:3] == ["Idle", "Walk", "Run"], "fragment ids are not what the file declares: %s" % [f["name"] for f in d["fragments"]])
            _need(any(s["file"].lower().endswith("villager.adb") for s in d["saved"]), "the engine's saved database is not in the report")
            _need(d["validate"]["errors"] == [], "the engine's own validation found errors in the villager's database: %s" % d["validate"]["errors"])
        self.check("mannequin check of the villager (adb + controller + character; engine load, save, Validate)",
                   "bridge.mannequin.check %s | Animations/Mannequin/ADB/Villager.adb | Animations/Mannequin/ADB/VillagerControllerDefinition.xml | Objects/Characters/Human/sdk_player/sdk_player.cdf | reload" % rep.replace(os.sep, "/"),
                   group=g, verify=verify_check)

        def verify_missing(p):
            _need(p["loaded"] is False, "a database that does not exist reported loaded: %s" % p)
        self.check("mannequin check of a file that does not exist reports loaded=false (control)", "bridge.mannequin.check %s | Animations/Mannequin/ADB/zz_nope.adb | - | - | -" % rep.replace(os.sep, "/"), group=g, verify=verify_missing)
        self.check("mannequin check refuses missing arguments", "bridge.mannequin.check %s" % rep.replace(os.sep, "/"), expect_ok=False, group=g)
        for f in (clips, rep):
            try:
                os.remove(f)
            except OSError:
                pass

    def behavior_tree(self):
        g = "behavior tree"
        tmp = tempfile.gettempdir()
        names = os.path.join(tmp, "bridge selftest bt names.txt")                   # a space in the path on purpose
        out = os.path.join(tmp, "bridge selftest bt nodes.json")
        tree = os.path.join(tmp, "bridge selftest bt tree.xml")
        rep = os.path.join(tmp, "bridge selftest bt load.json")
        open(names, "w", encoding="utf-8").write("\n".join(["Sequence", "Wait", "NoSuchNodeType"]) + "\n")
        open(tree, "w", encoding="utf-8").write('<BehaviorTree><Root><Sequence><Wait duration="2"/></Sequence></Root></BehaviorTree>')

        def verify_nodes(p):
            rows = json.loads(open(out, encoding="utf-8").read())
            by = {r["name"]: r for r in rows}
            _need(by["Sequence"]["created"] and by["Wait"]["created"] and not by["NoSuchNodeType"]["created"], "the node factory answered wrongly: %s" % {k: v["created"] for k, v in by.items()})
            _need("duration" in by["Wait"]["xml"], "Wait did not describe its duration attribute: %r" % by["Wait"]["xml"])
        self.check("behavior tree node registry (control: a name that is not registered is not created)", "bridge.bt.nodes %s | %s" % (out.replace(os.sep, "/"), names.replace(os.sep, "/")), group=g, verify=verify_nodes)

        def verify_load(p):
            _need(p["strict"] and p["lenient"], "a valid two-node tree was not loaded: %s" % p)
            d = json.loads(open(rep, encoding="utf-8").read())
            _need("Wait" in d["lenient"]["xml"] and 'duration="2"' in d["lenient"]["xml"], "the engine's description of the tree lacks the Wait node: %r" % d["lenient"]["xml"])
        self.check("behavior tree load (strict and lenient; path with a space; the engine's description of the tree)", "bridge.bt.load %s | %s" % (rep.replace(os.sep, "/"), tree.replace(os.sep, "/")), group=g, verify=verify_load)
        open(tree, "w", encoding="utf-8").write('<BehaviorTree><Root><Sequence><NoSuchNodeType/></Sequence></Root></BehaviorTree>')

        def verify_bad(p):
            _need(p["strict"] is False, "a tree with an unknown node type loaded strictly: %s" % p)
        self.check("behavior tree with an unknown node type is refused by the strict load (control)", "bridge.bt.load %s | %s" % (rep.replace(os.sep, "/"), tree.replace(os.sep, "/")), group=g, verify=verify_bad)
        self.check("behavior tree load refuses a missing file", "bridge.bt.load %s | %s" % (rep.replace(os.sep, "/"), (tree + ".nope").replace(os.sep, "/")), expect_ok=False, group=g)
        for f in (names, out, tree, rep):
            try:
                os.remove(f)
            except OSError:
                pass

    def uqs(self):
        g = "uqs"
        tmp = tempfile.gettempdir()
        cat = os.path.join(tmp, "bridge selftest uqs catalog.json")                 # a space in the path on purpose
        rep = os.path.join(tmp, "bridge selftest uqs check.json")

        def verify_catalog(p):
            d = json.loads(open(cat, encoding="utf-8").read())
            names = {g_["name"] for g_ in d["generators"]}
            _need("std::PointsOnGridProjectedOntoNavMesh" in names and len(d["functions"]) > 20 and len(d["query_factories"]) == 3, "the UQS registry is incomplete: %s" % {k: len(v) for k, v in d.items()})
            gen = [g_ for g_ in d["generators"] if g_["name"] == "std::PointsOnGridProjectedOntoNavMesh"][0]
            _need(any(i["name"] == "size" and i["id"] == "SIZE" for i in gen["inputs"]), "the generator's input ids are missing: %s" % gen["inputs"][:2])
        self.check("uqs catalog (path with a space; the hub's factory databases with input ids)", "bridge.uqs.catalog " + cat.replace(os.sep, "/"), group=g, verify=verify_catalog)

        def verify_check(p):
            _need(p["loaded"] and p["valid"], "the project's query did not load and validate: %s" % p)
            d = json.loads(open(rep, encoding="utf-8").read())
            _need(d["errors"] == [] and d["query"]["generator"] == "std::PointsOnGridProjectedOntoNavMesh", "unexpected result for the project's query: %s" % d["query"].get("generator"))
        self.check("uqs check of the project's query through the engine's editor service", "bridge.uqs.check %s | BestSpotToStand" % rep.replace(os.sep, "/"), group=g, verify=verify_check)

        def verify_missing(p):
            _need(p["loaded"] is False, "a query that does not exist reported loaded: %s" % p)
        self.check("uqs check of a query that does not exist reports loaded=false (control)", "bridge.uqs.check %s | zz_no_such_query" % rep.replace(os.sep, "/"), group=g, verify=verify_missing)
        self.check("uqs check refuses to register a name that is not zz_mcp_* (the engine deletes a removed record's file)", "bridge.uqs.check %s | BestSpotToStand | register" % rep.replace(os.sep, "/"), expect_ok=False, group=g)
        for f in (cat, rep):
            try:
                os.remove(f)
            except OSError:
                pass

    def character(self):
        g = "character"
        tmp = tempfile.gettempdir()
        names = os.path.join(tmp, "bridge selftest chars.txt")                      # a space in the path on purpose
        out = os.path.join(tmp, "bridge selftest chars.json")
        open(names, "w", encoding="utf-8").write(chr(10).join(["Objects/Characters/Human/sdk_player/sdk_player.cdf", "objects/characters/zz_no_such_character.cdf"]) + chr(10))

        def verify(p):
            rows = json.loads(open(out, encoding="utf-8").read())
            _need(p["asked"] == 2 and p["created"] == 1, "expected one of two characters to be created: %s" % p)
            by = {r["file"]: r for r in rows}
            base = by["Objects/Characters/Human/sdk_player/sdk_player.cdf"]
            _need(base["created"] and base["joints"] > 100 and len(base["attachments"]) > 20 and base["animations"] > 1000, "the villager character is not what it should be: %s" % {k: base[k] for k in ("created", "joints", "animations")})
            _need(not by["objects/characters/zz_no_such_character.cdf"]["created"], "a character that does not exist was created")
        self.check("character check (path with a space; the engine creates the villager, refuses a missing file; reference count handled)", "bridge.character.check %s | %s" % (out.replace(os.sep, "/"), names.replace(os.sep, "/")), group=g, verify=verify)
        self.check("character check refuses missing arguments", "bridge.character.check %s" % out.replace(os.sep, "/"), expect_ok=False, group=g)
        for f in (names, out):
            try:
                os.remove(f)
            except OSError:
                pass

    def material(self):
        g = "material"
        tmp = tempfile.gettempdir()
        names = os.path.join(tmp, "bridge selftest mtls.txt")                       # a space in the path on purpose
        out = os.path.join(tmp, "bridge selftest mtls.json")
        open(names, "w", encoding="utf-8").write(chr(10).join(["materials/ashvale/cloth.mtl", "materials/zz_no_such_material.mtl"]) + chr(10))

        def verify(p):
            rows = json.loads(open(out, encoding="utf-8").read())
            _need(p["asked"] == 2 and p["found"] == 1, "expected one of two materials to load: %s" % p)
            by = {r["file"]: r for r in rows}
            cloth = by["materials/ashvale/cloth.mtl"]
            sub = cloth["subs"][0]
            _need(cloth["found"] and sub["shader"].lower() == "illum" and sub["surface"] == "mat_fabric" and len(sub["textures"]) >= 2, "cloth.mtl is not what it should be: %s" % {k: sub.get(k) for k in ("shader", "surface")})
            _need(not by["materials/zz_no_such_material.mtl"]["found"], "a material that does not exist was loaded")
        self.check("material check (path with a space; the engine loads cloth.mtl with its shader, surface type and textures, refuses a missing file)", "bridge.material.check %s | %s" % (out.replace(os.sep, "/"), names.replace(os.sep, "/")), group=g, verify=verify)
        self.check("material check refuses missing arguments", "bridge.material.check %s" % out.replace(os.sep, "/"), expect_ok=False, group=g)
        for f in (names, out):
            try:
                os.remove(f)
            except OSError:
                pass

    def names_and_project(self):
        g = "character names / project"
        tmp = tempfile.gettempdir()
        out = os.path.join(tmp, "bridge selftest names.json")                      # a space in the path on purpose

        def verify_names(p):
            d = json.loads(open(out, encoding="utf-8").read())
            _need(d["created"] and p["joints"] == 163 and p["animations"] > 1000 and "Bip01 Pelvis" in d["joints"], "the villager's names are not what they should be: %s" % {k: p[k] for k in ("created", "joints", "animations")})
        self.check("character names (path with a space; the villager's 163 joints and its animation set, reference count handled)", "bridge.character.names %s | Objects/Characters/Human/sdk_player/sdk_player.cdf" % out.replace(os.sep, "/"), group=g, verify=verify_names)

        def verify_missing(p):
            _need(not p["created"] and p["joints"] == 0, "a character that does not exist was created: %s" % p)
        self.check("character names of a character that does not exist reports created=false (control)", "bridge.character.names %s | objects/characters/zz_no_such_character.cdf" % out.replace(os.sep, "/"), group=g, verify=verify_missing)
        self.check("character names refuses missing arguments", "bridge.character.names %s" % out.replace(os.sep, "/"), expect_ok=False, group=g)

        def verify_project(p):
            d = json.loads(open(out, encoding="utf-8").read())
            want_name, want_engine = None, None          # what the project's own Game.cryproject says
            try:
                import project_root
                cp = json.loads(open(project_root.CRYPROJECT, encoding="utf-8-sig").read())
                want_name = cp.get("info", {}).get("name")
                want_engine = cp.get("require", {}).get("engine")
            except Exception:
                pass
            _need((want_name is None or d["name"] == want_name) and (want_engine is None or d["engine"] == want_engine)
                  and p["plugins"] >= 6 and d["file"].lower().endswith("game.cryproject"), "the project manager's answer is not what it should be: %s" % {k: d[k] for k in ("name", "engine", "file")})
        self.check("project info (the engine's project manager: name, engine id, project file and every plug-in entry)", "bridge.project.info %s" % out.replace(os.sep, "/"), group=g, verify=verify_project)

        def verify_drs(p):
            d = json.loads(open(out, encoding="utf-8").read())
            _need(d["available"] and len(d["actions"]) >= 10 and len(d["conditions"]) >= 10 and len(d["lines"]) >= 1, "the DRS answer is not what it should be: %s" % {k: len(d[k]) for k in ("actions", "conditions", "lines")})
            tree = json.loads(d["responses"]["sg_villager_sees_player"])
            _need("BaseSegment" in tree, "the engine's copy of the response has no BaseSegment")
            _need("BaseSegment" not in (d["responses"]["sg_zz_no_such_signal"] or ""), "a signal that has no response got one")
        self.check("drs dump (the engine's action/condition types, its dialog lines and a response tree as it serialises it; an unknown signal has no tree)", "bridge.drs.dump %s | sg_villager_sees_player | sg_zz_no_such_signal" % out.replace(os.sep, "/"), group=g, verify=verify_drs)
        self.check("drs dump refuses missing arguments", "bridge.drs.dump", expect_ok=False, group=g)

        def verify_ui(p):
            d = json.loads(open(out, encoding="utf-8").read())
            _need(d["available"] and p["elements"] >= 1, "FlashUI is not there or holds no element: %s" % p)
            el = {e["name"]: e for e in d["elements"]}["MainMenu"]
            _need(el["file"] == "Menus_Startmenu.gfx" and el["layer"] == 3 and len(el["functions"]) == 57 and len(el["events"]) == 6, "MainMenu is not what the file declares: %s" % {k: len(el[k]) for k in ("functions", "events", "arrays")})
            _need(el["functions"][0].get("in_movie") is None, "a dump without init asked the movie")
        self.check("ui dump (the engine's UI elements as FlashUI kept them: MainMenu with 57 functions, 6 events, no movie asked)", "bridge.ui.dump %s" % out.replace(os.sep, "/"), group=g, verify=verify_ui)

        def verify_ui_movie(p):
            d = json.loads(open(out, encoding="utf-8").read())
            el = {e["name"]: e for e in d["elements"]}["MainMenu"]
            _need(p["movies_loaded"] == 1 and el["movie_loaded"], "the movie was not loaded: %s" % p)
            mv = el["movie"]
            _need(mv["control_root"] is True and mv["control_absent"] is False, "the movie query failed its controls: %s" % mv)
            have = {f["name"]: f["in_movie"] for f in el["functions"]}
            _need(have["SetupScreen"] is True and have["AddButton"] is True, "the movie lacks a function the game calls")
            _need(have["StartCountDown"] is False, "a declared function the movie lacks was reported present (positive control for a 'no')")
        self.check("ui dump with init (the movie is loaded and asked: root yes, a made-up name no, setupScreen yes, startCountDown no)", "bridge.ui.dump %s | init | MainMenu" % out.replace(os.sep, "/"), group=g, verify=verify_ui_movie)

        def verify_ui_unknown(p):
            _need(p["movies_loaded"] == 0, "an element name that does not exist loaded a movie: %s" % p)
        self.check("ui dump asking to init an element that does not exist loads no movie (control)", "bridge.ui.dump %s | init | ZzNoSuchElement" % out.replace(os.sep, "/"), group=g, verify=verify_ui_unknown)
        self.check("ui dump refuses missing arguments", "bridge.ui.dump", expect_ok=False, group=g)

        def verify_undo_info(p):
            _need(p["mode"] == "off" and isinstance(p["undo_steps"], int) and isinstance(p["redo_steps"], int) and isinstance(p["latest"], list), "the undo answer is not what it should be: %s" % p)
        self.check("undo info (the editor's own undo manager: mode off at rest, step counts, the newest names)", "bridge.undo.info", group=g, verify=verify_undo_info)
        self.check("undo mode refuses a word that is not on or off", "bridge.undo.mode maybe", expect_ok=False, group=g)
        self.check("undo step refuses an action that is not undo or redo", "bridge.undo.step explode", expect_ok=False, group=g)
        self.check("notify shows a message in the Notification Center and answers its id", "bridge.notify info Claude self-test | the notification verb answers", group=g, verify=lambda p: _need(isinstance(p.get("id"), int), "no notification id: %s" % p))
        self.check("notify refuses a level it does not know (control)", "bridge.notify loud x | y", expect_ok=False, group=g)
        self.check("notify refuses a missing title", "bridge.notify info", expect_ok=False, group=g)

        def verify_visarea(p):
            d = json.loads(open(out, encoding="utf-8").read())
            _need(d["available"] and p["count"] == d["count"] == len(d["areas"]) and p["probes"] == 1, "the vis area answer is not what it should be: %s" % p)
            _need(len(d["probes"]) == 1 and d["probes"][0]["area"] is None and d["probes"][0]["inside"] == [], "a point 5 km above the map is inside an area: %s" % d["probes"])
        self.check("visarea dump (the 3D engine's vis areas and portals; a probe 5 km above the map is in none)", "bridge.visarea.dump %s | 0 0 5000" % out.replace(os.sep, "/"), group=g, verify=verify_visarea)
        self.check("visarea dump refuses missing arguments", "bridge.visarea.dump", expect_ok=False, group=g)
        self.check("visarea dump refuses positions that do not come in threes (control)", "bridge.visarea.dump %s | 1 2" % out.replace(os.sep, "/"), expect_ok=False, group=g)
        try:
            os.remove(out)
        except OSError:
            pass

    def flow(self):
        g = "flow"
        tmp = tempfile.gettempdir()
        cat = os.path.join(tmp, "bridge selftest flow nodes.json")                  # a space in the path on purpose
        graph = os.path.join(tmp, "bridge selftest flow graph.json")

        def verify_catalog(p):
            rows = json.loads(open(cat, encoding="utf-8").read())
            by = {r["type"]: r for r in rows}
            _need(p["types"] >= 600 and p["created"] == p["types"], "expected 600+ node types, all created: %s" % p)
            timer = by["Time:Timer"]
            _need([x["name"] for x in timer["inputs"]] == ["period", "min", "max", "paused"] and [x["name"] for x in timer["outputs"]] == ["out"], "Time:Timer ports are not what they should be: %s" % timer)
        self.check("flow catalog (path with a space; every node type created in a scratch graph and read back)", "bridge.flow.catalog %s" % cat.replace(os.sep, "/"), group=g, verify=verify_catalog)

        def verify_graph(p):
            d = json.loads(open(graph, encoding="utf-8").read())
            _need(d["xml_loaded"] and d["ok"] and p["nodes"] == 4 and p["edges"] == 3, "the project's graph did not load as 4 nodes and 3 edges: %s" % p)
            _need({n["type"] for n in d["nodes"]} == {"Time:Timer", "Claude:Lamp", "Claude:VillagerSays", "Debug:Log"}, "unexpected node types: %s" % [n["type"] for n in d["nodes"]])
        self.check("flow graph check of the project's graph (loaded, not enabled, not initialised)", "bridge.flow.check %s | libs/FlowGraphs/claude_farm.xml" % graph.replace(os.sep, "/"), group=g, verify=verify_graph)

        def verify_missing(p):
            d = json.loads(open(graph, encoding="utf-8").read())
            _need(not d["xml_loaded"] and not d["ok"], "a graph that does not exist was reported as loaded: %s" % d)
        self.check("flow graph check of a file that does not exist reports xml_loaded=false (control)", "bridge.flow.check %s | libs/flowgraphs/zz_no_such_graph.xml" % graph.replace(os.sep, "/"), group=g, verify=verify_missing)
        self.check("flow graph check refuses missing arguments", "bridge.flow.check %s" % graph.replace(os.sep, "/"), expect_ok=False, group=g)
        for f in (cat, graph):
            try:
                os.remove(f)
            except OSError:
                pass

    def database(self):
        g = "database"
        types = self.check("db types", "bridge.db.types", group=g,
                           verify=lambda p: _need("flare" in p.get("types", []), "flare missing from db types"))
        for t in ("material", "particle", "archetype", "gametoken", "flare"):
            self.check("libraries: %s" % t, "bridge.db.libraries %s" % t, group=g,
                       verify=lambda p, t=t: _need(isinstance(p.get("libraries"), list),
                                                   "no library list for %s" % t))
            self.check("items: %s" % t, "bridge.db.items %s" % t, group=g)

        # Flares are the interesting case: EDB_TYPE_FLARE is not handled by
        # CEditorImpl::GetDBItemManager at all, and the per-item serializer
        # writes no optics, so both halves are patched.
        item = "ClaudeSelfTestFlare"
        lib_xml = _scratch("claude_selftest_flarelib.xml")
        self.check("create flare", "bridge.db.create flare Level %s" % item, group=g)
        self.check("export flare library", "bridge.db.export_library flare Level " + lib_xml, group=g,
                   verify=lambda p: _need(p.get("items", 0) >= 1, "library exported no items"))
        self.check("flare library carries the optics tree", "bridge.file.read_b64 " + lib_xml, group=g,
                   verify=_flare_xml_has_params)
        self.check("delete flare", "bridge.db.delete flare Level.%s" % item, group=g)

    def capture(self):
        g = "capture"
        shot = _scratch("claude_selftest_shot.bmp")
        first = self.check("viewport screenshot", "bridge.viewport.screenshot " + shot, group=g,
                           verify=lambda p: _need(p.get("width", 0) > 200 and p.get("fractionLit", 0) > 0.05,
                                                  "screenshot came back blank or tiny: %s" % json.dumps(p)))
        # Two settled captures of a live viewport should differ. Identical
        # checksums mean the capture returned the same frame twice, which is how
        # a stale grab gets mistaken for "the scene really looks like that".
        if isinstance(first, dict) and "checksum" in first:
            self.check("settled capture produces a new frame",
                       "bridge.viewport.screenshot settle=1500 " + shot, group=g,
                       verify=lambda p: _need(p.get("checksum") != first.get("checksum"),
                                              "checksum identical to the previous capture -- settle is not waking the editor"))
        else:
            self.skip("settled capture produces a new frame",
                      "this build's screenshot verb reports no checksum", g)
        self.check("input move lands where asked", "bridge.input.move 400 300", group=g,
                   verify=lambda p: _need(p.get("cursor") == p.get("screen"),
                                          "cursor %s, asked for %s" % (p.get("cursor"), p.get("screen"))))

    def record(self, name, passed, detail, group):
        self.results.append({"name": name, "group": group, "cmd": "", "status": "PASS" if passed else "FAIL",
                             "detail": "" if passed else detail, "raw": "", "ms": 0})

    def dialogs(self):
        """Answering a modal by name, with a real one. 'Rebuild all navigation' asks Yes/No
        (AIManager.cpp:786-793) and No does nothing. The command that opens it is a QUEUED one, so it holds
        the ordinary queue until the dialog closes -- the very situation the dialog queue exists for."""
        g = "dialog"
        if not self.destructive:
            # The modal is opened by ai.regenerate_agent_type_all; if the dialog does not appear in time the
            # command goes on to rebuild the navigation mesh, so this group is only run with --destructive.
            self.skip("dialog group", "needs --destructive: opens the navigation-rebuild modal, which rebuilds navigation if not answered", g)
            return
        self.check("dialog list answers", "bridge.dialog.list", group=g,
                   verify=lambda p: _need(isinstance(p, dict) and "dialogs" in p, "no dialogs field"))
        self.check("click with no dialog is refused", "bridge.dialog.click yes", expect_ok=False,
                   contains="no dialog", group=g)

        import threading
        opener = {}

        def open_it():
            try:
                opener["resp"] = Bridge(timeout=90).send("bridge.execute_command ai.regenerate_agent_type_all")
            except Exception as exc:
                opener["exc"] = exc

        thread = threading.Thread(target=open_it, daemon=True)
        thread.start()
        found = None
        deadline = time.time() + 15
        while time.time() < deadline and found is None:
            time.sleep(0.5)
            try:
                r = self.b.send("bridge.dialog.list")
                for d in json.loads(r.get("output", "{}")).get("dialogs", []) if r.get("ok") else []:
                    if "Navigation Rebuilding" in d.get("title", ""):
                        found = d
            except Exception:
                pass
        if found is None:
            self.record("a real modal is listed", False, "the rebuild question never appeared in bridge.dialog.list", g)
            return
        std = sorted(b.get("standard") for b in found.get("buttons", []))
        self.record("a real modal is listed with its buttons", "yes" in std and "no" in std,
                    "buttons %s" % found.get("buttons"), g)
        self.check("unknown button refused, buttons listed", "bridge.dialog.click maybe", expect_ok=False,
                   contains="its buttons", group=g)
        self.check("click No by name", "bridge.dialog.click no", group=g,
                   verify=lambda p: _need(p.get("standard") == "no", "clicked %s" % p.get("button")))
        thread.join(30)
        returned = "resp" in opener and opener["resp"].get("ok")
        self.record("the command that opened it returns", bool(returned),
                    "opener: %s" % (opener.get("resp") or opener.get("exc")), g)
        gone = self.check("the dialog is gone", "bridge.dialog.list", group=g,
                          verify=lambda p: _need(all("Navigation Rebuilding" not in d.get("title", "")
                                                     for d in p.get("dialogs", [])), "still open"))
        if isinstance(gone, dict) and any("Navigation Rebuilding" in d.get("title", "") for d in gone.get("dialogs", [])):
            # Do not leave a modal up for the rest of the suite: Escape answers it as Cancel (no rebuild).
            try:
                self.b.send("bridge.input.key escape")
            except Exception:
                pass

    def run(self):
        self.core()
        self.window()
        self.dialogs()
        self.python()
        self.files()
        self.camera()
        self.terrain()
        self.objects()
        self.entities()
        self.env()
        self.vegetation()
        self.database()
        self.schematyc()
        self.particles()
        self.nav()
        self.commands()
        self.sequence()
        self.mannequin()
        self.behavior_tree()
        self.uqs()
        self.character()
        self.material()
        self.flow()
        self.names_and_project()
        self.capture()
        return self.results


def _need(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _pos(p):
    if isinstance(p, dict):
        for key in ("pos", "position"):
            if key in p and isinstance(p[key], list) and len(p[key]) == 3:
                return p[key]
        if all(k in p for k in ("x", "y", "z")):
            return [p["x"], p["y"], p["z"]]
    return [0.0, 0.0, 0.0]


def _env_value(parsed, index):
    if isinstance(parsed, dict):
        for v in parsed.get("variables", []):
            if v.get("index") == index:
                return v.get("value")
    return None


def _flare_xml_has_params(p):
    import base64
    xml = base64.b64decode(p["b64"]).decode("utf-8", "replace")
    _need("<Params>" in xml, "exported flare library has no <Params> -- optics tree missing")


def render_md(results, elapsed):
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = [r for r in results if r["status"] == "FAIL"]
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    lines = []
    lines.append("# ClaudeBridge self-test")
    lines.append("")
    lines.append("%d passed, %d failed, %d skipped in %.1fs" % (passed, len(failed), skipped, elapsed))
    lines.append("")
    if failed:
        lines.append("## Failures")
        lines.append("")
        for r in failed:
            lines.append("- **%s** (`%s`)" % (r.get("name", "?"), r.get("cmd", "")))
            lines.append("  - %s" % r.get("detail", ""))
        lines.append("")
    lines.append("## All checks")
    lines.append("")
    lines.append("| group | check | status | ms | note |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in results:
        # .get throughout: a row that failed before it could be fully filled in
        # must still render. An earlier version raised KeyError('ms') while
        # formatting the report, turning one failed check into no report at all.
        note = (r.get("detail") or "").replace("|", "\\|")[:110]
        lines.append("| %s | %s | %s | %s | %s |" % (r.get("group", "?"), r.get("name", "?"),
                                                     r.get("status", "?"), r.get("ms", 0), note))
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destructive", action="store_true",
                    help="also run checks that modify terrain/vegetation")
    ap.add_argument("--json", default=os.path.join(os.getcwd(), "bridge_selftest.json"))
    ap.add_argument("--md", default=os.path.join(os.getcwd(), "bridge_selftest.md"))
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    t0 = time.time()
    suite = Suite(Bridge(port=args.port), destructive=args.destructive)
    try:
        results = suite.run()
    except Exception as exc:
        results = suite.results + [{"name": "harness", "group": "harness", "cmd": "",
                                    "status": "FAIL", "detail": "%s: %s" % (type(exc).__name__, exc),
                                    "raw": "", "ms": 0}]
    elapsed = time.time() - t0

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"elapsed": elapsed, "results": results}, f, indent=1)
    with open(args.md, "w", encoding="utf-8") as f:
        f.write(render_md(results, elapsed))

    failed = [r for r in results if r["status"] == "FAIL"]
    print(render_md(results, elapsed))
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
