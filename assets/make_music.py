#!/usr/bin/env python3
"""Ashvale's music, composed and rendered in Python: five seamless loops in a medieval manner, each about two
minutes long (the first versions were 34-71 s; their opening bars are kept and new sections follow).

    make_music.py [track ...]        tracks: menu day night danger end crypt   (default all)

Instruments, all synthesised with numpy (no samples, nothing downloaded):
  lute    Karplus-Strong plucked string (a delay line with a damping filter), the backbone of every track
  flute   a sine with a few harmonics, vibrato and breath noise, for melodies
  drone   detuned saws low-passed in the frequency domain, slowly breathing
  drum    a bodhran-like thump: a falling sine and a click
Each track is an exact number of bars and is mixed on a CIRCULAR timeline (a note that rings past the end wraps round to the start),
the reverb is a circular convolution, so the loop has no seam. Modes: D Dorian for the village, D Aeolian for the dark, D Phrygian touches
in the danger theme, D Ionian for the ending. Seeds are fixed: a build is repeatable.

Output: Assets/audio/sdlmixer/assets/ash_music_<track>.ogg (stereo, q5) and ace/claude_music.xml (a start and a stop trigger per
track with a 3 s fade, and one level parameter per track so the game can crossfade them).
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
import xml.etree.ElementTree as ET

import numpy as np

HERE = _paths.ROOT
PROJECT = __import__("project_root").PROJECT
ASSETS = os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "assets")
ACE = os.path.join(PROJECT, "Assets", "audio", "sdlmixer", "ace", "claude_music.xml")
SR = 44100

# semitones above D for each mode, one octave
MODES = {
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "ionian": [0, 2, 4, 5, 7, 9, 11],
}
D2 = 73.416


def hz(semi_above_d2):
    return D2 * 2 ** (semi_above_d2 / 12.0)


def degree(mode, deg, octave=0):
    """Semitones above D2 of scale degree `deg` (0-based, may exceed 6 or go negative) in `mode`."""
    scale = MODES[mode]
    o, d = divmod(deg, 7)
    return scale[d] + 12 * (o + octave)


class Track:
    def __init__(self, name, bpm, bars, beats=4, seed=1):
        self.name, self.bpm, self.bars, self.beats = name, bpm, bars, beats
        self.beat = 60.0 / bpm
        self.n = int(round(bars * beats * self.beat * SR))
        self.rng = np.random.default_rng(seed)
        self.dry = np.zeros((2, self.n))                # what goes through the reverb
        self.wet_send = np.zeros((2, self.n))

    def at(self, bar, beat):
        return int(round(((bar * self.beats) + beat) * self.beat * SR))

    def add(self, samples, start, pan=0.0, send=0.25, gain=1.0):
        """Mix mono samples in at `start`, wrapping round the end of the loop."""
        idx = (np.arange(len(samples)) + start) % self.n
        l, r = gain * np.cos((pan + 1) * np.pi / 4), gain * np.sin((pan + 1) * np.pi / 4)
        np.add.at(self.dry[0], idx, samples * l)
        np.add.at(self.dry[1], idx, samples * r)
        np.add.at(self.wet_send[0], idx, samples * l * send)
        np.add.at(self.wet_send[1], idx, samples * r * send)

    # ---------------------------------------------------------------- instruments
    def lute(self, freq, dur, amp=0.5, bright=0.5, pan=0.0, send=0.3, start=0):
        n = int(SR * dur)
        period = int(round(SR / freq))
        buf = np.zeros(n + period + 2)
        burst = self.rng.uniform(-1, 1, period)
        burst = (burst + np.roll(burst, 1) * (1 - bright) * 2) / (1 + (1 - bright) * 2)         # a duller pluck when bright is low
        buf[:period] = burst
        damp = 0.9965 + 0.0025 * bright
        pos = period
        while pos < n:
            end = min(pos + period, n)
            seg = buf[pos - period:end - period]
            nxt = buf[pos - period + 1:end - period + 1] if end - period + 1 <= pos else seg
            if len(nxt) < len(seg):
                nxt = np.append(nxt, seg[-1])
            buf[pos:end] = damp * 0.5 * (seg + nxt)
            pos = end
        env = np.minimum(1.0, np.arange(n) / (0.004 * SR))
        env *= np.exp(-np.arange(n) / (SR * dur * 0.9))
        x = buf[:n] * env * amp
        self.add(x, start, pan, send)

    def flute(self, freq, dur, amp=0.25, pan=0.0, send=0.5, start=0):
        n = int(SR * dur)
        t = np.arange(n) / SR
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t) * np.minimum(1.0, t / 0.25)
        phase = 2 * np.pi * np.cumsum(freq * vib) / SR
        x = np.sin(phase) + 0.30 * np.sin(2 * phase) + 0.08 * np.sin(3 * phase)
        breath = self.rng.normal(0, 0.05, n)
        x = (x + breath) * amp
        env = np.minimum(1.0, t / 0.06) * np.minimum(1.0, (dur - t) / 0.12)
        self.add(x * np.clip(env, 0, 1), start, pan, send)

    def drone(self, freq, amp=0.15, pan=0.0, send=0.4, voices=3):
        """A pad over the whole loop: detuned saws, low-passed by FFT, with a slow swell that is periodic in the loop."""
        t = np.arange(self.n) / SR
        x = np.zeros(self.n)
        for k in range(voices):
            f = freq * (1 + (k - (voices - 1) / 2) * 0.004)
            # a band-limited saw: the first 12 harmonics, each with a phase fixed by the voice
            for h in range(1, 13):
                # frequencies are rounded so that the waveform repeats exactly over the loop length
                cycles = max(1, round(f * h * self.n / SR))
                x += np.sin(2 * np.pi * cycles * np.arange(self.n) / self.n + k) / h
        swell = 0.75 + 0.25 * np.sin(2 * np.pi * np.arange(self.n) / self.n * max(1, self.bars // 4))
        x *= swell * amp / voices
        self.add(x, 0, pan, send)

    def drum(self, start, amp=0.4, pitch=1.0):
        n = int(SR * 0.45)
        t = np.arange(n) / SR
        f = (60 + 70 * np.exp(-t * 18)) * pitch
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
        click = self.rng.normal(0, 1, n) * np.exp(-t * 90) * 0.25
        self.add((body + click) * amp, start, 0.0, 0.18)

    # ---------------------------------------------------------------- mixing
    def render(self, rt60=2.0, wet=0.6, peak=0.85):
        # a circular reverb: a decaying noise impulse response multiplied in the frequency domain (periodic, so no seam)
        out = self.dry.copy()
        for ch in range(2):
            ir_len = int(SR * rt60)
            ir = self.rng.normal(0, 1, ir_len) * np.exp(-6.9 * np.arange(ir_len) / (SR * rt60))
            ir = np.convolve(ir, np.ones(40) / 40, mode="same")                          # darker tail
            ir_full = np.zeros(self.n)
            ir_full[:min(ir_len, self.n)] = ir[:self.n]
            tail = np.fft.irfft(np.fft.rfft(self.wet_send[ch]) * np.fft.rfft(ir_full), self.n)
            tail /= max(np.abs(ir_full).sum() * 0.25, 1e-6)
            out[ch] += tail * wet
        out *= peak / max(np.abs(out).max(), 1e-9)
        return out


# ---------------------------------------------------------------------------- compositions
def arpeggio(tr, bar, root_deg, mode, pattern, octave=1, amp=0.42, pan=-0.2):
    """A lute figure over one bar: `pattern` is a list of (beat, chord degree offset) with offsets from the root (0, 2, 4, 7...)."""
    for beat, off in pattern:
        semi = degree(mode, root_deg + off, octave)
        tr.lute(hz(semi), 1.8, amp=amp * (0.85 + 0.15 * tr.rng.random()), bright=0.55, pan=pan, start=tr.at(bar, beat))


def melody(tr, bar, notes, mode, octave=2, amp=0.22):
    """notes: (beat, duration in beats, degree)."""
    for beat, dur, deg in notes:
        tr.flute(hz(degree(mode, deg, octave)), dur * tr.beat * 0.96, amp=amp, pan=0.25, start=tr.at(bar, beat))


def chord_root(p):
    """A chord's root as a scale degree around the bass register: degrees above the fourth sit an octave lower."""
    return p - (7 if p > 3 else 0)


