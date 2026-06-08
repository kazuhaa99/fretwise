import random

from app.pipeline.fretboard import Fretboard, note_name_to_midi
from app.pipeline.tab_builder import (
    Note,
    _fallback_voicings,
    _merge_oversegmented_sustains,
    _voicings_for_pitches,
    build_multi_track_tab,
    build_tab,
    group_into_events,
    separate_voices,
)

STANDARD = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], max_fret=22)


# ---------------------------------------------------------------------------
# The core guarantee: physically impossible shapes are never produced
# ---------------------------------------------------------------------------

def test_wide_chord_is_never_produced_directly():
    # F2, G#3, A5 — a low note, a mid note and a very high note. No single hand
    # position can fret all three within a comfortable stretch (this is the
    # "1-3-15-17-18 in one chord" scenario the project exists to prevent).
    pitches = [note_name_to_midi(n) for n in ("F2", "G#3", "A5")]

    assert _voicings_for_pitches(STANDARD, pitches, max_span=4) == []

    for voicing in _voicings_for_pitches(STANDARD, pitches, max_span=22):
        # even if we *did* allow a huge span, every produced voicing must still
        # respect whatever span was requested
        fretted = [f for _, f in voicing if f > 0]
        if len(fretted) > 1:
            assert max(fretted) - min(fretted) <= 22


def test_unplayable_chord_falls_back_to_largest_playable_subset():
    pitches = [note_name_to_midi(n) for n in ("F2", "G#3", "A5")]

    voicings, kept = _fallback_voicings(STANDARD, pitches, max_span=4)

    assert voicings, "expected a playable reduced voicing to exist"
    assert len(kept) == 2, "should drop only the single note that breaks playability"
    for voicing in voicings:
        strings = [s for s, _ in voicing]
        assert len(strings) == len(set(strings))
        fretted = [f for _, f in voicing if f > 0]
        if len(fretted) > 1:
            assert max(fretted) - min(fretted) <= 4


def test_voicings_never_double_up_a_string():
    # A stack of pitches that are easy to reach on overlapping strings
    pitches = [note_name_to_midi(n) for n in ("E3", "G3", "B3", "E4")]
    for voicing in _voicings_for_pitches(STANDARD, pitches, max_span=4):
        strings = [s for s, _ in voicing]
        assert len(strings) == len(set(strings))


def test_build_tab_never_emits_unplayable_chords_under_random_input():
    rng = random.Random(1234)
    notes: list[Note] = []
    t = 0.0
    for _ in range(120):
        cluster = rng.randint(1, 4)  # occasionally stack simultaneous notes into chords
        for _ in range(cluster):
            pitch = rng.randint(40, 88)
            notes.append(Note(pitch_midi=pitch, onset=t, duration=0.4, confidence=1.0))
        t += rng.uniform(0.15, 0.6)

    for max_span in (3, 4, 5):
        tab = build_tab(notes, STANDARD, max_fret_span=max_span, position_jump_weight=1.0)
        assert tab
        for event in tab:
            strings_used = [n.string for n in event.notes]
            assert len(strings_used) == len(set(strings_used)), "two notes landed on one string"

            fretted = [n.fret for n in event.notes if n.fret > 0]
            if len(fretted) > 1:
                span = max(fretted) - min(fretted)
                assert span <= max_span, f"span {span} > allowed {max_span}: {event.notes}"

            for note in event.notes:
                assert 0 <= note.fret <= STANDARD.max_fret
                assert 0 <= note.string < STANDARD.string_count


# ---------------------------------------------------------------------------
# Grouping notes into simultaneous strikes
# ---------------------------------------------------------------------------

def test_group_into_events_clusters_simultaneous_notes():
    notes = [
        Note(60, onset=0.000, duration=0.5),
        Note(64, onset=0.010, duration=0.5),  # same strum
        Note(67, onset=0.020, duration=0.5),  # same strum
        Note(72, onset=0.600, duration=0.3),  # later, separate strike
    ]
    events = group_into_events(notes)
    assert [pitches for _, _, pitches in events] == [[60, 64, 67], [72]]


def test_group_into_events_dedupes_exact_pitch_repeats():
    notes = [
        Note(60, onset=0.0, duration=0.3),
        Note(60, onset=0.005, duration=0.3),  # spurious double-detection of the same pitch
    ]
    events = group_into_events(notes)
    assert len(events) == 1
    assert events[0][2] == [60]


