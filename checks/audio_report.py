#!/usr/bin/env python3
"""Measure an audio file: the numbers that can be had without listening. Listening decides what is good; this says what is on the disk.

    audio_report.py <file.ogg|file.wav> [--voice] [--loop]

  duration, sample rate, channels, codec
  peak and RMS in dBFS, crest factor, clipped samples, integrated loudness (LUFS, ffmpeg's ebur128), loudness range, true peak
  silence at the start and end, and the longest silent stretch inside (below -50 dBFS)
  loop seam: the jump between the last and the first sample divided by a typical step (the measure make_music.py prints), and the level jump across it
  spectrum: centroid, and the share of the energy below 120 Hz, 120 Hz-2 kHz, 2-8 kHz, above 8 kHz
  stereo: L/R correlation (1 = mono, -1 = out of phase)
  --voice: voice_quality.analyse (periodicity, voiced share, f0) -- the instrument that stands in for listening when a voice has been processed

What already existed and is reused: voice_quality.analyse, make_music's seam definition, ffmpeg/ffprobe (installed for the sound loops). New: everything for a file you did
not just generate (loudness, clipping, silence, spectrum). It reads; it never writes. Paths: a game path under Assets/ ('audio/sdlmixer/assets/x.ogg') or a file
under job_runs (a preview_voice WAV).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets")
RUNS = os.path.join(HERE, "job_runs")
MAX_SECONDS = 900
SILENCE_DB = -50.0


class AudioError(ValueError):
    pass


def _tool(name):
    p = shutil.which(name)
    if not p:
        raise AudioError("%s not found on PATH (it is installed with ffmpeg)" % name)
    return p


def resolve(path):
    """A game path (relative to Assets/) or a file under job_runs; nothing else is read."""
    cand = path if os.path.isabs(path) else os.path.join(ASSETS, path)
    real = os.path.realpath(cand)
    if not any(real.lower().startswith(os.path.realpath(r).lower() + os.sep) for r in (ASSETS, RUNS)):
        raise AudioError("%r is outside Assets/ and job_runs: only project audio and preview renders are read" % path)
    if not os.path.isfile(real):
        raise AudioError("not found: %s" % path)
    if not real.lower().endswith((".ogg", ".wav", ".mp3", ".flac")):
        raise AudioError("not an audio file: %s" % path)
    return real


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=300)


def probe(path):
    r = _run([_tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels:format=duration,bit_rate",
              "-of", "json", path])
    info = json.loads(r.stdout or b"{}")
    s = (info.get("streams") or [{}])[0]
    if not s:
        raise AudioError("ffprobe found no audio stream in %s" % path)
    return {"codec": s.get("codec_name"), "sample_rate": int(s["sample_rate"]), "channels": int(s["channels"]),
            "duration_s": round(float(info["format"]["duration"]), 3), "bitrate_kbps": round(int(info["format"].get("bit_rate", 0)) / 1000)}


def decode(path, channels, rate):
    r = _run([_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", path, "-f", "f32le", "-acodec", "pcm_f32le", "-"])
    x = np.frombuffer(r.stdout, dtype="<f4")
    if x.size == 0:
        raise AudioError("ffmpeg decoded nothing from %s: %s" % (path, r.stderr.decode("utf-8", "replace")[-200:]))
    return x[: (x.size // channels) * channels].reshape(-1, channels).T.copy()        # [channel, sample]


def db(v):
    return float(20 * np.log10(max(float(v), 1e-9)))


def loudness(path):
    """Integrated loudness, loudness range and true peak from ffmpeg's EBU R128 meter (its summary goes to stderr)."""
    r = _run([_tool("ffmpeg"), "-nostdin", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])
    text = r.stderr.decode("utf-8", "replace")
    tail = text[text.rfind("Summary:"):] if "Summary:" in text else text
    def pick(pattern):
        m = re.search(pattern, tail)
        return float(m.group(1)) if m else None
    return {"lufs": pick(r"I:\s+(-?[\d.]+) LUFS"), "range_lu": pick(r"LRA:\s+(-?[\d.]+) LU"), "true_peak_dbfs": pick(r"Peak:\s+(-?[\d.]+) dBFS")}