def track_day():
    # 40 bars of 84 bpm = 114 s. Bars 0-15 are the first version (an arpeggiated lute and bass, then the flute melody over bars 8-15);
    # 16-39 are new: a lilting roll with a low flute answering, a bridge on other chords with a soft drum, and the return.
    tr = Track("day", 84, 40, seed=11)
    prog = [0, 6, 3, 0, 4, 6, 3, 0]                 # Dm, C, G, Dm, Am, C, G, Dm (degrees 0..6 over a chord root)
    for b in range(16):
        root = chord_root(prog[b % 8])
        pat = [(0, 0), (0.5, 2), (1, 4), (1.5, 7), (2, 4), (2.5, 2), (3, 4), (3.5, 2)] if b % 2 == 0 else \
              [(0, 0), (0.5, 4), (1, 7), (1.5, 4), (2, 2), (2.5, 4), (3, 2), (3.5, 0)]
        arpeggio(tr, b, root, "dorian", pat, octave=1)
        tr.lute(hz(degree("dorian", root, 0)), 2.6, amp=0.5, bright=0.3, pan=0.0, start=tr.at(b, 0))          # the bass
    phrase_a = [(0, 1, 4), (1, 1, 5), (2, 1.5, 7), (3.5, 0.5, 5)]
    phrase_b = [(0, 1.5, 6), (1.5, 0.5, 5), (2, 1, 4), (3, 1, 2)]
    phrase_c = [(0, 1, 2), (1, 1, 4), (2, 2, 5)]
    phrase_d = [(0, 3, 4)]
    seq = [phrase_a, phrase_b, phrase_c, phrase_d, phrase_a, phrase_b, [(0, 1, 7), (1, 1, 6), (2, 2, 4)], phrase_d]
    for i, ph in enumerate(seq):
        melody(tr, 8 + i, ph, "dorian", octave=2)
    # 16-23: the same chords, the figure rolled in a lilting 3+3+2, a low flute line answering underneath
    roll = [(0, 0), (0.75, 4), (1.5, 7), (2, 4), (2.75, 2), (3.5, 4)]
    for b in range(16, 24):
        root = chord_root(prog[b % 8])
        arpeggio(tr, b, root, "dorian", roll, octave=1, amp=0.40, pan=0.2)
        tr.lute(hz(degree("dorian", root, 0)), 2.6, amp=0.5, bright=0.3, pan=0.0, start=tr.at(b, 0))
    low = [[(0, 2, 2), (2, 2, 4)], [(0, 3, 5), (3, 1, 4)], [(0, 2, 6), (2, 2, 5)], [(0, 4, 4)]]
    for i, ph in enumerate(low * 2):
        melody(tr, 16 + i, ph, "dorian", octave=1, amp=0.20)
    # 24-31: the bridge, other chords (G, Dm, Am, Dm, G, C, Am, Dm), a soft drum on the first and third beats, the flute up a step
    bridge = [3, 0, 4, 0, 3, 6, 4, 0]
    for b in range(24, 32):
        root = chord_root(bridge[b - 24])
        pat = [(0, 0), (0.5, 2), (1, 4), (1.5, 2), (2, 7), (2.5, 4), (3, 2), (3.5, 4)]
        arpeggio(tr, b, root, "dorian", pat, octave=1, amp=0.40, pan=-0.25)
        tr.lute(hz(degree("dorian", root, 0)), 2.6, amp=0.5, bright=0.3, pan=0.0, start=tr.at(b, 0))
        tr.drum(tr.at(b, 0), amp=0.20)
        tr.drum(tr.at(b, 2), amp=0.14, pitch=1.1)
    high = [[(0, 1, 6), (1, 1, 7), (2, 2, 9)], [(0, 1.5, 8), (1.5, 0.5, 7), (2, 2, 6)], [(0, 2, 7), (2, 1, 6), (3, 1, 4)], [(0, 4, 5)],
            [(0, 1, 6), (1, 1, 7), (2, 2, 9)], [(0, 1.5, 8), (1.5, 0.5, 9), (2, 2, 11)], [(0, 2, 9), (2, 2, 7)], [(0, 4, 6)]]
    for i, ph in enumerate(high):
        melody(tr, 24 + i, ph, "dorian", octave=2, amp=0.21)
    # 32-39: the return to the opening chords; the first melody again, then it thins to the bass and the loop starts over
    for b in range(32, 40):
        root = chord_root(prog[b % 8])
        arpeggio(tr, b, root, "dorian", [(0, 0), (0.5, 2), (1, 4), (1.5, 7), (2, 4), (2.5, 2), (3, 4), (3.5, 2)] if b % 2 == 0 else
                 [(0, 0), (0.5, 4), (1, 7), (1.5, 4), (2, 2), (2.5, 4), (3, 2), (3.5, 0)], octave=1, amp=0.42 if b < 38 else 0.30)
        tr.lute(hz(degree("dorian", root, 0)), 2.6, amp=0.5, bright=0.3, pan=0.0, start=tr.at(b, 0))
    for i, ph in enumerate(seq[:6]):
        melody(tr, 32 + i, ph, "dorian", octave=2)
    tr.drone(hz(0), amp=0.07, send=0.5)
    return tr.render(rt60=1.8, wet=0.5)


