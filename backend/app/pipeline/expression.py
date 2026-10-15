"""Bend and vibrato detection from Basic Pitch's per-note pitch contours.

Basic Pitch reports, alongside every note, a frame-by-frame pitch offset from
the note's nominal pitch (`pitch_bends`, in 1/3-semitone steps at ~86 frames
per second). The original pipeline threw it away, so a bent note came out as
either a plain note or — worse — two unrelated notes. This module reads that
contour the way a guitarist hears it:

- **Bend:** the pitch rises to a new level and *stays* there for a while,
  possibly coming back down once (bend-and-release). Amount is rounded to the
  nearest half-step, which is how bends are notated (½, full, 1½).
- **Vibrato:** the pitch *oscillates* around the note at a regular rate
  (roughly 3–10 Hz) with a small depth — many up/down crossings rather than
  one sustained step.

There is one more case the contour of a single note cannot show. Basic Pitch
tracks notes per semitone, so a bend that travels a whole step is often
reported as note A followed immediately by note A+2. What distinguishes that
from a legato hammer-on (also A then A+2, back to back) is the *glide*: a bent
string's pitch is already rising at the end of A, while a hammer-on jumps.
`merge_split_bends` stitches such pairs back into one bent note.

Everything here is a heuristic over a coarse contour (1/3-semitone
resolution): it is tuned to stay quiet unless the evidence is clear, because a
spurious "b full" in a tab is worse than a missing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

import numpy as np

from .tab_builder import Note

# Basic Pitch contour resolution: values are in 1/3-semitone bins.
CONTOUR_BINS_PER_SEMITONE = 3

_MIN_BEND_SECONDS = 0.12  # shorter notes don't carry enough contour to judge
_MIN_BEND_SEMITONES = 0.6  # ~2 contour bins; anything smaller is a "curl" at most
_MAX_BEND_SEMITONES = 3.0  # beyond 1½ steps it's far more likely a tracking error
_BEND_HOLD_FRACTION = 0.2  # bent pitch must be held for this share of the note...
_BEND_HOLD_SECONDS = 0.06  # ...and at least this long
_MAX_BEND_TRANSITIONS = 2  # up (and optionally back down) — more is oscillation

_MIN_VIBRATO_SECONDS = 0.3
_VIBRATO_MIN_HZ = 3.0
_VIBRATO_MAX_HZ = 10.0
_VIBRATO_MIN_DEPTH = 0.25  # semitones, peak-to-peak
_VIBRATO_MAX_DEPTH = 1.5
_VIBRATO_HYSTERESIS = 0.1  # semitones around the mean before a crossing counts
_VIBRATO_MIN_CYCLES = 2

_SPLIT_BEND_MAX_GAP = 0.04  # seconds between the halves of a split bend
_SPLIT_BEND_MIN_GLIDE = 0.3  # semitones of rise in the first note's tail
_SPLIT_BEND_TAIL_FRACTION = 0.3
_CHORD_EPSILON = 0.045  # matches tab_builder's "same strike" window


@dataclass(frozen=True)
class Expression:
    kind: Literal["bend", "vibrato"]
    bend_semitones: float = 0.0
    release: bool = False


def _smooth(values: np.ndarray) -> np.ndarray:
    """Median filter (width 5) — kills single-frame tracking flicker without
    flattening a 6 Hz vibrato (about 14 frames per cycle)."""
    if len(values) < 5:
        return values
    padded = np.pad(values, 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 5)
    return np.median(windows, axis=1)


def _transitions(above: np.ndarray) -> int:
    return int(np.count_nonzero(above[1:] != above[:-1]))


def classify_contour(offsets_semitones: Sequence[float], frame_rate: float) -> Optional[Expression]:
    """Classify one note's pitch contour (semitones relative to its pitch)."""
    raw = np.asarray(offsets_semitones, dtype=float)
    if frame_rate <= 0 or len(raw) == 0:
        return None
    seconds = len(raw) / frame_rate
    if seconds < _MIN_BEND_SECONDS:
        return None

    contour = _smooth(raw)
    attack = contour[: max(3, len(contour) // 8)]
    delta = contour - float(np.median(attack))

    bend = _classify_bend(delta, frame_rate)
    if bend is not None:
        return bend
    return _classify_vibrato(delta, seconds)


def _classify_bend(delta: np.ndarray, frame_rate: float) -> Optional[Expression]:
    peak = float(np.percentile(delta, 90))
    if not _MIN_BEND_SEMITONES <= peak <= _MAX_BEND_SEMITONES + 0.25:
        return None
    above = delta >= peak * 0.6
    held = int(np.count_nonzero(above))
    if held < _BEND_HOLD_FRACTION * len(delta) or held / frame_rate < _BEND_HOLD_SECONDS:
        return None
    if _transitions(above) > _MAX_BEND_TRANSITIONS:
        return None  # up-down-up-down: that's vibrato (or noise), not a bend
    if above[0]:
        return None  # already "bent" at the attack: a mis-pitched note, not a bend

    tail = delta[-max(2, len(delta) // 7):]
    released = bool(np.mean(tail) < peak * 0.3)
    amount = min(_MAX_BEND_SEMITONES, max(0.5, round(peak * 2) / 2))
    return Expression(kind="bend", bend_semitones=amount, release=released)


def _classify_vibrato(delta: np.ndarray, seconds: float) -> Optional[Expression]:
    if seconds < _MIN_VIBRATO_SECONDS:
        return None
    depth = float(np.percentile(delta, 95) - np.percentile(delta, 5))
    if not _VIBRATO_MIN_DEPTH <= depth <= _VIBRATO_MAX_DEPTH:
        return None

    centered = delta - float(np.mean(delta))
    crossings, state = 0, 0
    for value in centered:
        if value > _VIBRATO_HYSTERESIS and state <= 0:
            crossings += state < 0
            state = 1
        elif value < -_VIBRATO_HYSTERESIS and state >= 0:
            crossings += state > 0
            state = -1
    cycles = crossings / 2
    rate = cycles / seconds
    if cycles >= _VIBRATO_MIN_CYCLES and _VIBRATO_MIN_HZ <= rate <= _VIBRATO_MAX_HZ:
        return Expression(kind="vibrato")
    return None


def apply_expression(note: Note, expression: Optional[Expression]) -> None:
    note.expression = expression.kind if expression else None
    note.bend_semitones = expression.bend_semitones if expression else 0.0
    note.bend_release = expression.release if expression else False


def _rising_tail(note: Note) -> float:
    """How far `note`'s pitch has already risen by its end (semitones)."""
    if not note.pitch_bends:
        return 0.0
    contour = _smooth(np.asarray(note.pitch_bends, dtype=float))
    tail_len = max(2, int(len(contour) * _SPLIT_BEND_TAIL_FRACTION))
    head = contour[: max(1, len(contour) - tail_len)]
    return float(contour[-tail_len:].max() - np.median(head))


def merge_split_bends(notes: list[Note]) -> list[Note]:
    """Stitch "A then A+1..3, already gliding up at the end of A" back into one
    bent note (see module docstring). A third note back at A's pitch right
    afterwards is folded in as the release."""
    ordered = sorted(notes, key=lambda n: n.onset)
    consumed: set[int] = set()
    out: list[Note] = []

    def starts_chord(index: int) -> bool:
        onset = ordered[index].onset
        return any(
            j != index and abs(ordered[j].onset - onset) <= _CHORD_EPSILON
            for j in range(max(0, index - 6), min(len(ordered), index + 7))
        )

    def follower(index: int, pitch_min: int, pitch_max: int) -> Optional[int]:
        note = ordered[index]
        end = note.onset + note.duration
        for j in range(index + 1, len(ordered)):
            other = ordered[j]
            if other.onset > end + _SPLIT_BEND_MAX_GAP:
                break
            if j in consumed or abs(other.onset - end) > _SPLIT_BEND_MAX_GAP:
                continue
            if pitch_min <= other.pitch_midi <= pitch_max:
                return j
        return None

    for i, note in enumerate(ordered):
        if i in consumed:
            continue
        target = follower(i, note.pitch_midi + 1, note.pitch_midi + int(_MAX_BEND_SEMITONES))
        if target is not None and not starts_chord(target) and _rising_tail(note) >= _SPLIT_BEND_MIN_GLIDE:
            bent = ordered[target]
            consumed.add(target)
            end = bent.onset + bent.duration
            release = False
            back = follower(target, note.pitch_midi, note.pitch_midi)
            if back is not None:
                consumed.add(back)
                end = ordered[back].onset + ordered[back].duration
                release = True
            merged = Note(
                pitch_midi=note.pitch_midi,
                onset=note.onset,
                duration=end - note.onset,
                confidence=max(note.confidence, bent.confidence),
                contour_fps=note.contour_fps,
            )
            apply_expression(
                merged,
                Expression(kind="bend", bend_semitones=float(bent.pitch_midi - note.pitch_midi), release=release),
            )
            out.append(merged)
        else:
            out.append(note)

    out.sort(key=lambda n: n.onset)
    return out


def annotate_expressions(notes: list[Note]) -> list[Note]:
    """Full pass used by transcription: stitch split bends, then classify every
    remaining note from its own contour."""
    merged = merge_split_bends(notes)
    for note in merged:
        if note.expression is None and note.pitch_bends and note.contour_fps:
            apply_expression(note, classify_contour(note.pitch_bends, note.contour_fps))
    return merged
