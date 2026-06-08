"""API data models — the JSON contract with the frontend.

Field names mirror `frontend/src/types.ts` exactly (including its camelCase
keys, via Pydantic aliases) so the two can be kept in sync by inspection. Keep
this file and that one aligned when either changes.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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


class TabSettings(BaseModel):
    model_config = _camel_config

    tuning: list[str]
    capo: int = 0
    max_fret_span: int = Field(4, alias="maxFretSpan", ge=2, le=8)
    position_jump_weight: float = Field(1.0, alias="positionJumpWeight", ge=0.0, le=4.0)


class TabNote(BaseModel):
    string: int
    fret: int
    technique: Optional[Technique] = None


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
    tracks: list[TabTrack]


class JobResult(BaseModel):
    model_config = _camel_config

    job: JobStatus
    tab: TabData
    original_audio_url: str = Field(alias="originalAudioUrl")
    guitar_audio_url: str = Field(alias="guitarAudioUrl")
    gp5_url: str = Field(alias="gp5Url")