def silences(mono, rate):
    """(leading s, trailing s, longest interior s) below SILENCE_DB, in 10 ms frames."""
    hop = max(1, int(rate * 0.01))
    n = len(mono) // hop
    if n == 0:
        return 0.0, 0.0, 0.0
    frames = mono[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    quiet = 20 * np.log10(np.maximum(rms, 1e-9)) < SILENCE_DB
    if quiet.all():
        return n * 0.01, 0.0, 0.0
    lead = int(np.argmax(~quiet))
    trail = int(np.argmax(~quiet[::-1]))
    longest = run = 0
    for q in quiet[lead:n - trail]:
        run = run + 1 if q else 0
        longest = max(longest, run)
    return lead * 0.01, trail * 0.01, longest * 0.01


def spectrum(mono, rate):
    n = min(len(mono), rate * 120)
    seg = mono[:n] * np.hanning(n)
    mag = np.abs(np.fft.rfft(seg)) ** 2
    freq = np.fft.rfftfreq(n, 1.0 / rate)
    total = float(mag.sum()) or 1.0
    share = lambda lo, hi: round(float(mag[(freq >= lo) & (freq < hi)].sum()) / total, 3)
    return {"centroid_hz": round(float((freq * mag).sum() / total), 1), "share_below_120hz": share(0, 120), "share_120hz_2khz": share(120, 2000),
            "share_2khz_8khz": share(2000, 8000), "share_above_8khz": share(8000, 1e9)}


def report(path, voice=False, loop=None):
    real = resolve(path)
    info = probe(real)
    if info["duration_s"] > MAX_SECONDS:
        raise AudioError("%.0f s is longer than the %d s this reads" % (info["duration_s"], MAX_SECONDS))
    x = decode(real, info["channels"], info["sample_rate"])
    rate = info["sample_rate"]
    mono = x.mean(axis=0)
    peak, rms = float(np.abs(x).max()), float(np.sqrt((x ** 2).mean()))
    clipped = int((np.abs(x) >= 0.999).sum())
    lead, trail, interior = silences(mono, rate)
    step = max(float(np.abs(np.diff(x[0])).mean()), 1e-9)
    jump = float(np.abs(x[:, 0] - x[:, -1]).max())
    edge = max(1, int(rate * 0.5))
    head, tail = float(np.sqrt((x[:, :edge] ** 2).mean())), float(np.sqrt((x[:, -edge:] ** 2).mean()))
    out = dict(info)
    out.update({"file": os.path.relpath(real, PROJECT).replace("\\", "/"), "peak_dbfs": round(db(peak), 2), "rms_dbfs": round(db(rms), 2), "crest_db": round(db(peak) - db(rms), 2),
                "clipped_samples": clipped, "silence_start_s": round(lead, 2), "silence_end_s": round(trail, 2), "longest_silence_inside_s": round(interior, 2),
                "loop_seam": {"jump_over_typical_step": round(jump / step, 2), "level_change_db_across": round(db(head) - db(tail), 2)}})
    out.update(loudness(real))
    out["spectrum"] = spectrum(mono, rate)
    if info["channels"] == 2 and float(x[0].std()) > 1e-6 and float(x[1].std()) > 1e-6:
        out["stereo_correlation"] = round(float(np.corrcoef(x[0], x[1])[0, 1]), 3)
    if voice:
        import voice_quality as Q
        periodic, voiced, f0 = Q.analyse(mono.astype(np.float32), rate)
        out["voice"] = {"periodic": round(periodic, 3), "voiced_share": round(voiced, 3), "f0_hz": round(f0, 1)}
    looping = loop if loop is not None else os.path.basename(real).lower().startswith(("ash_music_", "amb_", "loop"))
    flags = []
    if clipped:
        flags.append("clips: %d samples at full scale" % clipped)
    if out["peak_dbfs"] > -0.3:
        flags.append("peak %.1f dBFS leaves no headroom" % out["peak_dbfs"])
    if out["lufs"] is not None and out["lufs"] < -35:
        flags.append("very quiet: %.1f LUFS" % out["lufs"])
    if lead > 0.5:
        flags.append("%.2f s of silence at the start" % lead)
    if trail > 1.0 and not looping:
        flags.append("%.2f s of silence at the end" % trail)
    if looping and (jump / step > 8 or abs(out["loop_seam"]["level_change_db_across"]) > 6):
        flags.append("loop seam may be audible: jump %.1fx a typical step, level change %.1f dB across it" % (jump / step, out["loop_seam"]["level_change_db_across"]))
    out["flags"] = flags
    out["treated_as_loop"] = bool(looping)
    out["note"] = "automated measurements only; judge the sound by listening"
    return out


def main():
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    for p in paths:
        print(json.dumps(report(p, voice="--voice" in sys.argv, loop=True if "--loop" in sys.argv else None), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
