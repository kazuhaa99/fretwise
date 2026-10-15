"""Manual corrections to a finished tab: validation, storage, .gp5 rebuild.

Transcription is never perfect, so the result screen lets the user fix notes
(fret, string, delete, add). The server is the source of truth for those
edits, for two reasons: the downloadable .gp5 must contain them, and they must
survive a reload (and, with job persistence, a restart).

Storage is two JSON files next to the job's other artifacts:
- `tab.original.json` — the tab exactly as the pipeline produced it, written
  once when the job finishes; "revert all edits" goes back to it;
- `tab.json` — the current edited tab, present only once something changed.

Every save carries the revision the client started from. A save based on a
stale revision (the same job edited in another tab) is refused rather than
silently overwriting the other edits.

The .gp5 is rebuilt from the stored `TabData` alone: its `beatTimes` *are* the
export grid (pickup bar included), so `grid_from_tab` reproduces the very same
bar lines the original export used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..models import schemas
from ..pipeline import gp_export
from ..pipeline.fretboard import Fretboard
from ..pipeline.rhythm import BeatGrid
from ..pipeline.tab_builder import TabEvent, TabNote

ORIGINAL_TAB_FILENAME = "tab.original.json"
EDITED_TAB_FILENAME = "tab.json"

MAX_FRET = 22
_MIN_DURATION = 0.01


class EditError(ValueError):
    """An edit that can't be applied; the message is shown to the user."""


class EditConflictError(Exception):
    """The edit was based on an outdated revision."""


def _clock(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes}:{seconds - minutes * 60:04.1f}".replace(".", ",")


def validate_tracks(tracks: list[schemas.TabTrack], base: schemas.TabData) -> None:
    """Reject anything a guitar (or the .gp5 format) can't represent.

    Deliberately *not* enforced: the hand-span rule from `tab_builder`. That
    rule shapes the automatic fingering; a person correcting the tab by ear
    knows their own hand, and may well want a wide stretch.
    """
    if len(tracks) != len(base.tracks):
        raise EditError(f"Ожидалось партий: {len(base.tracks)}, получено: {len(tracks)}")

    strings = len(base.tuning)
    frets = Fretboard.from_note_names(base.tuning, capo=base.capo, max_fret=MAX_FRET).frets_above_capo
    for track_index, track in enumerate(tracks, start=1):
        previous = -1.0
        for event in track.events:
            where = f"Партия {track_index}, {_clock(max(event.time, 0.0))}"
            if event.time < previous:
                raise EditError(f"{where}: ноты должны идти по времени")
            if event.time < 0 or event.time > base.duration_seconds + 1:
                raise EditError(f"{where}: время вне записи")
            if event.duration <= 0:
                raise EditError(f"{where}: длительность должна быть больше нуля")
            if not event.notes:
                raise EditError(f"{where}: пустой аккорд — удалите его целиком")
            used: set[int] = set()
            for note in event.notes:
                if not 0 <= note.string < strings:
                    raise EditError(f"{where}: струны {note.string + 1} нет — на гитаре {strings} струн")
                if not 0 <= note.fret <= frets:
                    raise EditError(f"{where}: лад {note.fret} вне грифа (доступно 0–{frets})")
                if note.string in used:
                    raise EditError(f"{where}: две ноты на одной струне в одном аккорде")
                used.add(note.string)
            previous = event.time


def apply_tracks(base: schemas.TabData, tracks: list[schemas.TabTrack]) -> schemas.TabData:
    """`base` with its tracks replaced; names and everything else stay the server's."""
    clean = [
        schemas.TabTrack(
            name=original.name,
            events=[
                schemas.TabEvent(
                    time=round(event.time, 4),
                    duration=max(round(event.duration, 4), _MIN_DURATION),
                    notes=[
                        schemas.TabNote(
                            string=n.string,
                            fret=n.fret,
                            technique=n.technique,
                            bend_semitones=n.bend_semitones if n.technique == "bend" else None,
                        )
                        for n in sorted(event.notes, key=lambda n: n.string)
                    ],
                )
                for event in edited.events
            ],
        )
        for original, edited in zip(base.tracks, tracks)
    ]
    return base.model_copy(update={"tracks": clean})


def grid_from_tab(tab: schemas.TabData) -> BeatGrid:
    if len(tab.beat_times) >= 2:
        return BeatGrid(
            tempo=tab.tempo_bpm,
            beats_per_measure=tab.beats_per_measure,
            beat_times=tuple(tab.beat_times),
            downbeat_index=0,
        )
    return BeatGrid.uniform(tab.tempo_bpm, tab.beats_per_measure)


def to_events(track: schemas.TabTrack) -> list[TabEvent]:
    return [
        TabEvent(
            time=e.time,
            duration=e.duration,
            notes=[
                TabNote(
                    string=n.string,
                    fret=n.fret,
                    technique=n.technique,
                    bend_semitones=n.bend_semitones,
                    bend_release=False,
                )
                for n in e.notes
            ],
        )
        for e in track.events
    ]


def write_gp5(tab: schemas.TabData, path: Path, *, title: str) -> None:
    """Export `tab` to `path` atomically (a half-written file is never served)."""
    fretboard = Fretboard.from_note_names(tab.tuning, capo=tab.capo, max_fret=MAX_FRET)
    tmp = path.with_suffix(".tmp.gp5")
    gp_export.export_gp5(
        [to_events(track) for track in tab.tracks],
        fretboard,
        tmp,
        title=title,
        artist="Fretwise",
        grid=grid_from_tab(tab),
    )
    os.replace(tmp, path)


def write_json(tab: schemas.TabData, path: Path) -> None:
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(tab.model_dump_json(by_alias=True), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> schemas.TabData:
    return schemas.TabData.model_validate(json.loads(path.read_text(encoding="utf-8")))
