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
import threading
import time
from pathlib import Path
from urllib.parse import quote

import guitarpro as gp
import pytest
from fastapi.testclient import TestClient

from app.api import upload as upload_api
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
    # sse-starlette keeps a module-global exit event bound to the event loop
    # that first used it; every TestClient runs its own loop, so a second SSE
    # test would otherwise fail with "bound to a different event loop".
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
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


def _upload_named(client, filename: str) -> str:
    with FIXTURE.open("rb") as fh:
        response = client.post(
            "/api/jobs",
            files={"file": (filename, fh, "audio/wav")},
            data={"settings": json.dumps(SETTINGS)},
        )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_download_keeps_original_cyrillic_filename_and_gp_title(client):
    job_id = _upload_named(client, "Кино — Группа крови.wav")
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"

    response = client.get(f"/api/jobs/{job_id}/export.gp5")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    expected = quote("Кино — Группа крови.gp5")
    assert f"filename*=utf-8''{expected}" in disposition

    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as fh:
        fh.write(response.content)
        gp5_path = Path(fh.name)
    try:
        # GP5 has no Unicode strings; Cyrillic text is stored as cp1251 (see gp_export).
        assert gp.parse(str(gp5_path), encoding="cp1251").title == "Кино — Группа крови"
    finally:
        gp5_path.unlink(missing_ok=True)


def test_empty_upload_is_rejected_without_leaving_files(client, tmp_path):
    response = client.post(
        "/api/jobs",
        files={"file": ("silence.wav", b"", "audio/wav")},
        data={"settings": json.dumps(SETTINGS)},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Файл пуст"
    assert list((tmp_path / "jobs").iterdir()) == []


def test_oversized_upload_is_rejected_while_streaming(client, tmp_path, monkeypatch):
    # Bigger than the limit, but within the multipart slack, so the request
    # passes the Content-Length pre-check and is caught by the streaming count.
    monkeypatch.setattr(upload_api, "MAX_UPLOAD_BYTES", 1000)
    response = client.post(
        "/api/jobs",
        files={"file": ("big.wav", b"x" * 5000, "audio/wav")},
        data={"settings": json.dumps(SETTINGS)},
    )
    assert response.status_code == 413
    assert "слишком большой" in response.json()["detail"]
    assert list((tmp_path / "jobs").iterdir()) == []


def test_oversized_upload_is_rejected_by_content_length(client, monkeypatch):
    monkeypatch.setattr(upload_api, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/api/jobs",
        files={"file": ("big.wav", b"x" * 200_000, "audio/wav")},
        data={"settings": json.dumps(SETTINGS)},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"capo": 13}, "каподастр — значение должно быть не больше 12"),
        ({"capo": -1}, "каподастр — значение должно быть не меньше 0"),
        ({"tuning": ["E2", "A2", "D3"]}, "строй — нужно минимум 4 струны"),
        ({"tuning": ["E2"] * 9}, "строй — допускается максимум 8 струн"),
        ({"tuning": ["E2", "A2", "H3", "G3", "B3", "E4"]}, "струна 3: «H3» — не нота"),
        ({"tuning": ["E2", "A2", "D3", "G3", "B3", "E7"]}, "струна 6: нота E7 вне допустимого диапазона"),
    ],
)
def test_invalid_settings_get_a_readable_russian_422(client, overrides, fragment):
    with FIXTURE.open("rb") as fh:
        response = client.post(
            "/api/jobs",
            files={"file": ("a.wav", fh, "audio/wav")},
            data={"settings": json.dumps({**SETTINGS, **overrides})},
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith("Некорректные настройки: ")
    assert fragment in detail


def test_malformed_settings_json_is_a_readable_422(client):
    with FIXTURE.open("rb") as fh:
        response = client.post(
            "/api/jobs",
            files={"file": ("a.wav", fh, "audio/wav")},
            data={"settings": "{not json"},
        )
    assert response.status_code == 422
    assert "не удалось разобрать JSON" in response.json()["detail"]


def test_delete_job(client, tmp_path):
    job_id = _upload(client)
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"
    assert (tmp_path / "jobs" / job_id).exists()

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert not (tmp_path / "jobs" / job_id).exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404


def test_delete_running_job_is_refused(client, monkeypatch):
    release = threading.Event()

    def blocking_separate(audio_path, output_path, *, device="cpu"):
        release.wait(timeout=30)
        output_path.write_bytes(Path(audio_path).read_bytes())
        return output_path

    monkeypatch.setattr(manager_module.separation, "separate_guitar_stem", blocking_separate)
    job_id = _upload(client)
    try:
        response = client.delete(f"/api/jobs/{job_id}")
        assert response.status_code == 409
        assert "выполняется" in response.json()["detail"]
    finally:
        release.set()
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"


def test_result_carries_title_beats_and_status_fields(client):
    job_id = _upload_named(client, "Мой рифф.wav")
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"
    result = client.get(f"/api/jobs/{job_id}/result").json()
    assert result["title"] == "Мой рифф"
    tab = result["tab"]
    assert tab["beatTimes"], "expected beat times for the metronome"
    assert tab["beatTimes"] == sorted(tab["beatTimes"])
    assert len(tab["beatTimes"]) == len(tab["measureTimes"]) * tab["beatsPerMeasure"]
    assert tab["measureTimes"][0] == pytest.approx(tab["beatTimes"][0])
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["etaSeconds"] == 0.0
    assert status["cancelRequested"] is False
    assert "queuePosition" in status


def test_cancel_running_job_over_http_ends_the_sse_stream(client, monkeypatch):
    release = threading.Event()

    def blocking_separate(audio_path, output_path, *, device="cpu"):
        release.wait(timeout=30)
        output_path.write_bytes(Path(audio_path).read_bytes())
        return output_path

    monkeypatch.setattr(manager_module.separation, "separate_guitar_stem", blocking_separate)
    job_id = _upload(client)
    deadline = time.monotonic() + 30
    while client.get(f"/api/jobs/{job_id}").json()["stage"] != "separating":
        assert time.monotonic() < deadline
        time.sleep(0.05)

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelRequested"] is True
    assert response.json()["message"] == "Отменяем после текущего шага…"
    release.set()

    stages = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data:"):
                stages.append(json.loads(line[5:].strip())["stage"])
                if stages[-1] in ("done", "error", "cancelled"):
                    break
    assert stages[-1] == "cancelled"
    assert "transcribing" not in stages
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    assert client.post("/api/jobs/nope/cancel").status_code == 404
