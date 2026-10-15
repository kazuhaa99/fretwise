"""Notes from separation residue (guitar silent) are dropped; real playing stays."""

import numpy as np

from app.pipeline.activity import filter_silent_notes, loudness_envelope
from app.pipeline.tab_builder import Note

SR = 22050


def _tone(seconds, freq, level_db, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (10 ** (level_db / 20)) * np.sin(2 * np.pi * freq * t).astype(np.float32)


def test_loudness_envelope_reads_levels():
    times, db = loudness_envelope(np.concatenate([_tone(1, 220, -6), _tone(1, 220, -40)]), SR)
    assert db[times < 0.9].mean() > -12
    assert db[times > 1.1].mean() < -40


def _scene():
    """0–2 s guitar plays; 2–4 s only drums/bass in the mix, faint bleed in the stem;
    4–6 s guitar plays softly (but clearly) under a loud mix."""
    guitar = np.concatenate([_tone(2, 196, -10), _tone(2, 330, -62), _tone(2, 196, -26)])
    others = np.concatenate([_tone(2, 60, -20), _tone(2, 60, -8), _tone(2, 60, -12)])
    return guitar, guitar + others


def test_bleed_while_the_guitar_rests_is_dropped():
    stem, mix = _scene()
    notes = [Note(55, onset=0.5, duration=0.4), Note(64, onset=2.5, duration=0.4), Note(64, onset=3.5, duration=0.3)]
    kept, dropped = filter_silent_notes(notes, stem, SR, mix, SR)
    assert [n.onset for n in kept] == [0.5]
    assert dropped == 2


def test_soft_but_real_playing_under_a_loud_mix_is_kept():
    stem, mix = _scene()
    kept, _ = filter_silent_notes([Note(55, onset=4.5, duration=0.4)], stem, SR, mix, SR)
    assert len(kept) == 1


def test_stem_only_check_without_a_mix():
    stem, _ = _scene()
    kept, dropped = filter_silent_notes([Note(55, onset=1.0, duration=0.3), Note(64, onset=3.0, duration=0.3)], stem, SR)
    assert [n.onset for n in kept] == [1.0] and dropped == 1


def test_a_silent_stem_yields_nothing_and_empty_input_is_fine():
    assert filter_silent_notes([Note(60, onset=0.1, duration=0.2)], np.zeros(SR, dtype=np.float32), SR) == ([], 1)
    assert filter_silent_notes([], np.zeros(SR, dtype=np.float32), SR) == ([], 0)
