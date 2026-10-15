"""Transcribes an isolated guitar stem into discrete notes using Basic Pitch.

Basic Pitch (Spotify) is a polyphonic pitch-detection model — it estimates note
onset, offset/duration and pitch directly from the audio spectrum, including
simultaneous notes (chords), which is exactly what a guitar part needs. We run
its bundled ONNX model variant for fast CPU inference without pulling in a full
TensorFlow runtime at request time.
"""

from __future__ import annotations

from pathlib import Path

from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
from basic_pitch.constants import ANNOTATIONS_FPS
from basic_pitch.inference import predict

from .expression import CONTOUR_BINS_PER_SEMITONE, annotate_expressions
from .tab_builder import Note

_ONNX_MODEL_PATH = build_icassp_2022_model_path(FilenameSuffix.onnx)

# Guitar's full range (low E2 to roughly the 22nd fret of the high E string,
# generously padded) — keeps the model from chasing sub-bass rumble or
# ultrasonic artifacts that the source separation didn't fully remove.
MIN_FREQUENCY_HZ = 70.0
MAX_FREQUENCY_HZ = 1500.0


def transcribe_notes(
    audio_path: Path,
    *,
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    minimum_note_length_ms: float = 70.0,
) -> list[Note]:
    """Run Basic Pitch over `audio_path` and return a flat list of `Note`s.

    Thresholds are tuned slightly toward precision over recall relative to the
    library defaults — a guitar stem from source separation carries some
    residual bleed from other instruments, and missing an occasional quiet note
    produces a far more usable tab than hallucinating notes from that bleed.
    """
    _, _, note_events = predict(
        audio_path,
        model_or_model_path=_ONNX_MODEL_PATH,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=minimum_note_length_ms,
        minimum_frequency=MIN_FREQUENCY_HZ,
        maximum_frequency=MAX_FREQUENCY_HZ,
        melodia_trick=True,
    )

    notes: list[Note] = []
    for start_s, end_s, pitch_midi, amplitude, pitch_bends in note_events:
        duration = max(end_s - start_s, 0.01)
        notes.append(Note(
            pitch_midi=int(pitch_midi),
            onset=float(start_s),
            duration=duration,
            confidence=float(amplitude),
            # Per-frame offsets in 1/3-semitone bins -> semitones; read by
            # `expression.py` to find bends and vibrato.
            pitch_bends=[float(b) / CONTOUR_BINS_PER_SEMITONE for b in pitch_bends] if pitch_bends is not None else None,
            contour_fps=float(ANNOTATIONS_FPS),
        ))

    notes = annotate_expressions(notes)
    notes.sort(key=lambda n: n.onset)
    return notes
