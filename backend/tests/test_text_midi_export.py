"""ASCII tab and MIDI export."""

import struct

from app.jobs.edits import grid_from_tab
from app.models import schemas
from app.pipeline.midi_export import TICKS_PER_BEAT, export_midi
from app.pipeline.text_export import export_text, string_names

N = schemas.TabNote


def _ev(t, *notes, d=0.25):
    return schemas.TabEvent(time=t, duration=d, notes=list(notes))


def _tab(events, *, capo=0, tuning=("E2", "A2", "D3", "G3", "B3", "E4"), bars=4, bpm=4):
    return schemas.TabData(
        tuning=list(tuning),
        capo=capo,
        tempo_bpm=120,
        duration_seconds=bars * bpm * 0.5,
        beats_per_measure=bpm,
        measure_times=[i * bpm * 0.5 for i in range(bars)],
        beat_times=[i * 0.5 for i in range(bars * bpm)],
        tracks=[schemas.TabTrack(name="Guitar", events=events)],
    )


def _text(tab):
    return export_text(tab, title="Тест", grid=grid_from_tab(tab))


def _staff_lines(text):
    return [line for line in text.splitlines() if "|" in line]


def test_string_names_high_to_low_with_lowercase_high_e():
    assert [n.strip() for n in string_names(["E2", "A2", "D3", "G3", "B3", "E4"])] == ["e", "B", "G", "D", "A", "E"]
    # Open D: every repeated letter after the first is lowercase.
    assert [n.strip() for n in string_names(["D2", "A2", "D3", "Gb3", "A3", "D4"])] == ["d", "a", "Gb", "d", "A", "D"]


def test_text_tab_places_notes_in_the_right_bars_and_columns():
    events = [_ev(0.0, N(string=0, fret=3)), _ev(1.0, N(string=0, fret=5)), _ev(2.0, N(string=5, fret=12))]
    text = _text(_tab(events, bars=2))
    lines = _staff_lines(text)
    assert len(lines) == 6
    assert len({len(line) for line in lines}) == 1, "all string lines are equally long"
    low_e, high_e = lines[5], lines[0]
    bars_low = low_e.split("|")[1:-1]
    bars_high = high_e.split("|")[1:-1]
    assert "3" in bars_low[0] and "5" in bars_low[0]
    assert bars_low[0].index("3") < bars_low[0].index("5")
    # beat 1 of bar 2 → right after the bar line
    assert bars_high[1].startswith("-12")
    assert "Строй: E A D G B E" in text


def test_note_on_the_downbeat_lands_in_the_new_bar():
    text = _text(_tab([_ev(2.0, N(string=3, fret=7, technique="bend", bend_semitones=2.0))], bars=2))
    g_line = _staff_lines(text)[2]
    assert g_line.split("|")[2].startswith("-7b")


def test_techniques_and_palm_mute_line():
    events = [
        _ev(0.0, N(string=0, fret=0, technique="palm_mute")),
        _ev(0.5, N(string=0, fret=0, technique="palm_mute")),
        _ev(1.0, N(string=2, fret=5)),
        _ev(1.25, N(string=2, fret=7, technique="hammer_on")),
        _ev(1.5, N(string=2, fret=9, technique="slide")),
        _ev(1.75, N(string=1, fret=7, technique="vibrato")),
    ]
    text = _text(_tab(events, bars=1))
    assert "h7" in text and "/9" in text and "7~" in text
    lines = text.splitlines()
    pm_line = lines[lines.index(_staff_lines(text)[0]) - 1]  # directly above the top string
    zero_cols = [i for i, c in enumerate(_staff_lines(text)[5]) if c == "0"]
    assert len(zero_cols) == 2
    assert all(pm_line[c:c + 2] == "PM" for c in zero_cols), "PM sits right above each muted note"


def test_dense_bars_switch_to_a_finer_grid_instead_of_dropping_notes():
    events = [_ev(i * 0.0625, N(string=1, fret=i % 10)) for i in range(8)]  # 32nds
    text = _text(_tab(events, bars=1))
    b_line = _staff_lines(text)[4]
    assert sum(ch.isdigit() for ch in b_line) == 8


def test_long_pieces_wrap_at_80_columns():
    events = [_ev(i * 0.25, N(string=i % 6, fret=10 + i % 10)) for i in range(64)]
    text = _text(_tab(events, bars=8))
    assert all(len(line) <= 80 for line in text.splitlines() if "|" in line)
    assert sum(1 for line in text.splitlines() if line.startswith("e|")) > 1


# -- MIDI ----------------------------------------------------------------------

def _read_vlq(data, i):
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def _parse(data):
    assert data[:4] == b"MThd"
    fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    i = 14
    tracks = []
    for _ in range(ntracks):
        assert data[i:i + 4] == b"MTrk"
        length = struct.unpack(">I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + length]
        i += 8 + length
        events, j, tick = [], 0, 0
        while j < len(body):
            delta, j = _read_vlq(body, j)
            tick += delta
            status = body[j]
            if status == 0xFF:
                kind = body[j + 1]
                size, k = _read_vlq(body, j + 2)
                events.append((tick, "meta", kind, body[k:k + size]))
                j = k + size
            else:
                n = 2 if status & 0xF0 in (0xC0, 0xD0) else 3
                events.append((tick, status & 0xF0, body[j + 1:j + n]))
                j += n
        tracks.append(events)
    return fmt, division, tracks


def test_midi_structure_tempo_meter_and_notes_on_the_beat_grid():
    events = [_ev(0.0, N(string=0, fret=0), N(string=1, fret=2)), _ev(0.5, N(string=5, fret=3), d=0.5)]
    data = export_midi(_tab(events, capo=2, bpm=3), title="Тест", grid=grid_from_tab(_tab(events, capo=2, bpm=3)))
    fmt, division, tracks = _parse(data)
    assert fmt == 1 and division == TICKS_PER_BEAT and len(tracks) == 2
    meta = {e[2]: e[3] for e in tracks[0] if e[1] == "meta"}
    assert int.from_bytes(meta[0x51], "big") == 500_000  # 120 BPM
    assert meta[0x58][0] == 3  # 3/4
    assert meta[0x03].decode() == "Тест"

    ons = [(t, d[0]) for t, kind, *rest in tracks[1] if kind == 0x90 for d in rest]
    # capo 2: E2+2 = 42, A2(45)+2+2 = 49, E4(64)+2+3 = 69; second event one beat later
    assert sorted(ons) == [(0, 42), (0, 49), (TICKS_PER_BEAT, 69)]
    offs = [t for t, kind, *_ in tracks[1] if kind == 0x80]
    assert max(offs) == 2 * TICKS_PER_BEAT


def test_midi_bends_become_pitch_bend_messages():
    events = [_ev(0.0, N(string=3, fret=7, technique="bend", bend_semitones=2.0), d=1.0)]
    tab = _tab(events)
    _, _, tracks = _parse(export_midi(tab, title="b", grid=grid_from_tab(tab)))
    bends = [(t, d[0] | (d[1] << 7)) for t, kind, d in [(e[0], e[1], e[2]) for e in tracks[1] if e[1] == 0xE0]]
    assert bends[0][1] == 8192 and max(v for _, v in bends) == 16383 and bends[-1][1] == 8192
