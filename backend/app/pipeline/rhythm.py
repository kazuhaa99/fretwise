"""Beat tracking, meter and downbeat estimation for laying a tab out in bars.

Tab events are timed in seconds, but a Guitar Pro file is a sequence of bars.
The original export simply cut absolute time into 4/4 bars starting at 0.0s:
any recording that didn't start exactly on a downbeat had every bar line in
the wrong place, and since GP tempos are integers, even a perfectly steady
117.4 BPM performance drifted a little further off the grid with every bar
("the measures float").

This module answers the questions a transcriber would ask before writing bar
lines:

- **Where are the beats?** librosa's beat tracker gives actual beat instants.
  Positions between them are interpolated, so the grid follows the performer
  (including gentle tempo drift) instead of an idealized metronome.
- **How many beats per bar?** We compare how strongly onsets are accented on
  every 3rd vs every 4th beat (`choose_meter`). Only 3/4 and 4/4 are
  distinguished — they cover the overwhelming majority of guitar music, and
  finer distinctions (6/8 vs 3/4, 2/4 vs 4/4) are not reliably recoverable
  from onset strength alone.
- **Which beat is "one"?** The phase with the strongest accents. Everything
  before it becomes a pickup measure (`BeatGrid.lead_in_beats`).

All of it is best-effort: when beat tracking finds nothing usable we fall back
to a uniform 4/4 grid at the estimated (or default) tempo, which is exactly the
old behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Sequence

import numpy as np

DEFAULT_TEMPO = 120
MIN_TEMPO = 55
MAX_TEMPO = 200
DEFAULT_BEATS_PER_MEASURE = 4

# Need at least this many tracked beats (two bars of 4/4) before trusting any
# meter/downbeat estimate; shorter clips get the plain 4/4 default.
_MIN_BEATS_FOR_METER = 8

# How much more clearly 3/4 must be accented than 4/4 before we pick it. 4/4 is
# far more common, and a wrong "3/4" scrambles every bar line, so ties and
# near-ties go to 4/4.
_METER_MARGIN = 0.15

# Below this normalized accent contrast there is no meaningful "one" to find
# (e.g. evenly strummed eighths); keep the default 4/4 with phase 0 rather
# than letting noise pick a phase.
_MIN_ACCENT_CONTRAST = 0.05

# Onset strength is sampled as the max over +-this many frames around each
# beat, because tracked beats and audible attacks rarely land on the very
# same frame.
_BEAT_STRENGTH_RADIUS_FRAMES = 2


def _fold_tempo(bpm: float) -> float:
    """Halve/double an estimate into [MIN_TEMPO, MAX_TEMPO] instead of clamping.

    Beat trackers commonly lock onto half or double the felt tempo; folding by
    octaves keeps beat times and tempo consistent, whereas clamping 230 BPM to
    200 would make the grid disagree with the very beats it was built from.
    """
    while bpm > MAX_TEMPO:
        bpm /= 2
    while bpm < MIN_TEMPO:
        bpm *= 2
    return bpm


@dataclass(frozen=True)
class BeatGrid:
    """Maps time in seconds to musical position in beats, and back.

    With `beat_times` (tracked beats) the mapping interpolates between actual
    beats and extrapolates past either end at the median beat period. Without
    them it is a uniform grid at `tempo` whose beat 0 sits at `origin`.
    Position 0 is always the first downbeat; negative positions are pickup.
    """

    tempo: int = DEFAULT_TEMPO
    beats_per_measure: int = DEFAULT_BEATS_PER_MEASURE
    beat_times: tuple[float, ...] = ()
    downbeat_index: int = 0
    origin: float = 0.0  # first downbeat for a uniform grid (ignored with beat_times)

    @staticmethod
    def uniform(tempo: int = DEFAULT_TEMPO, beats_per_measure: int = DEFAULT_BEATS_PER_MEASURE,
                first_downbeat: float = 0.0) -> "BeatGrid":
        return BeatGrid(tempo=int(tempo), beats_per_measure=beats_per_measure, origin=first_downbeat)

    @property
    def _tracked(self) -> bool:
        return len(self.beat_times) >= 2

    @property
    def beat_period(self) -> float:
        if self._tracked:
            return _mean_period(self.beat_times)
        return 60.0 / self.tempo

    @property
    def first_downbeat(self) -> float:
        return self.beat_times[self.downbeat_index] if self._tracked else self.origin

    def beat_position(self, seconds: float) -> float:
        """Musical position of `seconds`, in beats from the first downbeat."""
        if not self._tracked:
            return (seconds - self.origin) / self.beat_period
        times = self.beat_times
        period = self.beat_period
        if seconds < times[0]:
            index = (seconds - times[0]) / period
        elif seconds > times[-1]:
            index = len(times) - 1 + (seconds - times[-1]) / period
        else:
            index = float(np.interp(seconds, times, np.arange(len(times))))
        return index - self.downbeat_index

    def time_at(self, position: float) -> float:
        """Inverse of `beat_position`: seconds at a position in beats."""
        if not self._tracked:
            return self.origin + position * self.beat_period
        times = self.beat_times
        period = self.beat_period
        index = position + self.downbeat_index
        if index < 0:
            return times[0] + index * period
        if index > len(times) - 1:
            return times[-1] + (index - (len(times) - 1)) * period
        return float(np.interp(index, np.arange(len(times)), times))

    def lead_in_beats(self, earliest_seconds: float) -> int:
        """Whole pickup bars (in beats) needed so nothing lands before bar 1.

        Rounded to the export's grid resolution first, so a note a hair before
        the downbeat (attack-detection jitter) doesn't spawn an empty bar.
        """
        position = self.beat_position(earliest_seconds)
        if position >= -1 / 16:
            return 0
        bars = ceil(-position / self.beats_per_measure - 1e-9)
        return bars * self.beats_per_measure

    def measure_start_times(self, lead_in_beats: int, measure_count: int) -> list[float]:
        """Start time (seconds) of every exported bar — for drawing bar lines."""
        return [
            self.time_at(index * self.beats_per_measure - lead_in_beats)
            for index in range(measure_count)
        ]


DEFAULT_GRID = BeatGrid()


def _mean_period(beat_times: Sequence[float]) -> float:
    """Least-squares beat period (slope of time vs. beat index).

    Tracked beats are quantized to analysis frames (~23 ms with librosa's
    defaults), so individual intervals at 120 BPM come out as 0.488 s or
    0.511 s and their median lands on one of those — 117 BPM instead of 120.
    A regression over all beats averages that quantization away.
    """
    times = np.asarray(beat_times, dtype=float)
    return float(np.polyfit(np.arange(len(times)), times, 1)[0])


def choose_meter(beat_strengths: Sequence[float]) -> tuple[int, int]:
    """Pick (beats_per_measure, downbeat_phase) from per-beat accent strengths.

    For each candidate meter m and phase p, the score is how much stronger the
    beats p, p+m, p+2m, ... are than all the others, normalized by the overall
    mean so loud and quiet recordings compare alike. 4/4 wins unless 3/4 is
    clearly better (`_METER_MARGIN`).
    """
    strengths = np.asarray(beat_strengths, dtype=float)
    if len(strengths) < _MIN_BEATS_FOR_METER or not np.isfinite(strengths).all():
        return DEFAULT_BEATS_PER_MEASURE, 0
    overall = strengths.mean()
    if overall <= 0:
        return DEFAULT_BEATS_PER_MEASURE, 0

    def best(meter: int) -> tuple[float, int]:
        best_score, best_phase = -np.inf, 0
        for phase in range(meter):
            mask = np.zeros(len(strengths), dtype=bool)
            mask[phase::meter] = True
            score = (strengths[mask].mean() - strengths[~mask].mean()) / overall
            if score > best_score + 1e-9:  # ties keep the earliest phase
                best_score, best_phase = score, phase
        return best_score, best_phase

    score4, phase4 = best(4)
    score3, phase3 = best(3)
    if score3 > score4 + _METER_MARGIN and score3 >= _MIN_ACCENT_CONTRAST:
        return 3, phase3
    if score4 < _MIN_ACCENT_CONTRAST:
        return DEFAULT_BEATS_PER_MEASURE, 0
    return 4, phase4


def grid_from_beats(beat_times: Sequence[float], beat_strengths: Sequence[float]) -> BeatGrid:
    """Build a grid from tracked beats (seconds) and their accent strengths."""
    times = [float(t) for t in beat_times]
    if len(times) < 2:
        return DEFAULT_GRID
    strengths = list(beat_strengths)

    bpm = 60.0 / _mean_period(times)
    folded = _fold_tempo(bpm)
    if folded > bpm * 1.5:  # tracked at half tempo: add the missing off-beats
        mids = [(a + b) / 2 for a, b in zip(times, times[1:])]
        times = sorted(times + mids)
        strengths = [s for pair in zip(strengths, [0.0] * len(mids)) for s in pair] + strengths[len(mids):]
    elif folded < bpm / 1.5:  # tracked at double tempo: keep every other beat
        times, strengths = times[::2], strengths[::2]

    meter, phase = choose_meter(strengths[: len(times)])
    return BeatGrid(
        tempo=int(round(_fold_tempo(60.0 / _mean_period(times)))),
        beats_per_measure=meter,
        beat_times=tuple(times),
        downbeat_index=min(phase, len(times) - 1),
    )


def analyze_rhythm(samples, sample_rate: int) -> BeatGrid:
    """Best-effort beat grid for a mono signal (the isolated guitar stem).

    Never raises: sparse or rubato playing can leave the tracker with nothing
    to lock onto, and a rhythm guess is not worth failing a whole job over.
    """
    import librosa

    try:
        onset_env = librosa.onset.onset_strength(y=samples, sr=sample_rate)
        tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sample_rate)
        bpm = float(np.atleast_1d(tempo)[0])
    except Exception:
        return DEFAULT_GRID

    if len(beat_frames) < 2:
        return BeatGrid.uniform(int(round(_fold_tempo(bpm))) if bpm > 0 else DEFAULT_TEMPO)

    radius = _BEAT_STRENGTH_RADIUS_FRAMES
    strengths = [
        float(onset_env[max(0, frame - radius): frame + radius + 1].max()) for frame in beat_frames
    ]
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate)
    return grid_from_beats(beat_times, strengths)


def snap(position_beats: float, slots_per_beat: int) -> int:
    """Nearest grid slot for a position in beats (round half up, consistently)."""
    return floor(position_beats * slots_per_beat + 0.5)
