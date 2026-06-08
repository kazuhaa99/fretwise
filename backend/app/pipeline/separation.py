"""Isolates the guitar stem from a full mix using Demucs.

We use `htdemucs_6s` — the 6-source Hybrid Transformer Demucs model, the only
stock Demucs model that separates a dedicated "guitar" stem (drums, bass,
other, vocals, guitar, piano). That gets us a clean guitar-only signal directly,
rather than the much messier "subtract everything we can recognize" approach
four-stem models would force on us.

Model weights (~hundreds of MB) are downloaded once on first use and cached by
Demucs/Torch Hub under the user's home directory — see the project README.
"""

from __future__ import annotations

from pathlib import Path

import static_ffmpeg
import torch
from demucs.apply import apply_model
from demucs.audio import AudioFile, save_audio
from demucs.pretrained import get_model

MODEL_NAME = "htdemucs_6s"
TARGET_STEM = "guitar"

_model = None

# Unlike `ingest.py` (which calls `imageio_ffmpeg`'s bundled binary by full
# path), Demucs's `AudioFile`/`_read_info` shell out to the literal commands
# `ffmpeg`/`ffprobe` and expect them on PATH. `static_ffmpeg` downloads (once,
# then caches) a matching pair and prepends their directory to PATH, so this
# works out of the box without asking users to install ffmpeg system-wide.
static_ffmpeg.add_paths()


def _load_model():
    global _model
    if _model is None:
        _model = get_model(MODEL_NAME)
        _model.eval()
    return _model


def separate_guitar_stem(audio_path: Path, output_path: Path, *, device: str = "cpu") -> Path:
    """Run Demucs on `audio_path` and write the isolated guitar stem to `output_path`.

    This Demucs version exposes no programmatic progress hook for `apply_model`
    (only an internal tqdm bar via `progress=True`), so the caller is expected
    to treat this as one opaque, possibly-slow step — the job manager reports
    coarse elapsed-time-based progress for stages like this rather than
    fabricating a fake per-chunk number.
    """
    model = _load_model()
    model.to(device)

    audio = AudioFile(audio_path).read(
        streams=0,
        samplerate=model.samplerate,
        channels=model.audio_channels,
    )

    # Demucs is trained on normalized audio; normalize by the mix's own
    # statistics and undo it afterwards (the standard Demucs separation recipe).
    reference = audio.mean(0)
    mean, std = reference.mean(), reference.std()
    normalized = (audio - mean) / (std + 1e-8)

    with torch.no_grad():
        estimates = apply_model(model, normalized[None], device=device, split=True, overlap=0.25)[0]

    estimates = estimates * std + mean

    guitar_index = model.sources.index(TARGET_STEM)
    guitar = estimates[guitar_index]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_audio(guitar, str(output_path), samplerate=model.samplerate)
    return output_path