def track_night():
    # 28 bars of 54 bpm = 124 s. Bars 0-15 are the first version (a drone and sparse plucks); 16-27 add a slow theme, then thin out.
    tr = Track("night", 54, 28, seed=23)
    tr.drone(hz(0), amp=0.20, send=0.6, voices=3)
    tr.drone(hz(7), amp=0.10, send=0.6, voices=2)
    pent = [0, 2, 4, 5, 7]                           # a pentatonic-ish handful of degrees
    for b in range(16):
        if b % 2 == 0:
            for k in range(2):
                deg = int(tr.rng.choice(pent)) + (7 if tr.rng.random() < 0.4 else 0)
                tr.lute(hz(degree("aeolian", deg, 1)), 3.4, amp=0.34, bright=0.35, pan=float(tr.rng.uniform(-0.4, 0.4)), send=0.7,
                        start=tr.at(b, 0.3 + k * 1.9 + float(tr.rng.uniform(0, 0.4))))
        if b % 4 == 3:
            melody(tr, b, [(0.5, 2.5, 4 + int(tr.rng.integers(0, 3)))], "aeolian", octave=2, amp=0.16)
    # 16-23: a slow theme in the flute over a low pedal, the plucks continuing sparsely above
    theme = [[(0, 3, 4), (3, 1, 5)], [(0, 2, 7), (2, 2, 5)], [(0, 3, 4), (3, 1, 2)], [(0, 4, 0)],
             [(0, 3, 5), (3, 1, 7)], [(0, 2, 9), (2, 2, 7)], [(0, 3, 5), (3, 1, 4)], [(0, 4, 2)]]
    for i, ph in enumerate(theme):
        melody(tr, 16 + i, ph, "aeolian", octave=2, amp=0.17)
        tr.lute(hz(degree("aeolian", 0 if i < 6 else (-2 if i == 6 else 0), 0)), 4.0, amp=0.30, bright=0.25, pan=0.0, send=0.6, start=tr.at(16 + i, 0))
        if i % 2 == 0:
            deg = int(tr.rng.choice(pent))
            tr.lute(hz(degree("aeolian", deg, 1)), 3.4, amp=0.28, bright=0.35, pan=float(tr.rng.uniform(-0.4, 0.4)), send=0.7,
                    start=tr.at(16 + i, 1.2 + float(tr.rng.uniform(0, 0.5))))
    # 24-27: it thins to the first texture again: one slow pluck every other bar, so the loop's start comes in on a quiet room
    for b in range(24, 28):
        if b % 2 == 0:
            deg = int(tr.rng.choice(pent))
            tr.lute(hz(degree("aeolian", deg, 1)), 3.4, amp=0.30, bright=0.35, pan=float(tr.rng.uniform(-0.4, 0.4)), send=0.7,
                    start=tr.at(b, 0.5 + float(tr.rng.uniform(0, 0.4))))
    return tr.render(rt60=3.2, wet=0.8)


