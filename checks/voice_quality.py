#!/usr/bin/env python3
"""Measure whether processing damaged a synthetic voice. This is the instrument that stands in for listening.

    voice_quality.py a.wav [b.wav ...]          (or an .ogg: decoded with ffmpeg)

A voice is a periodic signal while a vowel sounds. Processing that smears the waveform (overlap-add that is not aligned to the pitch
period, a phase vocoder, a chorus from two copies) lowers how periodic the voiced stretches are, and that is what a listener hears as
phasey, doubled, robotic, "eldritch". For every 40 ms frame with enough energy this takes the strongest normalised autocorrelation peak
in the 65-400 Hz lag range (1.0 is a perfectly periodic frame) and reports:
  periodic   median of that peak over the frames
  voiced     fraction of loud frames whose peak is above 0.5
  f0         median pitch (Hz) of the frames above 0.5
The raw synthesiser output is the control: a processing step is acceptable when it keeps `periodic` and `voiced` close to the raw
file's, and moves `f0` by the amount it was asked to.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np


def load(path):
    if path.lower().endswith(".ogg"):
        ff = shutil.which("ffmpeg")
        if not ff:
            raise SystemExit("ffmpeg not found (needed to decode .ogg)")
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "decoded.wav")
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-ac", "1", tmp], check=True)
            return load(tmp)
    w = wave.open(path)
    sr = w.getframerate()
    n = w.getnframes()
    sw = w.getsampwidth()
    raw = w.readframes(n)
    channels = w.getnchannels()
    w.close()
    if sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw[:len(raw) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float64) / 2 ** 23
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2 ** 31
    else:
        raise ValueError("unsupported WAV sample width: %d bytes" % sw)
    if channels > 1:
        x = x[:len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    return x, sr


def analyse(x, sr):
    win, hop = int(0.04 * sr), int(0.02 * sr)
    lo, hi = int(sr / 400), int(sr / 65)
    if len(x) == 0:
        return 0.0, 0.0, 0.0
    peak = float(np.max(np.abs(x))) or 1.0
    floor = 0.05 * peak
    peaks, f0s = [], []
    for i in range(0, len(x) - win, hop):
        f = x[i:i + win]
        if np.sqrt(np.mean(f * f)) < floor:
            continue
        f = f - f.mean()
        spec = np.fft.rfft(f, 2 * win)
        ac = np.fft.irfft(spec * np.conj(spec))[:win]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lo:hi]
        k = int(np.argmax(seg))
        peaks.append(float(seg[k]))
        if seg[k] > 0.5:
            f0s.append(sr / (lo + k))
    if not peaks:
        return 0.0, 0.0, 0.0
    peaks = np.array(peaks)
    return float(np.median(peaks)), float(np.mean(peaks > 0.5)), float(np.median(f0s)) if f0s else 0.0


def main():
    for path in sys.argv[1:]:
        x, sr = load(path)
        periodic, voiced, f0 = analyse(x, sr)
        print("%-40s %5.1f s  periodic %.3f  voiced %.2f  f0 %5.1f Hz" % (os.path.basename(path), len(x) / sr, periodic, voiced, f0))


if __name__ == "__main__":
    main()
