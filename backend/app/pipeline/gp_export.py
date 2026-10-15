"""Renders a built tab into a Guitar Pro 5 (.gp5) file via PyGuitarPro.

The tricky part of this translation is rhythm: `build_tab` works in seconds,
but a GP file is a sequence of measures whose beats must sum to an exact tick
length. Rather than attempt full rhythm-notation transcription (decomposing
arbitrary durations into dotted/tied note values — a hard problem in its own
right, and one transcription timing isn't precise enough to reward), we lay
the whole piece out on a uniform thirty-second-note grid: every beat is the
same fixed duration, onsets snap to the nearest grid slot, and a note's
sustain across multiple slots is notated as tied continuation notes. That is
always a valid measure (8 thirty-second notes per quarter-note beat, times the
bar's beat count), so the file opens cleanly regardless of how the source
audio's timing wobbles.

*Where* the slots fall comes from a `rhythm.BeatGrid`: positions are measured
in beats from the first detected downbeat — interpolated between tracked
beats — so bar lines land on the music's bars rather than on multiples of an
idealized tempo counted from 0.0s. Anything before the first downbeat goes
into pickup bars. Without a grid (tests, fallbacks) a uniform 4/4 grid at
`tempo` starting at 0.0s reproduces the original behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Optional

import guitarpro as gp

from .fretboard import Fretboard
from .rhythm import DEFAULT_TEMPO, MAX_TEMPO, MIN_TEMPO, BeatGrid, snap
from .tab_builder import TabEvent, TabNote

_SLOTS_PER_BEAT = 8  # thirty-second notes per quarter-note beat: 8 * 120 ticks == one quarter
_SLOT_VALUE = gp.Duration.thirtySecond

_HAMMER_TECHNIQUES = frozenset({"hammer_on", "pull_off"})
_TREMOLO_NOTE_VALUE = gp.Duration.thirtySecond  # matches the "fast, even picking" the heuristic requires

# GP bend units (see PyGuitarPro's gp3.readBend/writeBend): the effect's
# header value is in 1/100 of a whole tone (100 = full bend = 2 semitones),
# while each BendPoint's value is in quarter tones and its position runs 0..12
# across the note's duration.
_BEND_HEADER_UNITS_PER_SEMITONE = 50
_BEND_POINT_UNITS_PER_SEMITONE = 2


def _slots_per_measure(grid: BeatGrid) -> int:
    return _SLOTS_PER_BEAT * grid.beats_per_measure


def _slot_seconds(tempo: int) -> float:
    """Real-world duration of one grid slot (a thirty-second note) at `tempo`."""
    return 60.0 / tempo / _SLOTS_PER_BEAT


def _gp_string_number(fretboard: Fretboard, string_index: int) -> int:
    """PyGuitarPro numbers strings thinnest-first (1 = highest); we model them low-to-high."""
    return fretboard.string_count - string_index


_SlotEntry = tuple[str, TabEvent]  # ("strike" | "tie", originating event)


@dataclass(frozen=True)
class Layout:
    """Where the piece sits on the grid — shared by every track in the song,
    and reported to the UI so it can draw the very same bar lines."""

    grid: BeatGrid
    lead_in_beats: int  # pickup beats before the first detected downbeat
    total_slots: int

    @property
    def measure_count(self) -> int:
        return self.total_slots // _slots_per_measure(self.grid)

    def slot(self, seconds: float) -> int:
        return snap(self.grid.beat_position(seconds) + self.lead_in_beats, _SLOTS_PER_BEAT)

    def measure_start_times(self) -> list[float]:
        return self.grid.measure_start_times(self.lead_in_beats, self.measure_count)


def plan_layout(tracks: list[list[TabEvent]], grid: BeatGrid) -> Layout:
    """Size the song: pickup bars for anything before the first downbeat, then
    enough whole bars to hold the longest track's last note."""
    events = [event for track in tracks for event in track]
    per_measure = _slots_per_measure(grid)
    if not events:
        return Layout(grid=grid, lead_in_beats=0, total_slots=per_measure)

    lead_in = grid.lead_in_beats(min(event.time for event in events))
    last_end = max(event.time + event.duration for event in events)
    needed = max(1, ceil((grid.beat_position(last_end) + lead_in) * _SLOTS_PER_BEAT))
    return Layout(grid=grid, lead_in_beats=lead_in, total_slots=ceil(needed / per_measure) * per_measure)


