"""Drop "notes" transcribed from passages where the guitar isn't playing.

Source separation is never perfectly clean: when the guitar is silent, the
separated guitar stem still carries a faint residue of the other instruments
(bleed), and Basic Pitch happily transcribes it — which put tab notes in
places where the recording has no guitar at all (real-track report).

Two checks at each note's attack, both on short-time loudness:

- **Quiet for this stem.** The guitar part has its own playing level; a note
  whose attack is far below it (`QUIET_DB` under the stem's loud passages) is
  residue, not playing.
- **Buried in the mix.** Bleed is, by construction, a small fraction of what's
  in the mix at that moment. Where the stem is `BLEED_DB` or more below the
  full mix, the guitar isn't what's sounding.

Both are deliberately loose: genuinely soft playing sits well above them, and
a missing ghost note costs far less than a phantom phrase.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .tab_builder import Note

QUIET_DB = 30.0
BLEED_DB = 24.0
_FRAME_SECONDS = 0.046
_HOP_SECONDS = 0.0116
_ATTACK_WINDOW = 0.15  # seconds after the onset examined for each note
_FLOOR_DB = -100.0
_REFERENCE_PERCENTILE = 95


def loudness_envelope(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """(frame times, RMS level in dBFS) of a mono signal."""
    samples = np.asarray(samples, dtype=np.float32)
    frame = max(1, int(_FRAME_SECONDS * sample_rate))
    hop = max(1, int(_HOP_SECONDS * sample_rate))
    if len(samples) < frame:
        samples = np.pad(samples, (0, frame - len(samples)))
    count = 1 + (len(samples) - frame) // hop
    windows = np.lib.stride_tricks.as_strided(
        samples, shape=(count, frame), strides=(samples.strides[0] * hop, samples.strides[0])
    )
    rms = np.sqrt(np.mean(windows.astype(np.float64) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 10 ** (_FLOOR_DB / 20)))
    times = (np.arange(count) * hop + frame / 2) / sample_rate
    return times, db


def _peak_in(times: np.ndarray, db: np.ndarray, start: float, end: float) -> float:
    lo = int(np.searchsorted(times, start, side="left"))
    hi = int(np.searchsorted(times, end, side="right"))
    if hi <= lo:  # window between frames: nearest frame
        index = min(max(lo, 0), len(db) - 1)
        return float(db[index])
    return float(db[lo:hi].max())


def filter_silent_notes(
    notes: Sequence[Note],
    stem: np.ndarray,
    stem_rate: int,
    mix: np.ndarray | None = None,
    mix_rate: int | None = None,
) -> tuple[list[Note], int]:
    """Notes whose attack is real guitar, and how many were dropped."""
    if not notes:
        return list(notes), 0
    stem_times, stem_db = loudness_envelope(stem, stem_rate)
    audible = stem_db[stem_db > _FLOOR_DB + 1]
    if audible.size == 0:
        return [], len(notes)  # a silent stem: everything transcribed from it is residue
    reference = float(np.percentile(audible, _REFERENCE_PERCENTILE))
    mix_env = loudness_envelope(mix, mix_rate or stem_rate) if mix is not None else None

    kept: list[Note] = []
    for note in notes:
        start = note.onset
        end = note.onset + min(max(note.duration, 0.02), _ATTACK_WINDOW)
        level = _peak_in(stem_times, stem_db, start, end)
        if level < reference - QUIET_DB:
            continue
        if mix_env is not None and level < _peak_in(*mix_env, start, end) - BLEED_DB:
            continue
        kept.append(note)
    return kept, len(notes) - len(kept)
