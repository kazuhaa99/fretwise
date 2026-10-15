"""Normalizes any uploaded audio or video file into a clean stereo WAV.

ffmpeg pulls the audio track out of a video container exactly the same way it
transcodes a standalone audio file, so there's no need to special-case "is this
a video" — we just always demux/transcode through ffmpeg and let it figure out
the input format. `imageio-ffmpeg` bundles a static ffmpeg binary so users don't
have to install one system-wide for a "just works" local app.

The WAV is deliberately *stereo*. It used to be downmixed to mono here, before
anything else ran — which threw away exactly the information Demucs leans on
most: instruments panned to different positions in the stereo field are far
easier to pull apart than the same instruments summed into one channel. Mono
is produced only where it is actually needed, by the consumers themselves:
Basic Pitch loads its input with `librosa.load(mono=True)`, and the tempo /
rhythm analysis averages channels (`jobs/manager.py:_load_mono_samples`).
Mono sources are simply upmixed (both channels identical), which costs Demucs
nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import soundfile as sf

TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2


def _ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def extract_audio(
    input_path: Path,
    output_path: Path,
    sample_rate: int = TARGET_SAMPLE_RATE,
    channels: int = TARGET_CHANNELS,
) -> Path:
    """Decode `input_path` (audio or video, any ffmpeg-readable format) into a
    WAV with `channels` channels at `sample_rate`, written to `output_path`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-f", "wav",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        tail = result.stderr.strip().splitlines()[-12:]
        raise RuntimeError("Не удалось извлечь звук из файла:\n" + "\n".join(tail))
    return output_path


def audio_duration_seconds(wav_path: Path) -> float:
    info = sf.info(str(wav_path))
    return float(info.frames) / float(info.samplerate)
