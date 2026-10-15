"""Meter / downbeat estimation and the beat grid used for bar lines."""

import numpy as np
import pytest

from app.pipeline.rhythm import BeatGrid, analyze_rhythm, choose_meter, grid_from_beats


def _accents(meter: int, phase: int, bars: int = 8, strong: float = 1.0, weak: float = 0.35) -> list[float]:
    return [strong if (i - phase) % meter == 0 else weak for i in range(meter * bars)]


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_choose_meter_finds_four_four_and_its_downbeat(phase):
    assert choose_meter(_accents(4, phase)) == (4, phase)


@pytest.mark.parametrize("phase", [0, 1, 2])
def test_choose_meter_finds_three_four_and_its_downbeat(phase):
    assert choose_meter(_accents(3, phase)) == (3, phase)


def test_choose_meter_defaults_to_four_four_without_clear_accents():
    assert choose_meter([1.0] * 24) == (4, 0)
    assert choose_meter([1.0, 0.5, 0.8]) == (4, 0)  # too short to judge
    assert choose_meter([0.0] * 24) == (4, 0)


def test_four_four_with_accents_on_one_and_three_stays_four_four():
    strengths = [1.0, 0.3, 0.7, 0.3] * 8
    assert choose_meter(strengths) == (4, 0)


def test_uniform_grid_positions_and_inverse():
    grid = BeatGrid.uniform(tempo=120, first_downbeat=0.25)
    assert grid.beat_position(0.25) == pytest.approx(0.0)
    assert grid.beat_position(1.25) == pytest.approx(2.0)
    assert grid.time_at(2.0) == pytest.approx(1.25)


def test_tracked_grid_follows_tempo_drift():
    # Beats slowly accelerating: a uniform grid would drift, the tracked one can't.
    times = np.cumsum([0.6] + [0.6 - 0.002 * i for i in range(40)])
    grid = grid_from_beats(times, [1.0] * len(times))
    for index in (0, 10, 25, 40):
        assert grid.beat_position(times[index]) == pytest.approx(index - grid.downbeat_index)
        assert grid.time_at(index - grid.downbeat_index) == pytest.approx(times[index])
    # Extrapolates beyond either end at the median period.
    assert grid.beat_position(times[0] - grid.beat_period) == pytest.approx(-1 - grid.downbeat_index)


def test_lead_in_creates_pickup_bars_only_when_needed():
    grid = BeatGrid.uniform(tempo=120, beats_per_measure=4, first_downbeat=1.0)  # downbeat 2 beats in
    assert grid.lead_in_beats(1.0) == 0
    assert grid.lead_in_beats(0.99) == 0  # jitter just before the downbeat is not a pickup
    assert grid.lead_in_beats(0.2) == 4  # pickup -> one whole extra bar
    assert grid.measure_start_times(4, 3) == pytest.approx([-1.0, 1.0, 3.0])


def test_grid_from_beats_folds_tempo_into_range():
    slow = np.arange(0, 40) * 1.5  # 40 BPM -> doubled to 80 by inserting off-beats
    grid = grid_from_beats(slow, [1.0] * len(slow))
    assert grid.tempo == 80
    assert len(grid.beat_times) == 2 * len(slow) - 1

    fast = np.arange(0, 80) * 0.25  # 240 BPM -> halved to 120
    grid = grid_from_beats(fast, [1.0] * len(fast))
    assert grid.tempo == 120


def _click_track(meter: int, bpm: float, first_downbeat_beat: int, beats: int = 32, sr: int = 22050):
    period = 60.0 / bpm
    start = 0.5
    y = np.zeros(int((start + beats * period + 1.0) * sr), dtype=np.float32)
    click = np.sin(2 * np.pi * 1500 * np.arange(int(0.03 * sr)) / sr) * np.hanning(int(0.03 * sr))
    for i in range(beats):
        accent = 1.0 if (i - first_downbeat_beat) % meter == 0 else 0.2
        at = int((start + i * period) * sr)
        y[at: at + len(click)] += accent * click
    return y, sr, start + first_downbeat_beat * period


@pytest.mark.parametrize(("meter", "phase"), [(4, 1), (3, 2)])
def test_analyze_rhythm_on_a_click_track(meter, phase):
    y, sr, expected_downbeat = _click_track(meter, 120, phase)
    grid = analyze_rhythm(y, sr)
    assert grid.beats_per_measure == meter
    assert abs(grid.tempo - 120) <= 2
    # The detected downbeat must be one of the accented clicks.
    period = 60.0 / 120
    offset = (grid.first_downbeat - expected_downbeat) / (period * meter)
    assert abs(offset - round(offset)) < 0.1


def test_analyze_rhythm_survives_silence():
    grid = analyze_rhythm(np.zeros(22050, dtype=np.float32), 22050)
    assert grid.beats_per_measure == 4
    assert 55 <= grid.tempo <= 200