def track_danger():
    # 56 bars of 112 bpm = 120 s. Bars 0-15 are the first version (drums and the phrygian riff); 16-31 drive harder, 32-47 break down
    # to a heartbeat and a stab, 48-55 build back into the riff.
    tr = Track("danger", 112, 56, seed=37)
    tr.drone(hz(0), amp=0.16, send=0.3, voices=3)
    tr.drone(hz(1), amp=0.05, send=0.3, voices=2)       # the minor second against the root: unease
    riff_a = [(0, 0), (0.5, 0), (1, 1), (1.5, 0), (2, 3), (2.5, 1), (3, 0), (3.5, 5)]
    riff_b = [(0, 0), (0.5, 0), (1, 4), (1.5, 3), (2, 1), (2.5, 0), (3, 1), (3.5, 0)]

    def riff(b, lo=1, amp=0.45, which=None):
        for beat, deg in (riff_a if (b % 2 == 0 if which is None else which) else riff_b):
            tr.lute(hz(degree("phrygian", deg, lo)), 0.5, amp=amp, bright=0.8, pan=-0.15, send=0.15, start=tr.at(b, beat))

    def drums(b, full=True):
        for beat in (0, 1.5, 2, 3.5) if b % 4 != 3 else (0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5):
            tr.drum(tr.at(b, beat), amp=0.5 if beat in (0, 2) else 0.3, pitch=1.0 if beat in (0, 2) else 1.25)

    for b in range(16):
        drums(b)
        riff(b)
    for b in range(16, 32):                              # driving: a snare-like crack on 2 and 4, the riff doubled an octave up on odd bars
        drums(b)
        tr.drum(tr.at(b, 1), amp=0.28, pitch=1.7)
        tr.drum(tr.at(b, 3), amp=0.28, pitch=1.7)
        riff(b)
        if b % 2 == 1:
            riff(b, lo=2, amp=0.26, which=False)
    for b in range(32, 48):                              # the breakdown: a heartbeat, a thin riff, a flute stab every fourth bar
        tr.drum(tr.at(b, 0), amp=0.55)
        if b % 2 == 0:
            tr.drum(tr.at(b, 2), amp=0.35, pitch=1.1)
        if b % 4 in (0, 2):
            for beat, deg in ((0, 0), (1.5, 1)):
                tr.lute(hz(degree("phrygian", deg, 1)), 0.9, amp=0.30, bright=0.6, pan=-0.15, send=0.35, start=tr.at(b, beat))
        if b % 4 == 3:
            melody(tr, b, [(0, 1, 8), (1, 0.5, 7), (1.5, 2.5, 8)], "phrygian", octave=1, amp=0.20)
    for b in range(48, 56):                              # the build: eighth notes under the riff, a fill in the last bar
        for beat in (0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5):
            tr.drum(tr.at(b, beat), amp=(0.45 if beat in (0, 2) else 0.22) * (0.7 + 0.04 * (b - 48)), pitch=1.0 if beat in (0, 2) else 1.25)
        riff(b)
    return tr.render(rt60=1.2, wet=0.35)