# ---------------------------------------------------------------------------
# Sustain merging: a held tone shouldn't surface as a wall of repeated strikes
# ---------------------------------------------------------------------------

def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_oversegmented_sustain_gets_merged_into_one_held_note():
    # One sustained tone the transcriber chopped into ragged fragments: each
    # piece butts against the next with no real silence (tiny gaps), but the
    # split points land at irregular instants -- exactly what confidence-wobble
    # noise (vibrato, decay, harmonic beating), not a player's hand, produces.
    notes = [
        Note(pitch_midi=64, onset=0.00, duration=0.295, confidence=0.9),
        Note(pitch_midi=64, onset=0.30, duration=0.195, confidence=0.8),
        Note(pitch_midi=64, onset=0.50, duration=0.495, confidence=0.85),
        Note(pitch_midi=64, onset=1.00, duration=0.20, confidence=0.7),
    ]

    merged = _merge_oversegmented_sustains(notes)

    assert len(merged) == 1
    assert merged[0].pitch_midi == 64
    assert _close(merged[0].onset, 0.0)
    assert _close(merged[0].duration, 1.20)


def test_regular_fast_repeats_with_tiny_gaps_are_left_for_tremolo_detection():
    # Mechanically even, fast restrikes with near-zero gaps -- a deliberate
    # tremolo, not a chopped-up tone (which would be irregular). Left alone
    # here; `_annotate_techniques` is what marks runs like this `tremolo`,
    # once it knows which string they land on.
    notes = [Note(pitch_midi=64, onset=t, duration=0.105, confidence=0.9) for t in (0.0, 0.12, 0.24, 0.36, 0.48)]

    merged = _merge_oversegmented_sustains(notes)

    assert len(merged) == len(notes)


def test_deliberate_slower_restrikes_are_left_untouched():
    notes = [Note(pitch_midi=64, onset=t, duration=0.18, confidence=0.9) for t in (0.0, 0.5, 1.0, 1.5)]

    merged = _merge_oversegmented_sustains(notes)

    assert len(merged) == len(notes)


def test_merged_sustain_survives_into_a_single_tied_tab_event():
    base = note_name_to_midi("B3")
    notes = [
        Note(base, onset=0.00, duration=0.49, confidence=0.9),
        Note(base, onset=0.50, duration=0.09, confidence=0.8),
        Note(base, onset=0.60, duration=0.40, confidence=0.85),
    ]

    tab = build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)

    assert len(tab) == 1
    assert _close(tab[0].duration, 1.00)


# ---------------------------------------------------------------------------
# Voice separation: a merged "guitar" stem can still hold >1 real performance
# ---------------------------------------------------------------------------

# A pair of pitches no single hand position can sound together (span 12 frets
# at best) but each fits trivially alone -- the cleanest possible stand-in for
# "two different guitars happened to land in the same separated stem."
_LOW_CONFLICT_PITCH = note_name_to_midi("E2") + 2    # fret 2 on the low E string
_HIGH_CONFLICT_PITCH = note_name_to_midi("B3") + 14  # fret 14 on the B string


def test_monophonic_melody_is_not_split():
    base = note_name_to_midi("A2")
    notes = [Note(base + i, onset=i * 0.3, duration=0.25) for i in (0, 2, 3, 5, 7, 5, 3, 2)]

    voices = separate_voices(notes, STANDARD, max_fret_span=4)

    assert voices == [notes]


def test_sustained_simultaneous_conflict_is_split_into_two_voices():
    # The same two irreconcilable pitches, struck together, over and over, for
    # most of the piece -- exactly what "two real guitars in one stem" looks
    # like at the note level: a real, ongoing second performance, not noise.
    notes = []
    t = 0.0
    for _ in range(20):
        notes.append(Note(_LOW_CONFLICT_PITCH, onset=t, duration=0.35))
        notes.append(Note(_HIGH_CONFLICT_PITCH, onset=t, duration=0.35))
        t += 0.4

    voices = separate_voices(notes, STANDARD, max_fret_span=4)

    assert len(voices) == 2
    voice_pitch_sets = [set(n.pitch_midi for n in voice) for voice in voices]
    assert {_LOW_CONFLICT_PITCH} in voice_pitch_sets
    assert {_HIGH_CONFLICT_PITCH} in voice_pitch_sets
    assert sum(len(v) for v in voices) == len(notes)


