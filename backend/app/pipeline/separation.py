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

import threading
from pathlib import Path

MODEL_NAME = "htdemucs_6s"
TARGET_STEM = "guitar"

_model = None
_model_lock = threading.Lock()


def _load_model():
    """Load (once) and return the Demucs model, preparing ffmpeg on the way.

    Everything heavy lives in here rather than at module import time, on
    purpose. Importing torch/demucs costs seconds and gigabytes of optional
    dependencies, and `static_ffmpeg.add_paths()` may hit the network to
    download binaries. Doing either at import would mean the web server, the
    test suite (which stubs separation out entirely) and CI could not even
    start without torch installed and internet access — for a step that only
    runs once a real file is being processed.

    Unlike `ingest.py` (which calls `imageio_ffmpeg`'s bundled binary by full
    path), Demucs's `AudioFile`/`_read_info` shell out to the literal commands
    `ffmpeg`/`ffprobe` and expect them on PATH. `static_ffmpeg` downloads (once,
    then caches) a matching pair and prepends their directory to PATH, so this
    works out of the box without asking users to install ffmpeg system-wide.

    The lock matters once `FRETWISE_MAX_CONCURRENT_JOBS` > 1: two worker
    threads hitting a cold cache at the same time would otherwise both load
    (and possibly both download) the multi-hundred-MB model.
    """
    global _model
    with _model_lock:
        if _model is None:
            import static_ffmpeg
            from demucs.pretrained import get_model

            static_ffmpeg.add_paths()
            model = get_model(MODEL_NAME)
            model.eval()
            _model = model
    return _model


def separate_guitar_stem(audio_path: Path, output_path: Path, *, device: str = "cpu") -> Path:
    """Run Demucs on `audio_path` and write the isolated guitar stem to `output_path`.

    This Demucs version exposes no programmatic progress hook for `apply_model`
    (only an internal tqdm bar via `progress=True`), so the caller is expected
    to treat this as one opaque, possibly-slow step — the job manager reports
    coarse elapsed-time-based progress for stages like this rather than
    fabricating a fake per-chunk number.
    """
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile, save_audio

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
