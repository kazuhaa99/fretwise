"""Ingest must keep stereo for Demucs (see `ingest.py`'s module docstring)."""

import numpy as np
import soundfile as sf

from app.jobs.manager import _load_mono_samples
from app.pipeline.ingest import audio_duration_seconds, extract_audio


def _write_stereo(path, seconds=0.5, sr=44100):
    t = np.arange(int(seconds * sr)) / sr
    left = 0.4 * np.sin(2 * np.pi * 220 * t)
    right = 0.4 * np.sin(2 * np.pi * 330 * t)  # different content per channel
    sf.write(str(path), np.stack([left, right], axis=1), sr)


def test_extract_audio_keeps_both_stereo_channels(tmp_path):
    source = tmp_path / "in.wav"
    _write_stereo(source)
    out = extract_audio(source, tmp_path / "out.wav")

    data, sr = sf.read(str(out), always_2d=True)
    assert sr == 44100
    assert data.shape[1] == 2
    # The channels must still differ — i.e. not a mono signal duplicated.
    assert np.abs(data[:, 0] - data[:, 1]).max() > 0.1
    assert abs(audio_duration_seconds(out) - 0.5) < 0.01


def test_mono_source_is_upmixed_to_stereo(tmp_path):
    source = tmp_path / "mono.wav"
    sf.write(str(source), np.zeros(4410), 44100)
    data, _ = sf.read(str(extract_audio(source, tmp_path / "out.wav")), always_2d=True)
    assert data.shape[1] == 2


def test_rhythm_analysis_still_gets_mono(tmp_path):
    source = tmp_path / "in.wav"
    _write_stereo(source)
    samples, sr = _load_mono_samples(source)
    assert samples.ndim == 1
    assert sr == 44100
