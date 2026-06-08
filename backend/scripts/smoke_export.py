"""Manual smoke test: transcription -> tab -> .gp5, round-tripped through PyGuitarPro.

Run with: .venv/Scripts/python.exe scripts/smoke_export.py
"""
from __future__ import annotations

from pathlib import Path

import guitarpro as gp

from app.pipeline import gp_export
from app.pipeline.fretboard import Fretboard
from app.pipeline.tab_builder import build_multi_track_tab
from app.pipeline.transcription import transcribe_notes

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_guitar.wav"
OUT = ROOT / "tests" / "fixtures" / "synthetic_guitar.gp5"


def main() -> None:
    notes = transcribe_notes(FIXTURE)
    fretboard = Fretboard.from_note_names(["E2", "A2", "D3", "G3", "B3", "E4"], max_fret=22)
    tracks = build_multi_track_tab(notes, fretboard, max_fret_span=4, position_jump_weight=1.0)
    print(f"{len(tracks)} track(s)")
    for track_index, tab in enumerate(tracks):
        print(f"track {track_index + 1}: {len(tab)} tab events")
        for event in tab:
            notes_str = ", ".join(f"s{n.string}f{n.fret}" + (f"[{n.technique}]" if n.technique else "") for n in event.notes)
            print(f"  t={event.time:5.2f} dur={event.duration:5.2f}  {notes_str}")

    gp_export.export_gp5(tracks, fretboard, OUT, title="Synthetic Smoke Test", artist="Fretwise", tempo=110)
    print(f"\nwrote {OUT} ({OUT.stat().st_size} bytes)")

    song = gp.parse(str(OUT))
    print(f"reparsed: title={song.title!r} tempo={song.tempo} tracks={[t.name for t in song.tracks]}")

    for track in song.tracks:
        print(f"  {track.name}: strings={[(s.number, s.value) for s in track.strings]} measures={len(track.measures)}")
        struck = 0
        tied = 0
        for measure in track.measures:
            for beat in measure.voices[0].beats:
                for note in beat.notes:
                    if note.type == gp.NoteType.normal:
                        struck += 1
                    elif note.type == gp.NoteType.tie:
                        tied += 1
        print(f"    struck notes={struck} tied continuations={tied}")


if __name__ == "__main__":
    main()
