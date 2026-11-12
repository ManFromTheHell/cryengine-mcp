#!/usr/bin/env python3
"""Spoken dialogue for Ashvale: every NPC line of libs/ashvale/dialogue.xml, read by Windows' own speech
synthesiser, shaped per character, encoded to OGG, and registered with the audio system.

    make_voices.py [--list]
    make_voices.py --only cuthbert [...]     render only these dialogues (the others' files are left exactly as they are; the ACE file still lists every line)

For each <Node> with text: a voice (Microsoft David or Zira, the two this machine has), a speaking rate, and a pitch factor that
is applied afterwards by a PLAIN RESAMPLE (the synthesiser's own pitch attribute barely moves these voices) make Hilda, Tom, Garrick,
Edmund, Oswin and the villagers sound like different people. A resample moves the pitch and the speed together, so the synthesiser is
asked to speak faster or slower by the same factor first (shaped_rate) and the line comes out at its natural length.
NOT an overlap-add to restore the length: an earlier version did that (pitch_shift below) and the voices sounded distorted.
voice_compare.py measured why (voice_quality.py, the raw synthesiser output as the control): the fraction of voiced frames fell from
0.65 to 0.24 (Garrick), 0.65 to 0.14 (Oswin), 0.74 to 0.32 (Edmund), and Garrick's pitch tracker jumped an octave -- smeared, doubled
periods. The result:
  Assets/audio/sdlmixer/assets/vox_<dialogue>_<node index>.ogg      (22 kHz mono, peak-normalised)
  Assets/audio/sdlmixer/ace/claude_voice.xml                        a trigger ash_vox_<dialogue>_<index> per line, and the level
                                                                    parameter ash_vol_voice over all of them
The game plays the line of the node it enters (CAshvaleGame::EnterNode). Nothing is downloaded: System.Speech ships with Windows
and ffmpeg is the one installed for the sound loops.

Honest limit: it is a synthetic voice. It is clear and consistent, it is not acting.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET

import numpy as np

import voice_quality as Q

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
# CRYENGINE_DIALOGUE: path of the dialogue XML to voice (default: the Ashvale example's Assets/libs/ashvale/dialogue.xml). CAST below is Ashvale's cast; other speakers get DEFAULT.
DIALOGUE = os.environ.get("CRYENGINE_DIALOGUE") or os.path.join(PROJECT, "Assets", "libs", "ashvale", "dialogue.xml")
ASSETS = os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "assets")
ACE = os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "ace", "claude_voice.xml")

DAVID, ZIRA = "Microsoft David Desktop", "Microsoft Zira Desktop"
# speaker (or dialogue id when the node has none) -> voice, SAPI rate (-10..10), pitch factor, volume in dB for the trigger
CAST = {
    "Hilda": (ZIRA, -1, 0.94, -3),
    "Tom": (DAVID, 0, 0.96, -3),
    "Garrick": (DAVID, -1, 0.92, -2),
    "Father Edmund": (DAVID, -3, 0.96, -3),
    "Oswin": (DAVID, 1, 1.06, -3),
    "villager": (DAVID, 0, 1.0, -4),
    "woman": (ZIRA, 0, 1.08, -4),
    "Martha": (ZIRA, 0, 1.04, -3),
    "Will": (DAVID, 0, 1.04, -3),
    "Brother Cuthbert": (DAVID, -2, 0.94, -3),
}
DEFAULT = (DAVID, 0, 1.0, -3)

# Piper (an offline neural voice, installed into tts_piper): the Windows voices are of poor quality, so Piper is preferred. speaker -> voice, length_scale (> 1 is slower), pitch factor (a small plain resample), volume in dB.
PIPER_DIR = os.path.join(HERE, "tts_piper")
PIPER_EXE = os.path.join(PIPER_DIR, "piper", "piper.exe")
PIPER_MODELS = {"alan": "en_GB-alan-medium.onnx", "north": "en_GB-northern_english_male-medium.onnx", "alba": "en_GB-alba-medium.onnx"}
PIPER_CAST = {
    "Hilda": ("alba", 1.05, 0.97, -3),
    "Tom": ("north", 1.00, 0.97, -3),
    "Garrick": ("alan", 1.10, 0.94, -2),
    "Father Edmund": ("north", 1.22, 0.97, -3),
    "Oswin": ("alan", 0.95, 1.04, -3),
    "villager": ("north", 1.00, 1.04, -4),
    "woman": ("alba", 1.00, 1.04, -4),
    "Martha": ("alba", 1.00, 1.02, -3),
    "Will": ("north", 0.95, 1.06, -3),
    "Brother Cuthbert": ("alan", 1.22, 0.93, -3),
}
PIPER_DEFAULT = ("north", 1.0, 1.0, -3)
USE_PIPER = os.path.isfile(PIPER_EXE) and "--sapi" not in sys.argv


def cast(speaker):
    return PIPER_CAST.get(speaker, PIPER_DEFAULT) if USE_PIPER else CAST.get(speaker, DEFAULT)


def render_piper(entries, work):
    """Every line through piper.exe (the text on stdin, a 22 kHz mono WAV out). The resample that follows shortens a line by its pitch
    factor, so the voice speaks that much slower first."""
    for stem, speaker, text in entries:
        voice, length, pitch, db = cast(speaker)
        proc = subprocess.run([PIPER_EXE, "--model", os.path.join(PIPER_DIR, PIPER_MODELS[voice]), "--output_file", os.path.join(work, stem + ".wav"),
                               "--length_scale", "%.3f" % (length * pitch), "--sentence_silence", "0.15"],
                              input=text.encode("utf-8"), capture_output=True)
        if proc.returncode != 0 or not os.path.isfile(os.path.join(work, stem + ".wav")):
            raise SystemExit("piper failed on %s: %s" % (stem, proc.stderr.decode("utf-8", "replace")[-400:]))


def lines():
    """[(file stem, speaker key, text)] for every spoken node, in file order."""
    out = []
    for d in ET.parse(DIALOGUE).getroot():
        for j, n in enumerate(d):
            text = (n.text or "").strip()
            sub = n.find("Text")
            if sub is not None:
                text = (sub.text or "").strip()
            if text:
                out.append(("%s_%d" % (d.get("id"), j), n.get("speaker") or d.get("id"), text))
    return out


def shaped_rate(rate, factor):
    """The SAPI rate (-10..10, each step about 11.6 % in speed: 3 ** (rate / 10)) that makes up for a resample by `factor`."""
    return max(-10, min(10, int(round(rate + 10.0 * math.log(1.0 / factor) / math.log(3.0)))))


def shape(samples, rate, factor):
    """Change the pitch (and with it the formants: a bigger or smaller person) by a plain resample, factor > 1 higher. No overlap-add."""
    if abs(factor - 1.0) < 1e-3:
        return samples
    n = len(samples)
    return np.interp(np.arange(0, n - 1, factor), np.arange(n), samples)


def pitch_shift(samples, rate, factor):
    """THE FIRST VERSION, KEPT ONLY as voice_compare.py's 'old' case (it made the voices sound like an eldritch horror: see the module
    docstring). Pitch up by `factor` keeping the duration: resample to change pitch AND speed, then time-stretch back by overlap-add."""
    if abs(factor - 1.0) < 1e-3:
        return samples
    n = len(samples)
    idx = np.arange(0, n - 1, factor)                      # reading faster raises the pitch and shortens the sound
    shifted = np.interp(idx, np.arange(n), samples)
    # stretch back to the original length with a WSOLA-like overlap-add (windows of 40 ms, hop chosen to land on n samples)
    win = int(rate * 0.04)
    hop_out = win // 2
    hop_in = hop_out / (len(shifted) / n) if len(shifted) else hop_out
    window = np.hanning(win)
    out = np.zeros(n + win)
    norm = np.zeros(n + win)
    pos_in, pos_out = 0.0, 0
    while pos_out < n and int(pos_in) + win < len(shifted):
        seg = shifted[int(pos_in):int(pos_in) + win] * window
        out[pos_out:pos_out + win] += seg
        norm[pos_out:pos_out + win] += window
        pos_in += hop_in
        pos_out += hop_out
    norm[norm < 1e-6] = 1.0
    return (out / norm)[:n]


def main():
    all_entries = lines()
    entries = all_entries
    only = sys.argv[sys.argv.index("--only") + 1:] if "--only" in sys.argv else []
    if only:
        entries = [e for e in all_entries if e[0].rsplit("_", 1)[0] in only]
        if not entries:
            raise SystemExit("no dialogue named %s in dialogue.xml" % ", ".join(only))
    if "--list" in sys.argv:
        for stem, speaker, text in entries:
            print("%-14s %-14s %s" % (stem, speaker, text[:70]))
        return
    work = tempfile.mkdtemp(prefix="vox_")
    try:
        if USE_PIPER:
            render_piper(entries, work)
        else:
            jobs = []
            for stem, speaker, text in entries:
                voice, rate, pitch, db = cast(speaker)
                jobs.append({"file": stem, "voice": voice, "rate": shaped_rate(rate, pitch), "pitch": "medium", "text": text})
            jobs_path = os.path.join(work, "jobs.json")
            json.dump(jobs, open(jobs_path, "w", encoding="utf-8"))
            proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "tts_render.ps1"),
                                   "-Jobs", jobs_path, "-Out", work], capture_output=True, text=True)
            if proc.returncode != 0:
                raise SystemExit("speech synthesis failed: " + (proc.stdout + proc.stderr)[-600:])
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise SystemExit("ffmpeg not found on PATH")
        encoded = os.path.join(work, "out")      # encode here first: the old vox_*.ogg are replaced only after the damage check passes
        os.makedirs(encoded, exist_ok=True)
        total = 0.0
        damaged = []
        worst = 9.9
        for stem, speaker, text in entries:
            voice, rate, pitch, db = cast(speaker)
            w = wave.open(os.path.join(work, stem + ".wav"))
            sr, n = w.getframerate(), w.getnframes()
            x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
            w.close()
            before = Q.analyse(x, sr)
            x = shape(x, sr, pitch)
            after = Q.analyse(x, sr)
            # the control built in: a voiced stretch must stay voiced. (A shaper that halves the voiced share produces distorted voices.)
            if before[1] >= 0.3 and after[1] < 0.8 * before[1]:
                damaged.append("%s voiced %.2f -> %.2f" % (stem, before[1], after[1]))
            worst = min(worst, after[1] / before[1]) if before[1] >= 0.3 else worst
            # trim silence at both ends (keep 60 ms), normalise to a peak of 0.85, a short fade out
            loud = np.where(np.abs(x) > 0.02)[0]
            if len(loud):
                x = x[max(0, loud[0] - int(0.06 * sr)):loud[-1] + int(0.12 * sr)]
            x = x * (0.85 / max(np.abs(x).max(), 1e-6))
            fade = int(0.05 * sr)
            x[-fade:] *= np.linspace(1, 0, fade)
            total += len(x) / sr
            wav = os.path.join(work, stem + "_p.wav")
            wf = wave.open(wav, "wb")
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            wf.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
            wf.close()
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "4",
                            os.path.join(encoded, "vox_%s.ogg" % stem)], check=True)
        if damaged:
            raise SystemExit("the shaping damaged %d line(s): %s (the existing vox_*.ogg were left untouched)" % (len(damaged), "; ".join(damaged[:5])))
        os.makedirs(ASSETS, exist_ok=True)
        if not only:
            for f in os.listdir(ASSETS):
                if f.startswith("vox_") and f.endswith(".ogg"):
                    os.remove(os.path.join(ASSETS, f))
        for f in os.listdir(encoded):
            os.replace(os.path.join(encoded, f), os.path.join(ASSETS, f))
        print("voiced share kept by the shaping, worst line: %.0f %% of the raw synthesiser's" % (100 * worst))
        # the ACE file: a trigger per line and one level parameter over all of them (no double dash inside a comment: RC rejects it)
        out = ['<AudioSystemData name="claude_voice" version="5" triggers="%d" parameters="1">' % (2 * len(all_entries)),
               ' <ImplData events="%d" parameters="%d"/>' % (2 * len(all_entries), len(all_entries)),
               ' <Triggers>',
               '  <!-- Spoken dialogue, written by make_voices.py: one trigger per line of dialogue.xml. -->']
        for stem, speaker, text in all_entries:
            db = cast(speaker)[3]
            out.append('  <Trigger name="ash_vox_%s"><Event name="vox_%s.ogg" type="start" volume="%d" loop_count="1"/></Trigger>' % (stem, stem, db))
            # the game stops a line when the conversation ends or moves on (Soundscape::StopVoice plays "<trigger>_stop")
            out.append('  <Trigger name="ash_vox_%s_stop"><Event name="vox_%s.ogg" type="stop" fade_out_time="0.15"/></Trigger>' % (stem, stem))
        out += [' </Triggers>', ' <Parameters>', '  <Parameter name="ash_vol_voice">']
        for stem, speaker, text in all_entries:
            out.append('   <Event name="vox_%s.ogg"/>' % stem)
        out += ['  </Parameter>', ' </Parameters>', ' <EditorData>', '  <Library />', '  <Folders />', '  <Controls />', ' </EditorData>', '</AudioSystemData>', '']
        text = "\n".join(out)
        ET.fromstring(text)                                  # well-formed, or stop here
        open(ACE, "w", encoding="utf-8", newline="\n").write(text)
        print("%d lines rendered (%d in the ACE file), %.0f s of speech -> %s and %s" % (len(entries), len(all_entries), total, ASSETS, ACE))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
