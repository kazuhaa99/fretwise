"""Renders a built tab into a Guitar Pro 5 (.gp5) file via PyGuitarPro.

The tricky part of this translation is rhythm: `build_tab` works in seconds,
but a GP file is a sequence of measures whose beats must sum to an exact tick
length. Rather than attempt full rhythm-notation transcription (decomposing
arbitrary durations into dotted/tied note values — a hard problem in its own
right, and one transcription timing isn't precise enough to reward), we lay
the whole piece out on a uniform thirty-second-note grid: every beat is the
same fixed duration, onsets snap to the nearest grid slot, and a note's
sustain across multiple slots is notated as tied continuation notes. That is
always a valid measure (32 thirty-second notes == one 4/4 bar, exactly), so
the file opens cleanly regardless of how the source audio's timing wobbles.
"""

from __future__ import annotations

from math import ceil

import guitarpro as gp

from .fretboard import Fretboard
from .tab_builder import TabEvent

DEFAULT_TEMPO = 120
_MIN_TEMPO = 55
_MAX_TEMPO = 200

_TICKS_PER_MEASURE = gp.Duration.quarterTime * 4  # 3840 — one 4/4 bar
_SLOTS_PER_MEASURE = 32  # thirty-second-note grid: 32 * 120 == 3840, divides evenly
_SLOT_VALUE = gp.Duration.thirtySecond

_HAMMER_TECHNIQUES = frozenset({"hammer_on", "pull_off"})
_TREMOLO_NOTE_VALUE = gp.Duration.thirtySecond  # matches the "fast, even picking" the heuristic requires


def estimate_tempo(samples, sample_rate: int) -> int:
    """Best-effort BPM estimate from the isolated guitar stem, for display/playback.

    This number only affects how fast Guitar Pro plays the file back and what
    it prints as the tempo marking — export always quantizes onsets to a fixed
    *absolute-time* grid (see `_slot_seconds`), so a rough estimate can't throw
    off note placement. Beat tracking can fail to lock onto anything for sparse
    or rubato playing; in that case we fall back to a plain default rather than
    propagating a nonsensical value into the file.
    """
    import librosa

    try:
        tempo, _beats = librosa.beat.beat_track(y=samples, sr=sample_rate)
        bpm = int(round(float(tempo)))
    except Exception:
        return DEFAULT_TEMPO
    if bpm <= 0:
        return DEFAULT_TEMPO
    return min(max(bpm, _MIN_TEMPO), _MAX_TEMPO)


def _slot_seconds(tempo: int) -> float:
    """Real-world duration of one export grid slot (a thirty-second note) at `tempo`."""
    return 60.0 / tempo / 8.0


def _gp_string_number(fretboard: Fretboard, string_index: int) -> int:
    """PyGuitarPro numbers strings thinnest-first (1 = highest); we model them low-to-high."""
    return fretboard.string_count - string_index


_SlotEntry = tuple[str, TabEvent]  # ("strike" | "tie", originating event)


def _build_timeline(events: list[TabEvent], tempo: int, total_slots: int) -> list[_SlotEntry | None]:
    """Lay events onto the grid: one "strike" slot per onset, "tie" slots for its sustain.

    Onsets are forced to be strictly increasing in slot index — quantization
    can otherwise collapse two close-but-distinct events onto the same slot,
    which would silently drop one of them. A later event's strike always
    overwrites an earlier event's tie tail at the same slot, mirroring how a
    fretting hand actually behaves: playing a new note on a string mutes
    whatever was still ringing on it.
    """
    slot_seconds = _slot_seconds(tempo)
    timeline: list[_SlotEntry | None] = [None] * total_slots
    prev_start = -1

    for event in events:
        start_slot = max(prev_start + 1, round(event.time / slot_seconds))
        if start_slot >= total_slots:
            break
        span = max(1, round(event.duration / slot_seconds))
        end_slot = min(start_slot + span, total_slots)

        timeline[start_slot] = ("strike", event)
        for slot in range(start_slot + 1, end_slot):
            timeline[slot] = ("tie", event)
        prev_start = start_slot

    return timeline


def _total_slots(events: list[TabEvent], tempo: int) -> int:
    if not events:
        return _SLOTS_PER_MEASURE
    slot_seconds = _slot_seconds(tempo)
    last_end = max(event.time + event.duration for event in events)
    needed = max(1, ceil(last_end / slot_seconds))
    measures = ceil(needed / _SLOTS_PER_MEASURE)
    return measures * _SLOTS_PER_MEASURE


def _make_song(title: str, artist: str, tempo: int, measure_count: int) -> gp.Song:
    headers = [
        gp.MeasureHeader(
            number=i + 1,
            start=gp.Duration.quarterTime + i * _TICKS_PER_MEASURE,
            timeSignature=gp.TimeSignature(numerator=4, denominator=gp.Duration(value=4)),
        )
        for i in range(measure_count)
    ]
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
    """A guitar track tuned to `fretboard` (capo already folded into the pitches:
    fret 0 in our model means "open at the capo", so writing the capo'd open pitches as
    the string tuning makes our fret numbers land on the right notes without GP needing
    to know about a capo at all)."""
    track = gp.Track(song, number=number, name=name)
    track.strings = sorted(
        (gp.GuitarString(_gp_string_number(fretboard, i), fretboard.open_pitch(i)) for i in range(fretboard.string_count)),
        key=lambda s: s.number,
    )
    song.tracks.append(track)
    return track


def _populate_track(track: gp.Track, fretboard: Fretboard, events: list[TabEvent], tempo: int, total_slots: int) -> None:
    timeline = _build_timeline(events, tempo, total_slots)

    for measure_index, measure in enumerate(track.measures):
        voice = measure.voices[0]
        base = measure_index * _SLOTS_PER_MEASURE
        for offset in range(_SLOTS_PER_MEASURE):
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
                        if tab_note.technique in _HAMMER_TECHNIQUES:
                            note.effect.hammer = True
                        elif tab_note.technique == "slide":
                            note.effect.slides = [gp.SlideType.legatoSlideTo]
                        elif tab_note.technique == "tremolo":
                            note.effect.tremoloPicking = gp.TremoloPickingEffect(
                                duration=gp.Duration(value=_TREMOLO_NOTE_VALUE)
                            )
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
) -> None:
    """Write `tracks` (as produced by `tab_builder.build_multi_track_tab` — one
    event list per auto-detected guitar part) to a single .gp5 file at `output_path`.

    Every track shares one set of measure headers, so they must all run the same
    length: we size the song to the longest part and let the others trail off in
    silence (rests), the way a real multi-guitar arrangement's score is laid out.
    """
    tempo = min(max(int(tempo), _MIN_TEMPO), _MAX_TEMPO)
    encoding = choose_encoding(title, artist)
    title, artist = _encodable(title, encoding), _encodable(artist, encoding)
    total_slots = max((_total_slots(events, tempo) for events in tracks), default=_SLOTS_PER_MEASURE)
    measure_count = total_slots // _SLOTS_PER_MEASURE

    song = _make_song(title=title, artist=artist, tempo=tempo, measure_count=measure_count)
    for index, events in enumerate(tracks):
        track = _make_track(song, fretboard, number=index + 1, name=track_name(index, len(tracks)))
        _populate_track(track, fretboard, events, tempo, total_slots)

    gp.write(song, str(output_path), version=(5, 1, 0), encoding=encoding)
