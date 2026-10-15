"""Models a fretted instrument: tuning, capo, and pitch <-> (string, fret) mapping.

Strings are indexed low to high (0 = lowest-pitched string, e.g. low E on a
standard-tuned guitar). Frets are counted relative to the capo — "fret 0" is
an open string played at the capo position, matching how tab notation and
Guitar Pro both represent a capo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_NOTE_TO_SEMITONE = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

_NOTE_NAME_RE = re.compile(r"^([A-Ga-g])(#|b)?(-?\d+)$")


def note_name_to_midi(name: str) -> int:
    """Parse a note name like 'E2', 'Eb4', 'F#3' into a MIDI note number (C4 = 60)."""
    m = _NOTE_NAME_RE.match(name.strip())
    if not m:
        raise ValueError(f"Invalid note name: {name!r}")
    letter = m.group(1).upper()
    accidental = m.group(2) or ""
    octave = int(m.group(3))
    semitone = _NOTE_TO_SEMITONE[letter + accidental]
    return (octave + 1) * 12 + semitone


def midi_to_note_name(midi: int) -> str:
    octave = midi // 12 - 1
    name = NOTE_NAMES[midi % 12]
    return f"{name}{octave}"


@dataclass(frozen=True)
class Fretboard:
    tuning_midi: tuple[int, ...]  # open-string pitches, low string to high string
    capo: int = 0
    max_fret: int = 22

    @staticmethod
    def from_note_names(tuning: list[str], capo: int = 0, max_fret: int = 22) -> "Fretboard":
        return Fretboard(tuple(note_name_to_midi(n) for n in tuning), capo=capo, max_fret=max_fret)

    @property
    def string_count(self) -> int:
        return len(self.tuning_midi)

    def open_pitch(self, string_index: int) -> int:
        """Sounding pitch of `string_index` at fret 0 (i.e. at the capo)."""
        return self.tuning_midi[string_index] + self.capo

    @property
    def frets_above_capo(self) -> int:
        """Playable frets above the capo: `max_fret` counts from the nut, so a
        capo at the 5th fret of a 22-fret neck leaves 17 frets, not 22."""
        return max(0, self.max_fret - self.capo)

    def candidates(self, midi_pitch: int) -> list[tuple[int, int]]:
        """Every (string_index, fret) pair that produces `midi_pitch`, fret in
        [0, frets_above_capo] (frets relative to the capo)."""
        out: list[tuple[int, int]] = []
        for string_index in range(self.string_count):
            fret = midi_pitch - self.open_pitch(string_index)
            if 0 <= fret <= self.frets_above_capo:
                out.append((string_index, fret))
        return out
