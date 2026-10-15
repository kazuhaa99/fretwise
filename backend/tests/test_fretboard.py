from app.pipeline.fretboard import Fretboard, midi_to_note_name, note_name_to_midi


def test_note_name_round_trip():
    assert note_name_to_midi("C4") == 60
    assert note_name_to_midi("E2") == 40
    assert note_name_to_midi("F#3") == 54
    assert note_name_to_midi("Eb4") == 63
    assert midi_to_note_name(60) == "C4"
    assert midi_to_note_name(40) == "E2"


def test_standard_tuning_open_strings():
    fb = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"])
    assert fb.string_count == 6
    assert fb.open_pitch(0) == note_name_to_midi("E2")
    assert fb.open_pitch(5) == note_name_to_midi("E4")


def test_candidates_cover_every_reachable_string():
    fb = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], max_fret=22)
    # Open high E (E4) is also reachable as fret 5 on the B string, fret 9 on G, etc.
    candidates = fb.candidates(note_name_to_midi("E4"))
    assert (5, 0) in candidates  # open high E string
    assert (4, 5) in candidates  # 5th fret of B string
    for string_index, fret in candidates:
        assert 0 <= fret <= 22


def test_candidates_respect_fret_ceiling():
    fb = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], max_fret=12)
    # A pitch only reachable above the fret ceiling on a given string is excluded
    very_high = note_name_to_midi("E4") + 13
    for string_index, fret in fb.candidates(very_high):
        assert fret <= 12


def test_capo_shifts_open_pitch_and_fret_numbering():
    plain = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], capo=0)
    capoed = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], capo=2)

    # With a capo on fret 2, "fret 0" on the low string now sounds a whole step higher
    assert capoed.open_pitch(0) == plain.open_pitch(0) + 2

    # The same sounding pitch is reached two frets lower relative to the capo
    target = plain.open_pitch(0) + 5
    assert (0, 5) in plain.candidates(target)
    assert (0, 3) in capoed.candidates(target)


def test_capo_reduces_the_frets_available_above_it():
    fb = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], capo=5, max_fret=22)
    assert fb.frets_above_capo == 17
    top = note_name_to_midi("E4") + 22  # 22nd fret from the nut on the high E
    assert (5, 17) in fb.candidates(top)
    assert not any(fret > 17 for _, fret in fb.candidates(top - 1))
    assert fb.candidates(top + 1) == []
