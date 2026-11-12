#!/usr/bin/env python3
"""Self-test of audio_report.py against signals whose answers are known: a measuring tool is trusted only after it reads what it was given.

    test_audio_report.py        # exit 1 on any failure
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

HERE = _paths.ROOT
sys.path.insert(0, HERE)
import audio_report as A

DIR = os.path.join(A.RUNS, "_audio_test")
SR = 44100
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %s %-66s %s" % ("ok  " if ok else "FAIL", name, str(detail)[:80]))


def wav(name, x):
    """x: [samples] or [channels, samples] float in -1..1 -> 16-bit WAV under job_runs/_audio_test."""
    x = np.atleast_2d(x)
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, name)
    w = wave.open(path, "wb")
    w.setnchannels(x.shape[0]); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(x.T, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()
    return path


def sine(freq, seconds, amp):
    t = np.arange(int(SR * seconds)) / SR
    return amp * np.sin(2 * np.pi * freq * t)


def expect_refusal(name, path):
    try:
        A.report(path)
    except A.AudioError as exc:
        check(name, True, str(exc)[:60])
        return
    check(name, False, "was read")


def main():
    shutil.rmtree(DIR, ignore_errors=True)
    try:
        # --- level: a 1 kHz sine at -20 dBFS peak is -23.0 dBFS RMS and -23.0 LUFS (BS.1770: full-scale 997 Hz sine = -3.01 LUFS)
        r = A.report(wav("tone20.wav", sine(1000, 3, 0.1)))
        check("peak of a -20 dBFS sine reads -20.0", abs(r["peak_dbfs"] + 20.0) < 0.1, r["peak_dbfs"])
        check("RMS reads -23.0 dBFS", abs(r["rms_dbfs"] + 23.0) < 0.1, r["rms_dbfs"])
        check("integrated loudness reads -23.0 LUFS", r["lufs"] is not None and abs(r["lufs"] + 23.0) < 0.4, r["lufs"])
        check("no flags on a clean tone", not r["flags"] and r["clipped_samples"] == 0, r["flags"])
        check("duration, rate and channels are read back", abs(r["duration_s"] - 3.0) < 0.01 and r["sample_rate"] == SR and r["channels"] == 1, (r["duration_s"], r["sample_rate"], r["channels"]))
        # --- spectrum
        low = A.report(wav("tone440.wav", sine(440, 2, 0.3)))["spectrum"]
        high = A.report(wav("tone4k.wav", sine(4000, 2, 0.3)))["spectrum"]
        check("spectral centroid of 440 Hz reads 440 and of 4 kHz reads 4000", abs(low["centroid_hz"] - 440) < 15 and abs(high["centroid_hz"] - 4000) < 60, (low["centroid_hz"], high["centroid_hz"]))
        check("the energy shares land in the right bands", low["share_120hz_2khz"] > 0.97 and high["share_2khz_8khz"] > 0.97, (low["share_120hz_2khz"], high["share_2khz_8khz"]))
        # --- clipping: the control for the clipped-sample count is the clean tone above (0)
        c = A.report(wav("clip.wav", np.clip(sine(300, 2, 1.6), -1, 1)))
        check("a clipped sine is flagged with a count", c["clipped_samples"] > 1000 and any("clips" in f for f in c["flags"]), (c["clipped_samples"], c["flags"]))
        # --- silence: 1.0 s, 2 s of tone, 1.5 s
        s = A.report(wav("padded.wav", np.concatenate([np.zeros(SR), sine(500, 2, 0.3), np.zeros(int(SR * 1.5))])), loop=False)
        check("leading silence reads 1.0 s and trailing 1.5 s", abs(s["silence_start_s"] - 1.0) < 0.05 and abs(s["silence_end_s"] - 1.5) < 0.05, (s["silence_start_s"], s["silence_end_s"]))
        gap = A.report(wav("gap.wav", np.concatenate([sine(500, 1, 0.3), np.zeros(int(SR * 0.8)), sine(500, 1, 0.3)])))
        check("a silent gap inside reads 0.8 s", abs(gap["longest_silence_inside_s"] - 0.8) < 0.05, gap["longest_silence_inside_s"])
        # --- the loop seam: 441 Hz is exactly 100 samples a cycle, so 2 s ends where it began; 2.0025 s ends a quarter cycle later (a jump)
        good = A.report(wav("loop_good.wav", sine(441, 2.0, 0.5)), loop=True)
        bad = A.report(wav("loop_bad.wav", sine(441, 2.0025, 0.5)), loop=True)
        check("a seamless loop reads a seam near 1x a typical step and no flag", good["loop_seam"]["jump_over_typical_step"] < 2.5 and not any("seam" in f for f in good["flags"]), good["loop_seam"])
        check("a loop cut mid-cycle reads a big jump and is flagged", bad["loop_seam"]["jump_over_typical_step"] > 8 and any("seam" in f for f in bad["flags"]), bad["loop_seam"])
        # --- stereo
        t = sine(300, 2, 0.3)
        same = A.report(wav("st_same.wav", np.stack([t, t])))
        anti = A.report(wav("st_anti.wav", np.stack([t, -t])))
        check("stereo correlation reads +1 for identical channels and -1 for opposite", same["stereo_correlation"] > 0.99 and anti["stereo_correlation"] < -0.99, (same["stereo_correlation"], anti["stereo_correlation"]))
        # --- voice: noise is the control (not voiced), a harmonic tone with vibrato-free pitch is
        rng = np.random.default_rng(1)
        noise = A.report(wav("noise.wav", rng.normal(0, 0.1, SR * 2)), voice=True)["voice"]
        buzz = sum(sine(120 * k, 2, 0.2 / k) for k in range(1, 12))
        voiced = A.report(wav("buzz.wav", buzz), voice=True)["voice"]
        check("voiced share: ~0 for noise (the control), high for a 120 Hz harmonic tone", noise["voiced_share"] < 0.2 and voiced["voiced_share"] > 0.8 and abs(voiced["f0_hz"] - 120) < 6, (noise, voiced))
        # --- a lossy file goes through ffmpeg and still reads right
        ogg = os.path.join(DIR, "tone20.ogg")
        subprocess.run([A._tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", os.path.join(DIR, "tone20.wav"), "-c:a", "libvorbis", "-q:a", "5", ogg], stdin=subprocess.DEVNULL, check=True)
        o = A.report(ogg)
        check("an OGG of the -20 dBFS tone reads about -20 dBFS and -23 LUFS", abs(o["peak_dbfs"] + 20) < 0.7 and abs(o["lufs"] + 23) < 0.7 and o["codec"] == "vorbis", (o["peak_dbfs"], o["lufs"], o["codec"]))
        # --- what it will not read
        expect_refusal("a file outside Assets/ and job_runs is refused", r"C:\Windows\Media\chimes.wav")
        expect_refusal("'..' out of Assets/ is refused", "../server.py")
        expect_refusal("a missing file is reported", "audio/sdlmixer/assets/no_such_file.ogg")
        # --- the real music: facts the user asked for (about two minutes, no clipping)
        for name in ("menu", "day", "night", "danger", "end"):
            rel = "audio/sdlmixer/assets/ash_music_%s.ogg" % name
            if not os.path.isfile(os.path.join(A.ASSETS, rel.replace("/", os.sep))):
                print("  SKIPPED: %s is not in this project (the music checks use the Ashvale example's tracks)" % rel)
                continue
            m = A.report(rel)
            check("music %-6s is about two minutes (100-130 s), unclipped, no flags" % name, 100 <= m["duration_s"] <= 130 and m["clipped_samples"] == 0 and not m["flags"],
                  "%.0f s %.1f LUFS %s" % (m["duration_s"], m["lufs"], m["flags"]))
    finally:
        shutil.rmtree(DIR, ignore_errors=True)
    check("cleanup: the scratch folder is gone", not os.path.exists(DIR))
    bad = results.count(False)
    print("audio report test: %s (%d checks)" % ("PASS" if not bad else "%d FAILED" % bad, len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
