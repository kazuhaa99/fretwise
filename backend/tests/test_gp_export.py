from pathlib import Path

import guitarpro as gp

from app.pipeline.fretboard import Fretboard, note_name_to_midi
from app.pipeline.gp_export import _slot_seconds, _total_slots, export_gp5
from app.pipeline.tab_builder import Note, TabEvent, TabNote, build_tab

STANDARD = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], max_fret=22)


def _sample_tab() -> list[TabEvent]:
    base = note_name_to_midi("A2")
    notes = [
        Note(base, onset=0.0, duration=0.5),
        Note(base + 2, onset=0.6, duration=0.3),
        Note(base + 3, onset=1.0, duration=1.2),  # long sustain -> should produce tied continuations
    ]
    return build_tab(notes, STANDARD, max_fret_span=4, position_jump_weight=1.0)


def test_export_produces_a_parseable_gp5_with_correct_tuning(tmp_path):
    tab = _sample_tab()
    out = tmp_path / "out.gp5"

    export_gp5([tab], STANDARD, out, title="Smoke Test", artist="Fretwise", tempo=120)

    assert out.exists()
    song = gp.parse(str(out))
    assert song.title == "Smoke Test"
    assert song.artist == "Fretwise"
    assert len(song.tracks) == 1

    track = song.tracks[0]
    # GP numbers strings thinnest-first (1 = high E); our model is low-to-high.
    by_number = {s.number: s.value for s in track.strings}
    assert by_number == {
        6: note_name_to_midi("E2"),
        5: note_name_to_midi("A2"),
        4: note_name_to_midi("D3"),
        3: note_name_to_midi("G3"),
        2: note_name_to_midi("B3"),
        1: note_name_to_midi("E4"),
    }


def test_multi_track_export_writes_each_voice_as_its_own_named_track(tmp_path):
    # Two parts of very different lengths -- e.g. a full rhythm part alongside a
    # short lead lick that drops out early. They must land in one .gp5 as
    # separate, independently-readable tracks sharing one timeline.
    long_part = _sample_tab()
    short_part = [TabEvent(time=0.0, duration=0.3, notes=[TabNote(string=0, fret=0)])]
    out = tmp_path / "out.gp5"

    export_gp5([long_part, short_part], STANDARD, out, title="Two Guitars", tempo=120)

    song = gp.parse(str(out))
    assert [track.name for track in song.tracks] == ["Guitar 1", "Guitar 2"]

    # Every track shares the song's measure headers, so the shorter part must
    # trail off into rests rather than the file simply ending early for it.
    long_track, short_track = song.tracks
    assert len(long_track.measures) == len(short_track.measures)
    short_statuses = [beat.status for measure in short_track.measures for beat in measure.voices[0].beats]
    assert gp.BeatStatus.normal in short_statuses
    assert gp.BeatStatus.rest in short_statuses


def test_every_measure_is_exactly_one_bar_long(tmp_path):
    tab = _sample_tab()
    out = tmp_path / "out.gp5"
    export_gp5([tab], STANDARD, out, tempo=120)

    song = gp.parse(str(out))
    track = song.tracks[0]
    assert len(track.measures) >= 1
    for measure in track.measures:
        voice = measure.voices[0]
        total_ticks = sum(beat.duration.time for beat in voice.beats)
        assert total_ticks == gp.Duration.quarterTime * 4, "a measure's beats must sum to exactly one 4/4 bar"


def test_struck_notes_carry_fret_and_string_and_techniques(tmp_path):
    tab = _sample_tab()
    out = tmp_path / "out.gp5"
    export_gp5([tab], STANDARD, out, tempo=120)

    song = gp.parse(str(out))
    track = song.tracks[0]

    struck = [
        note
        for measure in track.measures
        for beat in measure.voices[0].beats
        for note in beat.notes
        if note.type == gp.NoteType.normal
    ]
    assert struck, "expected at least one struck note in the export"
    for note in struck:
        assert 0 <= note.value <= STANDARD.max_fret
        assert 1 <= note.string <= STANDARD.string_count


def test_long_sustained_note_is_notated_as_tied_continuation(tmp_path):
    # A single long note (1.2s) at a tempo where a thirty-second-note slot is short
    # should span multiple grid slots and continue as tied notes, not silence.
    tab = [TabEvent(time=0.0, duration=1.2, notes=[TabNote(string=1, fret=2)])]
    out = tmp_path / "out.gp5"
    export_gp5([tab], STANDARD, out, tempo=120)

    song = gp.parse(str(out))
    track = song.tracks[0]
    types = [
        note.type
        for measure in track.measures
        for beat in measure.voices[0].beats
        for note in beat.notes
    ]
    assert gp.NoteType.normal in types
    assert gp.NoteType.tie in types, "a sustained note should produce tied continuation notes on the grid"


def test_total_slots_always_a_whole_number_of_measures():
    tab = _sample_tab()
    for tempo in (60, 90, 120, 180):
        slots = _total_slots(tab, tempo)
        assert slots % 32 == 0
        assert slots > 0


def test_slot_seconds_scales_inversely_with_tempo():
    assert _slot_seconds(60) > _slot_seconds(120) > _slot_seconds(240)