def track_menu():
    # 32 bars of 66 bpm = 116 s. Bars 0-15 are the first version; 16-23 are on new chords with the melody a step up and an octave-up
    # arpeggio joining; 24-31 return to the first chords with the melody in the higher octave, ending on a held note.
    tr = Track("menu", 66, 32, seed=41)
    tr.drone(hz(0), amp=0.10, send=0.5)
    prog = [0, 3, 6, 0, 0, 3, 4, 0]
    for b in range(16):
        root = chord_root(prog[b % 8])
        pat = [(0, 0), (1, 2), (2, 4), (3, 7)]
        arpeggio(tr, b, root, "dorian", pat, octave=1, amp=0.40, pan=-0.3)
        tr.lute(hz(degree("dorian", root, 0)), 3.5, amp=0.45, bright=0.3, start=tr.at(b, 0))
    lines = [[(0, 2, 7), (2, 2, 6)], [(0, 3, 5), (3, 1, 4)], [(0, 2, 6), (2, 2, 7)], [(0, 4, 9)],
             [(0, 2, 7), (2, 1, 9), (3, 1, 7)], [(0, 3, 6), (3, 1, 5)], [(0, 2, 4), (2, 2, 5)], [(0, 4, 4)]]
    for i, ph in enumerate(lines):
        melody(tr, 8 + i, ph, "dorian", octave=1, amp=0.24)
    prog2 = [3, 0, 6, 3, 0, 6, 4, 0]
    for b in range(16, 24):
        root = chord_root(prog2[b - 16])
        arpeggio(tr, b, root, "dorian", [(0, 0), (1, 2), (2, 4), (3, 7)], octave=1, amp=0.40, pan=-0.3)
        arpeggio(tr, b, root, "dorian", [(0.5, 4), (1.5, 7), (2.5, 9), (3.5, 7)], octave=2, amp=0.20, pan=0.35)
        tr.lute(hz(degree("dorian", root, 0)), 3.5, amp=0.45, bright=0.3, start=tr.at(b, 0))
    lines2 = [[(0, 2, 9), (2, 2, 8)], [(0, 3, 7), (3, 1, 6)], [(0, 2, 8), (2, 2, 9)], [(0, 4, 11)],
              [(0, 2, 9), (2, 1, 11), (3, 1, 9)], [(0, 3, 8), (3, 1, 7)], [(0, 2, 6), (2, 2, 7)], [(0, 4, 6)]]
    for i, ph in enumerate(lines2):
        melody(tr, 16 + i, ph, "dorian", octave=1, amp=0.23)
    for b in range(24, 32):
        root = chord_root(prog[b - 24])
        arpeggio(tr, b, root, "dorian", [(0, 0), (1, 2), (2, 4), (3, 7)], octave=1, amp=0.40 if b < 30 else 0.30, pan=-0.3)
        tr.lute(hz(degree("dorian", root, 0)), 3.5, amp=0.45, bright=0.3, start=tr.at(b, 0))
    for i, ph in enumerate(lines[:7] + [[(0, 4, 4)]]):
        melody(tr, 24 + i, ph, "dorian", octave=2, amp=0.20)
    return tr.render(rt60=2.4, wet=0.6)