def _build_timeline(events: list[TabEvent], layout: Layout) -> list[_SlotEntry | None]:
    """Lay events onto the grid: one "strike" slot per onset, "tie" slots for its sustain.

    Onsets are forced to be strictly increasing in slot index — quantization
    can otherwise collapse two close-but-distinct events onto the same slot,
    which would silently drop one of them. A later event's strike always
    overwrites an earlier event's tie tail at the same slot, mirroring how a
    fretting hand actually behaves: playing a new note on a string mutes
    whatever was still ringing on it.
    """
    total_slots = layout.total_slots
    timeline: list[_SlotEntry | None] = [None] * total_slots
    prev_start = -1

    for event in events:
        start_slot = max(prev_start + 1, layout.slot(event.time), 0)
        if start_slot >= total_slots:
            break
        end_slot = min(max(start_slot + 1, layout.slot(event.time + event.duration)), total_slots)

        timeline[start_slot] = ("strike", event)
        for slot in range(start_slot + 1, end_slot):
            timeline[slot] = ("tie", event)
        prev_start = start_slot

    return timeline


def _total_slots(events: list[TabEvent], tempo: int) -> int:
    """Slots needed for `events` on a uniform 4/4 grid at `tempo` (whole bars)."""
    return plan_layout([events], BeatGrid.uniform(tempo)).total_slots


def _make_song(title: str, artist: str, layout: Layout) -> gp.Song:
    beats = layout.grid.beats_per_measure
    ticks_per_measure = gp.Duration.quarterTime * beats
    headers = [
        gp.MeasureHeader(
            number=i + 1,
            start=gp.Duration.quarterTime + i * ticks_per_measure,
            timeSignature=gp.TimeSignature(numerator=beats, denominator=gp.Duration(value=4)),
        )
        for i in range(layout.measure_count)
    ]
    tempo = min(max(int(layout.grid.tempo), MIN_TEMPO), MAX_TEMPO)
    return gp.Song(measureHeaders=headers, tracks=[], tempo=tempo, title=title, artist=artist)


# GP5 stores every string (title, artist, track names) in a single-byte
# "ANSI" code page, and Guitar Pro decodes it with the reader's system code
# page — there is no Unicode option in the format. Western files are cp1252
# (PyGuitarPro's default), Cyrillic ones are cp1251, which is exactly what
# Guitar Pro on a Russian-locale Windows reads. We pick the first code page
# that can represent all of the song's text, so a Latin title stays a plain
# cp1252 file for everyone while a Cyrillic title survives instead of crashing
# the export with UnicodeEncodeError.
_GP_ENCODINGS = ("cp1252", "cp1251")


def choose_encoding(*texts: str) -> str:
    """The first GP-compatible code page that can encode every text in `texts`,
    or the default (cp1252) if none can — callers then replace the leftovers
    via `_encodable` rather than failing the whole export over one character."""
    for encoding in _GP_ENCODINGS:
        try:
            for text in texts:
                text.encode(encoding)
        except UnicodeEncodeError:
            continue
        return encoding
    return _GP_ENCODINGS[0]


def _encodable(text: str, encoding: str) -> str:
    return text.encode(encoding, errors="replace").decode(encoding)


def track_name(index: int, total: int) -> str:
    """Plain "Guitar" for the (overwhelmingly common) single-part case — matching
    this project's pre-multi-track label exactly — and "Guitar 1"/"Guitar 2"/...
    once `separate_voices` auto-detects more than one simultaneous performance.

    Public because the JSON result (`jobs/manager.py`) must label tracks exactly
    the way the .gp5 file does — one definition keeps the two from drifting."""
    return "Guitar" if total == 1 else f"Guitar {index + 1}"


def _make_track(song: gp.Song, fretboard: Fretboard, *, number: int, name: str) -> gp.Track:
    """A guitar track with the instrument's *real* tuning and its capo set.

    Our fret numbers are relative to the capo (fret 0 = "open at the capo"),
    which is also how Guitar Pro numbers frets on a capo'd track: it plays
    `string tuning + capo + fret`. So the strings get the actual open-string
    pitches and the capo goes into the track's capo field (`Track.offset` in
    PyGuitarPro — written as "height of the capo" in the GP5 track header).

    This used to fold the capo into the tuning instead (E A D G B E with capo 2
    became F# B E A C# F#): it sounded right, but Guitar Pro then displayed a
    tuning nobody had asked for and no capo at all.
    """
    track = gp.Track(song, number=number, name=name)
    track.strings = sorted(
        (
            gp.GuitarString(_gp_string_number(fretboard, i), fretboard.tuning_midi[i])
            for i in range(fretboard.string_count)
        ),
        key=lambda s: s.number,
    )
    track.offset = fretboard.capo
    track.fretCount = fretboard.max_fret
    song.tracks.append(track)
    return track




