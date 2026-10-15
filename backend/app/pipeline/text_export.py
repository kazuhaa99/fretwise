"""Plain-text (ASCII) tab export — the format guitarists paste into forums.

    1                                 2
    PM PM PM
  e|--------------------------------|-------7b----------9~-------|
  B|--------------------------------|-----------8-----10---------|
  G|-----------------------------7--|----------------------------|
  ...

Layout follows the same bars as the .gp5 and the in-app sheet: notes are
placed by beat position on a sixteenth-note grid inside each bar (finer for
bars whose notes would otherwise collide), and bars wrap into systems no wider
than `LINE_WIDTH` characters, as printed tab does. Techniques use the common
text conventions: `h`/`p` before the target note, `/` for a slide, `b` after a
bent note, `~` after a vibrato note, and a `PM` line above palm-muted notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from ..models import schemas
from .rhythm import BeatGrid

LINE_WIDTH = 80
_RESOLUTIONS = (4, 8, 16)  # slots per beat, tried in order until nothing collides

_PREFIX = {"hammer_on": "h", "pull_off": "p", "slide": "/"}
_SUFFIX = {"bend": "b", "vibrato": "~"}


def string_names(tuning: list[str]) -> list[str]:
    """Names for each string, high to low as printed: the higher of two
    same-letter strings is lowercase (the classic e/E)."""
    letters = []
    for name in tuning:
        letter = name[0].upper() + ("b" if len(name) > 2 and name[1] == "b" else "#" if "#" in name else "")
        letters.append(letter)
    names = list(letters)
    for i, letter in enumerate(letters):
        if letter in letters[:i]:
            names[i] = letter.lower()
    width = max(len(n) for n in names)
    return [n.ljust(width) for n in reversed(names)]  # top line = highest string


def _note_label(note: schemas.TabNote) -> str:
    return f"{_PREFIX.get(note.technique or '', '')}{note.fret}{_SUFFIX.get(note.technique or '', '')}"


@dataclass
class _Bar:
    number: int
    lines: list[str]  # one per string, high to low
    pm: str = ""
    width: int = 0


def _bar_events(tab: schemas.TabData, track: schemas.TabTrack, grid: BeatGrid) -> list[list[tuple[float, schemas.TabEvent]]]:
    """(beat position within the bar, event) grouped per bar of the sheet."""
    bpm = tab.beats_per_measure
    bar_count = max(1, len(tab.measure_times))
    bars: list[list[tuple[float, schemas.TabEvent]]] = [[] for _ in range(bar_count)]
    for event in track.events:
        # A note a hair before a bar line (attack jitter) belongs to the next
        # bar's downbeat — same rule as the in-app sheet.
        index = min(max(int(grid.beat_position(event.time + 0.03) // bpm), 0), bar_count - 1)
        position = grid.beat_position(event.time) - index * bpm
        bars[index].append((min(max(position, 0.0), bpm - 1e-6), event))
    return bars


def _render_bar(number: int, events: list[tuple[float, schemas.TabEvent]], strings: int, bpm: int) -> _Bar:
    for resolution in _RESOLUTIONS:
        slots: dict[int, schemas.TabEvent] = {}
        collided = False
        for pos, event in events:
            slot = floor(pos * resolution + 0.5)
            slot = min(slot, bpm * resolution - 1)
            if slot in slots:
                collided = True
                break
            slots[slot] = event
        if not collided:
            break

    total = bpm * resolution
    cells: list[list[str]] = [["-"] * total for _ in range(strings)]
    pm_cells = [" "] * total
    widths = [1] * total
    for slot, event in slots.items():
        for note in event.notes:
            row = strings - 1 - note.string
            label = _note_label(note)
            cells[row][slot] = label
            widths[slot] = max(widths[slot], len(label))
            if note.technique == "palm_mute":
                pm_cells[slot] = "x"

    lines = []
    for row in range(strings):
        parts = []
        for slot in range(total):
            label = cells[row][slot]
            parts.append(label + "-" * (widths[slot] - len(label)) + "-")
        lines.append("-" + "".join(parts))
    pm = " " + "".join(("PM" if mark == "x" else "  ")[: widths[slot] + 1].ljust(widths[slot] + 1) for slot, mark in enumerate(pm_cells))
    return _Bar(number=number, lines=lines, pm=pm if "PM" in pm else "", width=len(lines[0]))


def export_text(tab: schemas.TabData, *, title: str, grid: BeatGrid) -> str:
    strings = len(tab.tuning)
    names = string_names(tab.tuning)
    gutter = len(names[0]) + 1
    tuning_letters = " ".join(n.strip().capitalize() for n in reversed(names))
    header = [
        title or "Fretwise",
        f"Строй: {tuning_letters}" + (f" · каподастр {tab.capo}" if tab.capo else "") + f" · {tab.tempo_bpm} BPM · {tab.beats_per_measure}/4",
        "Сделано в Fretwise. h — хаммер-он, p — пул-офф, / — слайд, b — бенд, ~ — вибрато, PM — приглушение.",
    ]
    out = header[:]
    for track in tab.tracks:
        out += ["", f"[{track.name}]"]
        bars = [
            _render_bar(i + 1, events, strings, tab.beats_per_measure)
            for i, events in enumerate(_bar_events(tab, track, grid))
        ]
        system: list[_Bar] = []

        def flush() -> None:
            if not system:
                return
            numbers = " " * gutter
            pm = " " * gutter
            for bar in system:
                numbers += str(bar.number).ljust(bar.width + 1)
                pm += bar.pm.ljust(bar.width) + " "
            out.append("")
            out.append(numbers.rstrip())
            if pm.strip():
                out.append(pm.rstrip())
            for row in range(strings):
                out.append(names[row] + "|" + "|".join(bar.lines[row] for bar in system) + "|")

        used = gutter + 1
        for bar in bars:
            if system and used + bar.width + 1 > LINE_WIDTH:
                flush()
                system = []
                used = gutter + 1
            system.append(bar)
            used += bar.width + 1
        flush()
    return "\n".join(out) + "\n"
