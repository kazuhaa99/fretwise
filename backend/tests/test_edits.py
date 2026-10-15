"""Unit tests for manual tab edits (validation, grid reconstruction, export)."""

import guitarpro as gp
import pytest

from app.jobs import edits
from app.models import schemas
from app.pipeline.gp_export import plan_layout
from app.pipeline.rhythm import BeatGrid
from app.pipeline.tab_builder import TabEvent, TabNote


def _tab(events, *, capo=0, beat_times=None, bpm=4):
    return schemas.TabData(
        tuning=["E2", "A2", "D3", "G3", "B3", "E4"],
        capo=capo,
        tempo_bpm=120,
        duration_seconds=8.0,
        beats_per_measure=bpm,
        measure_times=[0.0, 2.0, 4.0, 6.0],
        beat_times=beat_times if beat_times is not None else [i * 0.5 for i in range(16)],
        tracks=[schemas.TabTrack(name="Guitar", events=events)],
    )


def _event(time, *notes, duration=0.4):
    return schemas.TabEvent(time=time, duration=duration, notes=[schemas.TabNote(string=s, fret=f) for s, f in notes])


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([_event(1.0, (6, 3))], "струны 7 нет"),
        ([_event(1.0, (0, 23))], "лад 23 вне грифа"),
        ([_event(1.0, (0, 3), (0, 5))], "две ноты на одной струне"),
        ([_event(2.0, (0, 3)), _event(1.0, (1, 3))], "по времени"),
        ([schemas.TabEvent(time=1.0, duration=0.4, notes=[])], "пустой аккорд"),
        ([_event(-1.0, (0, 3))], "время вне записи"),
    ],
)
def test_validation_rejects_what_a_guitar_cannot_play(events, message):
    base = _tab([_event(1.0, (0, 3))])
    with pytest.raises(edits.EditError, match=message):
        edits.validate_tracks([schemas.TabTrack(name="x", events=events)], base)


def test_capo_limits_frets_and_wide_stretches_are_allowed():
    base = _tab([], capo=5)
    with pytest.raises(edits.EditError, match="0–17"):
        edits.validate_tracks([schemas.TabTrack(name="x", events=[_event(1.0, (0, 18))])], base)
    # A stretch the automatic fingering would never produce is the user's call.
    edits.validate_tracks([schemas.TabTrack(name="x", events=[_event(1.0, (0, 1), (1, 12))])], base)


def test_track_count_must_match():
    with pytest.raises(edits.EditError, match="партий"):
        edits.validate_tracks([], _tab([]))


def test_apply_keeps_server_names_and_drops_stray_bend_amounts():
    base = _tab([_event(1.0, (0, 3))])
    edited = schemas.TabTrack(
        name="hacked",
        events=[schemas.TabEvent(time=1.0, duration=0.4, notes=[schemas.TabNote(string=2, fret=5, bend_semitones=2.0), schemas.TabNote(string=0, fret=3)])],
    )
    tab = edits.apply_tracks(base, [edited])
    assert tab.tracks[0].name == "Guitar"
    assert [n.string for n in tab.tracks[0].events[0].notes] == [0, 2]  # sorted by string
    assert all(n.bend_semitones is None for n in tab.tracks[0].events[0].notes)


def test_grid_from_tab_reproduces_the_exported_bar_lines():
    # Grid with a pickup: downbeat at 0.75 s, 3/4.
    original = BeatGrid.uniform(tempo=120, beats_per_measure=3, first_downbeat=0.75)
    events = [TabEvent(time=0.25, duration=0.2, notes=[TabNote(0, 3)]), TabEvent(time=2.3, duration=0.2, notes=[TabNote(0, 5)])]
    layout = plan_layout([events], original)
    tab = _tab([], beat_times=[round(t, 4) for t in layout.beat_times()], bpm=3)
    rebuilt = plan_layout([events], edits.grid_from_tab(tab))
    assert rebuilt.measure_start_times() == pytest.approx(layout.measure_start_times())
    assert rebuilt.total_slots == layout.total_slots


def test_write_gp5_contains_the_edit(tmp_path):
    tab = _tab([_event(0.0, (0, 3)), _event(1.0, (1, 7))])
    out = tmp_path / "tab.gp5"
    edits.write_gp5(tab, out, title="Правка")
    song = gp.parse(str(out), encoding="cp1251")
    frets = [n.value for m in song.tracks[0].measures for b in m.voices[0].beats for n in b.notes if n.type == gp.NoteType.normal]
    assert frets == [3, 7]
    assert song.title == "Правка"
    assert not list(tmp_path.glob("*.tmp.*"))


def test_json_round_trip(tmp_path):
    tab = _tab([_event(1.0, (0, 3))])
    edits.write_json(tab, tmp_path / "t.json")
    assert edits.read_json(tmp_path / "t.json") == tab
