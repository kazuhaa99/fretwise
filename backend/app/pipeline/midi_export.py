"""Standard MIDI File export (type 1) of a tab.

The file is laid out on the musical grid, not on raw seconds: one constant
tempo (the tab's BPM), the detected time signature, and every note at its beat
position from the same beat grid as the .gp5 — so bars line up when the file
is opened in a DAW or notation program. (Raw-seconds timing would match the
recording but put every bar line in the wrong place at any tempo drift.)

Pitches are what sounds: open string + capo + fret. Bends become pitch-bend
messages (the General MIDI default range of ±2 semitones, so bends beyond a
whole step are clamped); everything else is articulation MIDI has no standard
event for, and is left out.

No dependency: the format is small enough to write directly (variable-length
quantities, a header chunk, one tempo track, one track per guitar part).
"""

from __future__ import annotations

import struct

from ..models import schemas
from .fretboard import note_name_to_midi
from .rhythm import BeatGrid

TICKS_PER_BEAT = 480
GUITAR_PROGRAM = 25  # General MIDI "Acoustic Guitar (steel)" (0-based 25)
VELOCITY = 90
_PITCH_BEND_RANGE = 2.0  # semitones, GM default
_MIN_TICKS = 30


def _vlq(value: int) -> bytes:
    """MIDI variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack(">I", len(data)) + data


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """events: (tick, order, message) — order breaks ties (note-off before note-on)."""
    data = bytearray()
    now = 0
    for tick, _order, message in sorted(events, key=lambda e: (e[0], e[1])):
        data += _vlq(tick - now) + message
        now = tick
    data += _vlq(0) + b"\xff\x2f\x00"  # end of track
    return _chunk(b"MTrk", bytes(data))


def _text_meta(kind: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return bytes([0xFF, kind]) + _vlq(len(raw)) + raw


def _pitch_bend(channel: int, semitones: float) -> bytes:
    value = int(round(8192 + max(-1.0, min(1.0, semitones / _PITCH_BEND_RANGE)) * 8191))
    return bytes([0xE0 | channel, value & 0x7F, (value >> 7) & 0x7F])


def export_midi(tab: schemas.TabData, *, title: str, grid: BeatGrid) -> bytes:
    open_pitches = [note_name_to_midi(n) + tab.capo for n in tab.tuning]

    def ticks(seconds: float) -> int:
        return max(0, int(round(grid.beat_position(seconds) * TICKS_PER_BEAT)))

    tempo_us = int(round(60_000_000 / max(tab.tempo_bpm, 1)))
    conductor = [
        (0, 0, _text_meta(0x03, title or "Fretwise")),
        (0, 1, b"\xff\x51\x03" + tempo_us.to_bytes(3, "big")),
        (0, 2, bytes([0xFF, 0x58, 0x04, tab.beats_per_measure, 2, 24, 8])),  # n/4
    ]
    chunks = [_track(conductor)]

    for index, track in enumerate(tab.tracks):
        channel = index % 16
        if channel == 9:  # never the GM drum channel
            channel = 10
        messages: list[tuple[int, int, bytes]] = [
            (0, 0, _text_meta(0x03, track.name)),
            (0, 1, bytes([0xC0 | channel, GUITAR_PROGRAM])),
        ]
        for event in track.events:
            start = ticks(event.time)
            end = max(start + _MIN_TICKS, ticks(event.time + event.duration))
            for note in event.notes:
                pitch = max(0, min(127, open_pitches[note.string] + note.fret))
                messages.append((start, 3, bytes([0x90 | channel, pitch, VELOCITY])))
                messages.append((end, 0, bytes([0x80 | channel, pitch, 0])))
                if note.technique == "bend" and note.bend_semitones:
                    rise = start + (end - start) // 3
                    messages.append((start, 2, _pitch_bend(channel, 0.0)))
                    messages.append((rise, 2, _pitch_bend(channel, note.bend_semitones)))
                    messages.append((end, 1, _pitch_bend(channel, 0.0)))
        chunks.append(_track(messages))

    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), TICKS_PER_BEAT))
    return header + b"".join(chunks)
