"""Bend / vibrato detection from synthetic Basic Pitch-style pitch contours.

Contours are built the way Basic Pitch reports them — quantized to 1/3
semitone at ~86 frames/s — so the thresholds are exercised at the real
resolution, not on idealized smooth curves.
"""

import numpy as np
import pytest

from app.pipeline.expression import annotate_expressions, classify_contour, merge_split_bends
from app.pipeline.fretboard import Fretboard, note_name_to_midi
from app.pipeline.tab_builder import Note, build_tab

FPS = 86.0
STANDARD = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"])


def _quantize(semitones: np.ndarray) -> list[float]:
    return list(np.round(semitones * 3) / 3)


def _bend_contour(amount: float, seconds: float = 0.6, release: bool = False) -> list[float]:
    n = int(seconds * FPS)
    t = np.arange(n) / n
    rise = np.clip((t - 0.1) / 0.15, 0, 1) * amount  # attack at pitch, bend over ~90 ms
    if release:
        rise = rise * np.clip((0.85 - t) / 0.15, 0, 1)
    return _quantize(rise)


def _vibrato_contour(rate_hz: float = 6.0, depth: float = 0.4, seconds: float = 0.8) -> list[float]:
    t = np.arange(int(seconds * FPS)) / FPS
    return _quantize(depth / 2 * np.sin(2 * np.pi * rate_hz * t))


@pytest.mark.parametrize(("amount", "expected"), [(1.0, 1.0), (2.0, 2.0), (1.4, 1.5)])
def test_detects_held_bends_and_rounds_to_half_steps(amount, expected):
    expression = classify_contour(_bend_contour(amount), FPS)
    assert expression is not None and expression.kind == "bend"
    assert expression.bend_semitones == expected
    assert not expression.release


def test_detects_bend_and_release():
    expression = classify_contour(_bend_contour(2.0, release=True), FPS)
    assert expression is not None and expression.kind == "bend"
    assert expression.release


def test_detects_vibrato():
    expression = classify_contour(_vibrato_contour(), FPS)
    assert expression is not None and expression.kind == "vibrato"


@pytest.mark.parametrize(
    "contour",
    [
        [0.0] * 60,  # perfectly steady note
        _quantize(np.full(60, 0.0) + (np.arange(60) % 17 == 0) / 3),  # sparse single-frame flicker
        _vibrato_contour(rate_hz=1.0),  # too slow to be vibrato
        _vibrato_contour(depth=3.0),  # too wide to be vibrato
        _bend_contour(2.0, seconds=0.08),  # too short to judge
        _quantize(np.full(60, 2.0)),  # "bent" from the very first frame: a mis-pitched note
    ],
)
def test_stays_quiet_without_clear_evidence(contour):
    assert classify_contour(contour, FPS) is None


def test_wide_vibrato_is_not_mistaken_for_a_bend():
    expression = classify_contour(_vibrato_contour(depth=1.2), FPS)
    assert expression is None or expression.kind == "vibrato"


def _note(pitch: str, onset: float, duration: float, contour=None) -> Note:
    return Note(note_name_to_midi(pitch), onset=onset, duration=duration, pitch_bends=contour, contour_fps=FPS)


def _gliding_tail(seconds: float, rise: float) -> list[float]:
    n = int(seconds * FPS)
    t = np.arange(n) / n
    return _quantize(np.clip((t - 0.7) / 0.3, 0, 1) * rise)


def test_split_bend_is_stitched_into_one_bent_note():
    first = _note("G3", 0.0, 0.3, _gliding_tail(0.3, 0.67))
    second = _note("A3", 0.3, 0.4, [0.0] * 34)
    merged = merge_split_bends([first, second])
    assert len(merged) == 1
    note = merged[0]
    assert note.pitch_midi == note_name_to_midi("G3")
    assert note.expression == "bend" and note.bend_semitones == 2.0
    assert note.duration == pytest.approx(0.7)
    assert not note.bend_release


def test_split_bend_with_return_becomes_bend_release():
    notes = [
        _note("G3", 0.0, 0.3, _gliding_tail(0.3, 0.67)),
        _note("A3", 0.3, 0.3, [0.0] * 26),
        _note("G3", 0.6, 0.3, [0.0] * 26),
    ]
    merged = merge_split_bends(notes)
    assert len(merged) == 1
    assert merged[0].bend_release
    assert merged[0].duration == pytest.approx(0.9)


def test_hammer_on_without_glide_is_left_alone():
    notes = [_note("G3", 0.0, 0.3, [0.0] * 26), _note("A3", 0.3, 0.3, [0.0] * 26)]
    assert len(merge_split_bends(notes)) == 2


def test_note_into_a_chord_is_not_merged():
    notes = [
        _note("G3", 0.0, 0.3, _gliding_tail(0.3, 0.67)),
        _note("A3", 0.3, 0.3, [0.0] * 26),
        _note("E4", 0.31, 0.3, [0.0] * 26),
    ]
    assert len(merge_split_bends(notes)) == 3


def test_bend_and_vibrato_reach_the_tab():
    notes = annotate_expressions([
        _note("D4", 0.0, 0.6, _bend_contour(2.0)),
        _note("A3", 1.0, 0.8, _vibrato_contour()),
        _note("E3", 2.0, 0.4, [0.0] * 34),
    ])
    tab = build_tab(notes, STANDARD)
    techniques = [(event.notes[0].technique, event.notes[0].bend_semitones) for event in tab]
    assert techniques[0] == ("bend", 2.0)
    assert techniques[1] == ("vibrato", None)
    assert techniques[2][0] is None


def test_vibrato_split_into_fragments_is_recovered_when_merged():
    # A wobbling sustain chopped into near-contiguous fragments at irregular
    # boundaries: each piece alone is too short to read as vibrato.
    contour = _vibrato_contour(seconds=0.9)
    cuts = [0, 20, 33, 58, len(contour)]
    fragments = []
    for a, b in zip(cuts, cuts[1:]):
        fragments.append(_note("A3", a / FPS, (b - a) / FPS - 0.005, contour[a:b]))
    tab = build_tab(annotate_expressions(fragments), STANDARD)
    assert len(tab) == 1
    assert tab[0].notes[0].technique == "vibrato"