def test_sparse_conflicts_read_as_noise_not_a_second_voice():
    # The same irreconcilable pair, but only twice in a twenty-note piece --
    # exactly the "stray bleed-through pitch" case a second track shouldn't
    # be conjured for.
    base = note_name_to_midi("A2")
    melody = [Note(base + i, onset=i * 0.3, duration=0.25) for i in range(20)]
    stray = [
        Note(_LOW_CONFLICT_PITCH, onset=12.0, duration=0.2),
        Note(_HIGH_CONFLICT_PITCH, onset=12.0, duration=0.2),
        Note(_LOW_CONFLICT_PITCH, onset=14.0, duration=0.2),
        Note(_HIGH_CONFLICT_PITCH, onset=14.0, duration=0.2),
    ]

    voices = separate_voices(melody + stray, STANDARD, max_fret_span=4)

    assert len(voices) == 1


def test_multi_track_tab_arranges_each_voice_under_the_same_constraints():
    notes = []
    t = 0.0
    for _ in range(20):
        notes.append(Note(_LOW_CONFLICT_PITCH, onset=t, duration=0.35))
        notes.append(Note(_HIGH_CONFLICT_PITCH, onset=t, duration=0.35))
        t += 0.4

    tracks = build_multi_track_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)

    assert len(tracks) == 2
    for track in tracks:
        assert track
        for event in track:
            strings_used = [n.string for n in event.notes]
            assert len(strings_used) == len(set(strings_used))
            fretted = [n.fret for n in event.notes if n.fret > 0]
            if len(fretted) > 1:
                assert max(fretted) - min(fretted) <= 4


# ---------------------------------------------------------------------------
# Hand-position continuity: prefers staying put over jumping around the neck
# ---------------------------------------------------------------------------

def test_prefers_minimal_hand_movement_across_a_phrase():
    # A short melodic run that is reachable either as a tight run in one
    # position, or scattered across the neck. With a high jump-weight the
    # optimizer should keep the hand roughly still.
    base = note_name_to_midi("A2")
    notes = [Note(base + i, onset=i * 0.3, duration=0.25) for i in (0, 2, 3, 5)]

    tab = build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=2.0)
    fretted_positions = [n.fret for e in tab for n in e.notes if n.fret > 0]

    assert fretted_positions, "expected at least some fretted notes"
    assert max(fretted_positions) - min(fretted_positions) <= 5


# ---------------------------------------------------------------------------
# Articulation heuristics
# ---------------------------------------------------------------------------

def test_legato_same_string_runs_get_hammer_on_and_pull_off():
    base = note_name_to_midi("E2")
    notes = [
        Note(base + 2, onset=0.00, duration=0.20),  # fret 2
        Note(base + 4, onset=0.21, duration=0.20),  # fret 4, ascending, short gap -> hammer-on
        Note(base + 2, onset=0.42, duration=0.20),  # fret 2, descending, short gap -> pull-off
    ]
    tab = build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)
    techniques = [n.technique for event in tab for n in event.notes]

    assert "hammer_on" in techniques
    assert "pull_off" in techniques


def test_fast_even_restrikes_get_marked_tremolo():
    # Same pitch, repeating quickly and at a mechanically even rate -- a
    # deliberate tremolo, not a chopped-up sustain (that's irregular, and
    # would have been merged away before this point) or a slow restrike
    # (that's just plain repeated numbers, with no marking at all).
    base = note_name_to_midi("A2") + 5
    notes = [Note(base, onset=t, duration=0.10, confidence=0.9) for t in (0.0, 0.12, 0.24, 0.36, 0.48)]

    tab = build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)
    techniques = [n.technique for event in tab for n in event.notes]

    assert techniques.count("tremolo") == len(notes)


def test_slow_deliberate_restrikes_get_no_special_marking():
    base = note_name_to_midi("A2") + 5
    notes = [Note(base, onset=t, duration=0.20, confidence=0.9) for t in (0.0, 0.6, 1.2, 1.8)]

    tab = build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)
    techniques = [n.technique for event in tab for n in event.notes]

    assert all(t is None for t in techniques)
