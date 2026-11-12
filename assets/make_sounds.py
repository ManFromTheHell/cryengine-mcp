#!/usr/bin/env python3
"""Synthesises Ashvale's own ambience loops: rain, crickets, a soft wind bed.

    make_sounds.py [out dir]        (default: Assets/audio/sdlmixer/assets)

No sample library is needed and none is copied: the GameSDK has no rain and no cricket recording, and Neon Noir's (which has a
thunder) is not ours to use. Everything here is numpy noise shaped in the frequency domain, 44.1 kHz, 16-bit mono, written as
WAV and then encoded to OGG Vorbis with the ffmpeg already installed on this machine: SDL_mixer plays WAV, but Crytek's packager
copies only the extensions in its own whitelist (rc_jobs/game_assets.xml AllExtensions has *.ogg, no *.wav), so a WAV never
reaches a package and the rain, crickets and wind would be silent in every build (visible by listing the pak). Each
loop is built PERIODIC (its content repeats exactly at the loop length) so the seam is inaudible;
`--check` measures the seam (the step between the last and the first sample against the loop's own sample-to-sample step).

  ash_rain_loop.wav      12 s   broadband hiss (2-9 kHz) + a low patter (200-900 Hz) + individual drops on hard surfaces
  ash_crickets_loop.wav   8 s   seven "voices" (4.3-5.6 kHz), each a train of ~3 ms pulses at 27-38 Hz in 0.35 s chirps, every 1.1-1.9 s
  ash_wind_loop.wav       8 s   low-passed noise (60-700 Hz) with two slow swells
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import sys
import wave

import numpy as np

RATE = 44100
HERE = _paths.ROOT
DEFAULT_OUT = os.path.join(__import__("project_root").PROJECT, "Assets", "audio", "sdlmixer", "assets")   # the active project, not the folder above this module
rng = np.random.default_rng(20260930)


def band(noise, lo, hi, edge=0.15):
    """Band-pass by masking the spectrum with smooth (raised-cosine) edges; the signal stays periodic in len(noise)."""
    n = len(noise)
    spec = np.fft.rfft(noise)
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    m = np.zeros_like(f)
    lo_e, hi_e = lo * (1 - edge), hi * (1 + edge)
    up = (f >= lo_e) & (f < lo)
    m[up] = 0.5 - 0.5 * np.cos(np.pi * (f[up] - lo_e) / max(lo - lo_e, 1e-9))
    m[(f >= lo) & (f <= hi)] = 1.0
    dn = (f > hi) & (f <= hi_e)
    m[dn] = 0.5 + 0.5 * np.cos(np.pi * (f[dn] - hi) / max(hi_e - hi, 1e-9))
    return np.fft.irfft(spec * m, n)


def norm(x, peak):
    return x * (peak / max(np.max(np.abs(x)), 1e-9))


def circular_add(dst, src, at):
    """Add src into dst starting at `at`, wrapping around the end: keeps a loop periodic."""
    n = len(dst)
    idx = (np.arange(len(src)) + at) % n
    np.add.at(dst, idx, src)


def rain(seconds=12.0):
    n = int(RATE * seconds)
    hiss = band(rng.standard_normal(n), 2000, 9000)
    patter = band(rng.standard_normal(n), 200, 900)
    # a slow, shallow swell so it is not a flat hiss; the swell period divides the loop length
    t = np.arange(n) / RATE
    swell = 1.0 + 0.10 * np.sin(2 * np.pi * t / seconds * 2 + 1.3) + 0.06 * np.sin(2 * np.pi * t / seconds * 5)
    bed = norm(hiss, 0.30) * swell + norm(patter, 0.12)
    # drops: a short resonant ping (a damped 1.5-4 kHz sinusoid), a few hundred a second in aggregate would be a hiss, so ~60/s
    drops = np.zeros(n)
    for _ in range(int(60 * seconds)):
        at = int(rng.integers(0, n))
        f0 = rng.uniform(1500, 4200)
        dur = int(RATE * rng.uniform(0.004, 0.012))
        tt = np.arange(dur) / RATE
        ping = np.sin(2 * np.pi * f0 * tt) * np.exp(-tt * rng.uniform(500, 900)) * rng.uniform(0.03, 0.16)
        circular_add(drops, ping, at)
    return norm(bed + drops, 0.60)


def cricket_voice(n, seconds, rate_hz, freq, chirp_len, chirp_period, phase_s):
    """One cricket: pulses of a fixed pitch, in chirps. Every period divides the loop, so the voice is periodic."""
    out = np.zeros(n)
    pulse_len = int(RATE * 0.0035)
    tt = np.arange(pulse_len) / RATE
    pulse = np.sin(2 * np.pi * freq * tt) * np.sin(np.pi * np.arange(pulse_len) / pulse_len) ** 2
    pulse *= 1.0 + 0.0 * tt
    chirps = int(round(seconds / chirp_period))
    for c in range(chirps):
        start = (c * chirp_period + phase_s) % seconds
        pulses = int(chirp_len * rate_hz)
        for k in range(pulses):
            env = np.sin(np.pi * (k + 0.5) / pulses) ** 0.7        # a chirp swells and dies
            circular_add(out, pulse * env * 0.5, int((start + k / rate_hz) * RATE))
    return out


def crickets(seconds=8.0):
    n = int(RATE * seconds)
    mix = np.zeros(n)
    for i in range(7):
        chirp_period = seconds / int(rng.integers(5, 8))           # 1.1 .. 1.6 s, an integer number to the loop
        mix += cricket_voice(n, seconds, rng.uniform(27, 38), rng.uniform(4300, 5600), 0.35,
                             chirp_period, rng.uniform(0, seconds)) * rng.uniform(0.5, 1.0)
    floor = band(rng.standard_normal(n), 3000, 8000) * 0.012        # the still air between them
    return norm(mix + floor, 0.90)


def wind(seconds=8.0):
    n = int(RATE * seconds)
    base = band(rng.standard_normal(n), 60, 700)
    t = np.arange(n) / RATE
    swell = 0.55 + 0.30 * np.sin(2 * np.pi * t / seconds * 1 + 0.7) + 0.15 * np.sin(2 * np.pi * t / seconds * 3 + 2.1)
    return norm(base * swell, 0.90)


def write_wav(path, x):
    pcm = np.clip(x, -1, 1)
    data = (pcm * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)


def to_ogg(wav_path, ogg_path):
    """Encode with the installed ffmpeg (libvorbis), quality 5, then drop the WAV: only the OGG ships."""
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found on PATH: cannot encode %s to OGG" % wav_path)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", wav_path, "-c:a", "libvorbis", "-q:a", "5", ogg_path], check=True)
    os.remove(wav_path)


def seam(x):
    """The step across the loop point relative to the loop's typical step. ~1 or less: the seam is as smooth as the signal."""
    typical = np.mean(np.abs(np.diff(x)))
    return abs(x[0] - x[-1]) / max(typical, 1e-9)


def main():
    out = next((a for a in sys.argv[1:] if not a.startswith("--")), DEFAULT_OUT)
    os.makedirs(out, exist_ok=True)
    for name, fn in (("ash_rain_loop.wav", rain), ("ash_crickets_loop.wav", crickets), ("ash_wind_loop.wav", wind)):
        x = fn()
        wav = os.path.join(out, name)
        write_wav(wav, x)
        to_ogg(wav, wav[:-4] + ".ogg")
        rms = float(np.sqrt(np.mean(x * x)))
        print("%-24s %5.1f s  peak %.2f  rms %.3f  seam step %.2fx typical  -> %s" % (name, len(x) / RATE, np.max(np.abs(x)), rms, seam(x), name[:-4] + ".ogg"))


if __name__ == "__main__":
    main()