def _bend_effect(tab_note: TabNote) -> gp.BendEffect:
    """A GP bend for a note pushed up to its bent pitch (optionally released)."""
    semitones = tab_note.bend_semitones or 0.0
    top = max(1, round(semitones * _BEND_POINT_UNITS_PER_SEMITONE))
    if tab_note.bend_release:
        bend_type = gp.BendType.bendRelease
        points = [gp.BendPoint(0, 0), gp.BendPoint(3, top), gp.BendPoint(6, top), gp.BendPoint(9, 0), gp.BendPoint(12, 0)]
    else:
        bend_type = gp.BendType.bend
        points = [gp.BendPoint(0, 0), gp.BendPoint(6, top), gp.BendPoint(12, top)]
    return gp.BendEffect(type=bend_type, value=round(semitones * _BEND_HEADER_UNITS_PER_SEMITONE), points=points)


def _apply_technique(note: gp.Note, tab_note: TabNote) -> None:
    technique = tab_note.technique
    if technique in _HAMMER_TECHNIQUES:
        note.effect.hammer = True
    elif technique == "slide":
        note.effect.slides = [gp.SlideType.legatoSlideTo]
    elif technique == "tremolo":
        note.effect.tremoloPicking = gp.TremoloPickingEffect(duration=gp.Duration(value=_TREMOLO_NOTE_VALUE))
    elif technique == "bend" and tab_note.bend_semitones:
        note.effect.bend = _bend_effect(tab_note)
    elif technique == "vibrato":
        note.effect.vibrato = True


def _populate_track(track: gp.Track, fretboard: Fretboard, events: list[TabEvent], layout: Layout) -> None:
    timeline = _build_timeline(events, layout)
    per_measure = _slots_per_measure(layout.grid)

    for measure_index, measure in enumerate(track.measures):
        voice = measure.voices[0]
        base = measure_index * per_measure
        for offset in range(per_measure):
            entry = timeline[base + offset]
            beat = gp.Beat(voice, duration=gp.Duration(value=_SLOT_VALUE))

            if entry is None:
                beat.status = gp.BeatStatus.rest
            else:
                kind, event = entry
                beat.status = gp.BeatStatus.normal
                note_type = gp.NoteType.tie if kind == "tie" else gp.NoteType.normal
                for tab_note in event.notes:
                    note = gp.Note(
                        beat,
                        value=tab_note.fret,
                        string=_gp_string_number(fretboard, tab_note.string),
                        type=note_type,
                        velocity=gp.Velocities.default,
                    )
                    if kind == "strike" and tab_note.technique:
                        _apply_technique(note, tab_note)
                    elif kind == "tie" and tab_note.technique == "vibrato":
                        # Vibrato lasts as long as the note rings, so GP needs
                        # it on every tied continuation, not just the attack.
                        note.effect.vibrato = True
                    beat.notes.append(note)

            voice.beats.append(beat)


def export_gp5(
    tracks: list[list[TabEvent]],
    fretboard: Fretboard,
    output_path,
    *,
    title: str = "",
    artist: str = "",
    tempo: int = DEFAULT_TEMPO,
    grid: Optional[BeatGrid] = None,
) -> Layout:
    """Write `tracks` (as produced by `tab_builder.build_multi_track_tab` — one
    event list per auto-detected guitar part) to a single .gp5 file at `output_path`.

    Every track shares one set of measure headers, so they must all run the same
    length: we size the song to the longest part and let the others trail off in
    silence (rests), the way a real multi-guitar arrangement's score is laid out.

    `grid` (from `rhythm.analyze_rhythm`) decides meter, tempo and bar lines;
    without one, a uniform 4/4 grid at `tempo` is used. Returns the layout so
    the caller can report the same bar lines to the UI.
    """
    if grid is None:
        grid = BeatGrid.uniform(min(max(int(tempo), MIN_TEMPO), MAX_TEMPO))
    layout = plan_layout(tracks, grid)
    encoding = choose_encoding(title, artist)
    title, artist = _encodable(title, encoding), _encodable(artist, encoding)

    song = _make_song(title=title, artist=artist, layout=layout)
    for index, events in enumerate(tracks):
        track = _make_track(song, fretboard, number=index + 1, name=track_name(index, len(tracks)))
        _populate_track(track, fretboard, events, layout)

    gp.write(song, str(output_path), version=(5, 1, 0), encoding=encoding)
    return layout
