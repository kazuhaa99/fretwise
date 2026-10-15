from pathlib import Path

import guitarpro as gp
import pytest

from app.pipeline.fretboard import Fretboard, note_name_to_midi
from app.pipeline.gp_export import _slot_seconds, _total_slots, export_gp5, track_name
from app.pipeline.rhythm import BeatGrid
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


def test_track_name_labels_single_and_multiple_parts():
    assert track_name(0, 1) == "Guitar"
    assert [track_name(i, 2) for i in range(2)] == ["Guitar 1", "Guitar 2"]


def test_export_with_cyrillic_title_uses_cp1251(tmp_path):
    out = tmp_path / "ru.gp5"
    export_gp5([_sample_tab()], STANDARD, out, title="Звезда по имени Солнце", artist="Fretwise", tempo=120)
    assert gp.parse(str(out), encoding="cp1251").title == "Звезда по имени Солнце"


def test_export_with_latin_title_stays_cp1252(tmp_path):
    out = tmp_path / "fr.gp5"
    export_gp5([_sample_tab()], STANDARD, out, title="Café Señor", artist="Fretwise", tempo=120)
    assert gp.parse(str(out)).title == "Café Señor"


def test_export_with_unencodable_title_degrades_instead_of_crashing(tmp_path):
    out = tmp_path / "mixed.gp5"
    export_gp5([_sample_tab()], STANDARD, out, title="ギター riff", artist="Fretwise", tempo=120)
    assert gp.parse(str(out)).title.endswith(" riff")


def test_capo_is_written_as_capo_not_folded_into_tuning(tmp_path):
    capo = 3
    fretboard = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], capo=capo, max_fret=22)
    notes = [
        Note(note_name_to_midi("C3"), onset=0.0, duration=0.4),
        Note(note_name_to_midi("G3"), onset=0.5, duration=0.4),
        Note(note_name_to_midi("D4"), onset=1.0, duration=0.4),
    ]
    tab = build_tab(notes, fretboard, max_fret_span=4, position_jump_weight=1.0)
    out = tmp_path / "capo.gp5"
    export_gp5([tab], fretboard, out, tempo=120)

    track = gp.parse(str(out)).tracks[0]
    assert track.offset == capo
    # The real tuning, not the capo'd one.
    assert {s.number: s.value for s in track.strings}[6] == note_name_to_midi("E2")
    assert {s.number: s.value for s in track.strings}[1] == note_name_to_midi("E4")

    # Guitar Pro sounds `string tuning + capo + fret`; that must reproduce the
    # transcribed pitches exactly.
    sounded = [
        track.strings[note.string - 1].value + track.offset + note.value
        for measure in track.measures
        for beat in measure.voices[0].beats
        for note in beat.notes
        if note.type == gp.NoteType.normal
    ]
    assert sounded == [n.pitch_midi for n in notes]


def test_three_four_grid_writes_three_four_bars_aligned_to_the_downbeat(tmp_path):
    # Downbeat at 0.75s (120 BPM, 3/4): a note on it must open bar 2, and the
    # earlier note becomes a pickup in bar 1.
    grid = BeatGrid.uniform(tempo=120, beats_per_measure=3, first_downbeat=0.75)
    tab = [
        TabEvent(time=0.25, duration=0.2, notes=[TabNote(string=0, fret=3)]),
        TabEvent(time=0.75, duration=0.2, notes=[TabNote(string=0, fret=5)]),
        TabEvent(time=2.25, duration=0.2, notes=[TabNote(string=0, fret=7)]),  # next downbeat
    ]
    out = tmp_path / "waltz.gp5"
    layout = export_gp5([tab], STANDARD, out, grid=grid)

    song = gp.parse(str(out))
    track = song.tracks[0]
    assert all(h.timeSignature.numerator == 3 for h in song.measureHeaders)
    for measure in track.measures:
        assert sum(beat.duration.time for beat in measure.voices[0].beats) == gp.Duration.quarterTime * 3

    def first_struck_fret(measure):
        for beat in measure.voices[0].beats:
            if beat.notes and beat.notes[0].type == gp.NoteType.normal:
                return beat.notes[0].value
        return None

    first_beat = lambda m: m.voices[0].beats[0]  # noqa: E731
    assert first_struck_fret(track.measures[0]) == 3  # pickup
    assert first_beat(track.measures[1]).notes[0].value == 5  # exactly on bar 2's downbeat
    assert first_beat(track.measures[2]).notes[0].value == 7
    assert layout.lead_in_beats == 3
    assert layout.measure_start_times()[:3] == pytest.approx([-0.75, 0.75, 2.25])


def test_bend_and_vibrato_are_written_as_gp_effects(tmp_path):
    tab = [
        TabEvent(time=0.0, duration=0.5, notes=[TabNote(string=3, fret=7, technique="bend", bend_semitones=2.0)]),
        TabEvent(time=0.5, duration=0.5, notes=[TabNote(string=3, fret=7, technique="bend", bend_semitones=1.0, bend_release=True)]),
        TabEvent(time=1.0, duration=1.0, notes=[TabNote(string=2, fret=5, technique="vibrato")]),
    ]
    out = tmp_path / "fx.gp5"
    export_gp5([tab], STANDARD, out, tempo=120)
    notes = [
        note
        for measure in gp.parse(str(out)).tracks[0].measures
        for beat in measure.voices[0].beats
        for note in beat.notes
    ]
    struck = [n for n in notes if n.type == gp.NoteType.normal]

    full, half_release, vib = struck
    assert full.effect.bend.type == gp.BendType.bend
    assert max(p.value for p in full.effect.bend.points) == 4  # quarter tones: full bend
    assert half_release.effect.bend.type == gp.BendType.bendRelease
    assert max(p.value for p in half_release.effect.bend.points) == 2
    assert half_release.effect.bend.points[-1].value == 0
    assert vib.effect.vibrato
    # Vibrato continues across the tied part of the note.
    tied_vibrato = [n for n in notes if n.type == gp.NoteType.tie and n.string == vib.string]
    assert tied_vibrato and all(n.effect.vibrato for n in tied_vibrato)