def track_end():
    # 36 bars of 76 bpm = 114 s. Bars 0-11 are the first version; 12-23 take a brighter path through the same key (the vi and the IV)
    # with the melody answered a third above; 24-35 are the first chords again with the melody in full.
    tr = Track("end", 76, 36, seed=53)
    tr.drone(hz(0), amp=0.09, send=0.5)
    prog = [0, 3, 4, 0, 5, 3, 4, 0, 0, 3, 4, 0]
    for b in range(12):
        root = chord_root(prog[b])
        pat = [(0, 0), (0.5, 2), (1, 4), (1.5, 2), (2, 7), (2.5, 4), (3, 2), (3.5, 4)]
        arpeggio(tr, b, root, "ionian", pat, octave=1, amp=0.38, pan=-0.2)
        tr.lute(hz(degree("ionian", root, 0)), 3.0, amp=0.5, bright=0.3, start=tr.at(b, 0))
    lines = [[(0, 2, 4), (2, 2, 6)], [(0, 3, 7), (3, 1, 6)], [(0, 2, 5), (2, 2, 4)], [(0, 4, 2)]]
    for i, ph in enumerate(lines * 2):
        melody(tr, 4 + i, ph, "ionian", octave=2, amp=0.22)
    prog2 = [5, 3, 0, 4, 5, 3, 4, 0, 3, 4, 5, 0]
    for b in range(12, 24):
        root = chord_root(prog2[b - 12])
        pat = [(0, 0), (0.5, 2), (1, 4), (1.5, 7), (2, 4), (2.5, 2), (3, 4), (3.5, 2)]
        arpeggio(tr, b, root, "ionian", pat, octave=1, amp=0.38, pan=-0.2)
        tr.lute(hz(degree("ionian", root, 0)), 3.0, amp=0.5, bright=0.3, start=tr.at(b, 0))
    lines2 = [[(0, 2, 6), (2, 2, 8)], [(0, 3, 9), (3, 1, 8)], [(0, 2, 7), (2, 2, 6)], [(0, 4, 4)]]
    for i, ph in enumerate(lines2 * 3):
        melody(tr, 12 + i, ph, "ionian", octave=2, amp=0.22)
        melody(tr, 12 + i, [(b, d, deg - 2) for b, d, deg in ph], "ionian", octave=2, amp=0.10) if i >= 4 else None
    for b in range(24, 36):
        root = chord_root(prog[b - 24])
        pat = [(0, 0), (0.5, 2), (1, 4), (1.5, 2), (2, 7), (2.5, 4), (3, 2), (3.5, 4)]
        arpeggio(tr, b, root, "ionian", pat, octave=1, amp=0.38 if b < 34 else 0.28, pan=-0.2)
        tr.lute(hz(degree("ionian", root, 0)), 3.0, amp=0.5, bright=0.3, start=tr.at(b, 0))
    for i, ph in enumerate(lines * 2 + lines[:2]):
        melody(tr, 24 + i, ph, "ionian", octave=2, amp=0.22)
    return tr.render(rt60=2.2, wet=0.6)


