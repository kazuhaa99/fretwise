"""API data models — the JSON contract with the frontend.

Field names mirror `frontend/src/types.ts` exactly (including its camelCase
keys, via Pydantic aliases) so the two can be kept in sync by inspection. Keep
this file and that one aligned when either changes.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..pipeline.fretboard import midi_to_note_name, note_name_to_midi

JobStage = Literal[
    "queued",
    "extracting_audio",
    "separating",
    "transcribing",
    "arranging",
    "exporting",
    "done",
    "error",
]

Technique = Literal["hammer_on", "pull_off", "slide", "bend", "vibrato", "palm_mute", "tremolo"]

_camel_config = ConfigDict(populate_by_name=True)


class JobStatus(BaseModel):
    id: str
    stage: JobStage
    progress: float = Field(ge=0.0, le=1.0)
    message: str
    error: Optional[str] = None


# Bounds for a believable open-string pitch. The low end leaves room for
# 8-string guitars (F#1) and basses (E1, B0 on a 5-string is already outside a
# guitar app's remit); the high end allows the highest common open tunings
# (open E tops out at E4) with slack. Anything outside is almost certainly a
# typo — "E7" instead of "E2" — and would otherwise sail through to the
# pipeline and produce a tab no instrument can play.
MIN_OPEN_STRING_MIDI = note_name_to_midi("A0")
MAX_OPEN_STRING_MIDI = note_name_to_midi("A4")
MIN_STRINGS = 4
MAX_STRINGS = 8
MAX_CAPO = 12


class TabSettings(BaseModel):
    """Arrangement settings sent alongside an upload.

    Validated up front, at the API boundary, instead of letting a bad value
    fail deep inside the pipeline minutes later: an unparseable note name used
    to surface only once `Fretboard.from_note_names` ran — after the user had
    already waited through source separation.
    """

    model_config = _camel_config

    tuning: list[str] = Field(min_length=MIN_STRINGS, max_length=MAX_STRINGS)
    capo: int = Field(0, ge=0, le=MAX_CAPO)
    max_fret_span: int = Field(4, alias="maxFretSpan", ge=2, le=8)
    position_jump_weight: float = Field(1.0, alias="positionJumpWeight", ge=0.0, le=4.0)

    @field_validator("tuning")
    @classmethod
    def _validate_tuning(cls, tuning: list[str]) -> list[str]:
        """Every string must be a parseable note in a plausible register.

        Messages are in Russian because they are shown to the user verbatim
        (see `api/errors.py`).
        """
        for index, name in enumerate(tuning, start=1):
            try:
                midi = note_name_to_midi(name)
            except ValueError:
                raise ValueError(f"струна {index}: «{name}» — не нота (ожидается, например, E2, F#3 или Bb1)") from None
            if not MIN_OPEN_STRING_MIDI <= midi <= MAX_OPEN_STRING_MIDI:
                raise ValueError(
                    f"струна {index}: нота {name} вне допустимого диапазона "
                    f"({midi_to_note_name(MIN_OPEN_STRING_MIDI)}–{midi_to_note_name(MAX_OPEN_STRING_MIDI)})"
                )
        return [name.strip() for name in tuning]


class TabNote(BaseModel):
    model_config = _camel_config

    string: int
    fret: int
    technique: Optional[Technique] = None
    # Set only when technique == "bend": bend size in semitones (1 = half-step, 2 = full).
    bend_semitones: Optional[float] = Field(None, alias="bendSemitones")


class TabEvent(BaseModel):
    time: float
    duration: float
    notes: list[TabNote]


class TabTrack(BaseModel):
    name: str
    events: list[TabEvent]


class TabData(BaseModel):
    model_config = _camel_config

    tuning: list[str]
    capo: int
    tempo_bpm: int = Field(alias="tempoBpm")
    duration_seconds: float = Field(alias="durationSeconds")
    beats_per_measure: int = Field(4, alias="beatsPerMeasure")
    # Start time (seconds) of every bar, matching the .gp5 export exactly. The
    # first may be negative when a pickup bar begins before the audio does.
    measure_times: list[float] = Field(default_factory=list, alias="measureTimes")
    tracks: list[TabTrack]


class JobResult(BaseModel):
    model_config = _camel_config

    job: JobStatus
    tab: TabData
    original_audio_url: str = Field(alias="originalAudioUrl")
    guitar_audio_url: str = Field(alias="guitarAudioUrl")
    gp5_url: str = Field(alias="gp5Url")
