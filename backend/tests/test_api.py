"""End-to-end test of the HTTP wiring: upload -> SSE progress -> result -> downloads.

Demucs source separation needs a multi-hundred-MB model download on first use
(see `app/pipeline/separation.py`), so it's stubbed out here with a pass-through
copy — everything *around* it (job tracking, progress streaming, transcription,
tab building, GP5 export, and result/audio/download serving) runs for real
against a real audio fixture. Separation itself is exercised manually/via the
running app, not in the unit-test suite.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import guitarpro as gp
import pytest
from fastapi.testclient import TestClient

from app.jobs import manager as manager_module
from app.main import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_guitar.wav"

SETTINGS = {
    "tuning": ["E2", "A2", "D3", "G3", "B3", "E4"],
    "capo": 0,
    "maxFretSpan": 4,
    "positionJumpWeight": 1.0,
}


@pytest.fixture(autouse=True)
def stub_separation(monkeypatch):
    def fake_separate(audio_path, output_path, *, device="cpu"):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(Path(audio_path).read_bytes())
        return output_path

    monkeypatch.setattr(manager_module.separation, "separate_guitar_stem", fake_separate)


@pytest.fixture
def client(tmp_path):
    app = create_app(jobs_root=tmp_path / "jobs")
    with TestClient(app) as test_client:
        yield test_client


def _upload(client) -> str:
    with FIXTURE.open("rb") as fh:
        response = client.post(
            "/api/jobs",
            files={"file": ("synthetic_guitar.wav", fh, "audio/wav")},
            data={"settings": json.dumps(SETTINGS)},
        )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _wait_for_terminal_stage(client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["stage"] in ("done", "error"):
            return status
        time.sleep(0.25)
    raise TimeoutError(f"job {job_id} never reached a terminal stage")


def test_health_check(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_rejects_unsupported_file_extensions(client):
    response = client.post(
        "/api/jobs",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"settings": json.dumps(SETTINGS)},
    )
    assert response.status_code == 422


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404
    assert client.get("/api/jobs/does-not-exist/result").status_code == 404


def test_full_job_lifecycle_produces_a_working_result(client):
    job_id = _upload(client)

    final = _wait_for_terminal_stage(client, job_id)
    assert final["stage"] == "done", final

    result = client.get(f"/api/jobs/{job_id}/result").json()
    assert result["job"]["stage"] == "done"

    tab = result["tab"]
    assert tab["tuning"] == SETTINGS["tuning"]
    assert tab["capo"] == 0
    assert tab["durationSeconds"] > 0
    assert tab["tracks"], "expected at least one guitar track from the synthetic fixture"

    for track in tab["tracks"]:
        assert track["name"]
        assert track["events"], "expected at least one tab event in the track"
        for event in track["events"]:
            strings_used = [note["string"] for note in event["notes"]]
            assert len(strings_used) == len(set(strings_used)), "no two simultaneous notes on one string"
            fretted = [note["fret"] for note in event["notes"] if note["fret"] > 0]
            if len(fretted) > 1:
                assert max(fretted) - min(fretted) <= SETTINGS["maxFretSpan"]

    # Audio playback endpoints
    original = client.get(result["originalAudioUrl"])
    assert original.status_code == 200
    assert original.headers["content-type"].startswith("audio/")

    guitar = client.get(result["guitarAudioUrl"])
    assert guitar.status_code == 200

    # GP5 download — must be a real, parseable Guitar Pro file
    gp5_response = client.get(result["gp5Url"])
    assert gp5_response.status_code == 200
    assert gp5_response.headers["content-disposition"].endswith('.gp5"')

    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as fh:
        fh.write(gp5_response.content)
        gp5_path = Path(fh.name)
    try:
        song = gp.parse(str(gp5_path))
        assert len(song.tracks) == 1
    finally:
        gp5_path.unlink(missing_ok=True)


def test_sse_stream_reports_progress_through_to_done(client):
    job_id = _upload(client)

    stages_seen = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:"):].strip())
            if not stages_seen or stages_seen[-1] != payload["stage"]:
                stages_seen.append(payload["stage"])
            if payload["stage"] in ("done", "error"):
                break

    assert stages_seen[0] in ("queued", "extracting_audio")
    assert stages_seen[-1] == "done"
    # stages should appear in pipeline order (subject to de-duplication of repeats)
    order = ["queued", "extracting_audio", "separating", "transcribing", "arranging", "exporting", "done"]
    indices = [order.index(stage) for stage in stages_seen]
    assert indices == sorted(indices), f"stages out of order: {stages_seen}"