def track_crypt():
    # 24 bars of 48 bpm = 120 s. The one track for the whole of the level under the tower: a low drone with the
    # minor second against it, sparse slow plucks in D Phrygian, water dripping high and far in the reverb, a hollow flute motif every fourth bar from
    # bar 8, and from bar 16 a soft pulse that fades again by bar 22 so the loop starts over on a quiet room.
    tr = Track("crypt", 48, 24, seed=67)
    tr.drone(hz(-5), amp=0.18, send=0.6, voices=3)          # G1: a fourth under the root
    tr.drone(hz(0), amp=0.16, send=0.6, voices=3)
    tr.drone(hz(1), amp=0.04, send=0.6, voices=2)           # the minor second against the root: unease
    for b in range(24):
        if b % 2 == 0:                                       # a low root, rarely
            tr.lute(hz(degree("phrygian", 0, 0)), 4.5, amp=0.34, bright=0.22, pan=0.0, send=0.8, start=tr.at(b, 0))
        if 6 <= b < 20 and b % 2 == 1:                       # a slow falling figure above it
            for k, deg in enumerate((4, 3, 1, 0)):
                tr.lute(hz(degree("phrygian", deg, 1)), 3.0, amp=0.20, bright=0.35, pan=-0.3 + 0.2 * k, send=0.8, start=tr.at(b, k * 1.0))
        for _ in range(int(tr.rng.integers(1, 3))):          # drips: high, short, wet, anywhere
            tr.lute(hz(degree("phrygian", int(tr.rng.integers(0, 5)), 3)), 0.9, amp=0.075, bright=0.95, pan=float(tr.rng.uniform(-0.8, 0.8)),
                    send=0.95, start=tr.at(b, float(tr.rng.uniform(0, 3.8))))
    motif = [(0, 3, 4), (3, 1, 3)], [(0, 2, 5), (2, 2, 3)], [(0, 4, 1)], [(0, 3, 4), (3, 1, 0)]
    for i, b in enumerate((8, 12, 16, 20)):
        melody(tr, b, motif[i], "phrygian", octave=2, amp=0.15)
    for b in range(16, 22):                                  # the pulse: a soft low thump on the first beat, a second one on odd bars
        amp = 0.22 * (1.0 - (b - 16) / 6.0)
        tr.drum(tr.at(b, 0), amp=amp, pitch=0.8)
        if b % 2 == 1:
            tr.drum(tr.at(b, 0.7), amp=amp * 0.6, pitch=0.75)
    return tr.render(rt60=4.5, wet=0.85)


TRACKS = {"menu": track_menu, "day": track_day, "night": track_night, "danger": track_danger, "end": track_end, "crypt": track_crypt}
DB = {"menu": -5, "day": -6, "night": -5, "danger": -5, "end": -5, "crypt": -5}


def write_ogg(name, stereo):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found on PATH")
    wav = os.path.join(tempfile.gettempdir(), "ash_music_%s.wav" % name)
    w = wave.open(wav, "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    pcm = (np.clip(stereo.T, -1, 1) * 32767).astype("<i2")
    w.writeframes(pcm.tobytes())
    w.close()
    out = os.path.join(ASSETS, "ash_music_%s.ogg" % name)
    tmp_out = out[:-4] + ".tmp.ogg"                    # encode beside, then replace: a failed encode keeps the old track
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "5", tmp_out], check=True)
    os.remove(wav)
    os.replace(tmp_out, out)
    return out


def write_ace(names):
    out = ['<AudioSystemData name="claude_music" version="5" triggers="%d" parameters="%d">' % (2 * len(names), len(names)),
           ' <ImplData events="%d" parameters="%d"/>' % (2 * len(names), len(names)),
           ' <Triggers>',
           '  <!-- Music, written by make_music.py. Each track loops and fades over 3 s. -->']
    for n in names:
        out.append('  <Trigger name="ash_music_%s_start"><Event name="ash_music_%s.ogg" type="start" volume="%d" loop_count="0" fade_in_time="3.0"/></Trigger>' % (n, n, DB[n]))
        out.append('  <Trigger name="ash_music_%s_stop"><Event name="ash_music_%s.ogg" type="stop" fade_out_time="3.0"/></Trigger>' % (n, n))
    out += [' </Triggers>', ' <Parameters>']
    for n in names:
        out.append('  <Parameter name="ash_vol_music_%s"><Event name="ash_music_%s.ogg"/></Parameter>' % (n, n))
    out += [' </Parameters>', ' <EditorData>', '  <Library />', '  <Folders />', '  <Controls />', ' </EditorData>', '</AudioSystemData>', '']
    text = "\n".join(out)
    ET.fromstring(text)
    with open(ACE + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(ACE + ".tmp", ACE)


if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if a in TRACKS] or list(TRACKS)
    os.makedirs(ASSETS, exist_ok=True)
    os.makedirs(os.path.dirname(ACE), exist_ok=True)
    for n in names:
        x = TRACKS[n]()
        path = write_ogg(n, x)
        seam = np.abs(x[:, 0] - x[:, -1]).max() / max(np.abs(np.diff(x[0])).mean(), 1e-9)
        print("%-7s %5.1f s  peak %.2f  rms %.3f  loop seam %.2fx a typical step -> %s (%d KB)" % (
            n, x.shape[1] / SR, np.abs(x).max(), np.sqrt((x ** 2).mean()), seam, os.path.basename(path), os.path.getsize(path) // 1024))
    # only tracks whose ogg exists, so a subset run does not register triggers for files never rendered
    write_ace([n for n in TRACKS if os.path.isfile(os.path.join(ASSETS, "ash_music_%s.ogg" % n))])
    print("wrote", ACE)
