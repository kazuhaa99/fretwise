"""End-to-end test of the HTTP wiring: upload -> SSE progress -> result -> downloads.

Demucs source separation needs a multi-hundred-MB model download on first use
(see `app/pipeline/separation.py`), so it's stubbed out here with a pass-through
copy — everything *around* it (job tracking, progress streaming, transcription,
tab building, GP5 export, and result/audio/download serving) runs for real
against a real audio fixture. Separation itself is exercised manually/via the
running app, not in the unit-test suite.
"""

from __future__ import annotations

import copy
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


def _done_result(client) -> tuple[str, dict]:
    job_id = _upload(client)
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"
    return job_id, client.get(f"/api/jobs/{job_id}/result").json()


def _gp5_struck_frets(client, job_id) -> list[list[int]]:
    """Frets of every struck beat, per beat (a chord is one inner list)."""
    content = client.get(f"/api/jobs/{job_id}/export.gp5").content
    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as fh:
        fh.write(content)
        path = Path(fh.name)
    try:
        song = gp.parse(str(path))
        return [
            sorted(n.value for n in b.notes if n.type == gp.NoteType.normal)
            for m in song.tracks[0].measures
            for b in m.voices[0].beats
            if any(n.type == gp.NoteType.normal for n in b.notes)
        ]
    finally:
        path.unlink(missing_ok=True)


def test_edit_tab_persists_and_rebuilds_gp5(client):
    job_id, result = _done_result(client)
    assert result["revision"] == 0 and result["edited"] is False
    tracks = copy.deepcopy(result["tab"]["tracks"])
    first = tracks[0]["events"][0]["notes"][0]
    first["fret"] = 12 if first["fret"] != 12 else 11
    new_fret = first["fret"]
    tracks[0]["events"].pop()  # delete the last event

    response = client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == 1 and saved["edited"] is True
    assert saved["tab"]["tracks"][0]["events"][0]["notes"][0]["fret"] == new_fret
    assert saved["tab"]["measureTimes"] == result["tab"]["measureTimes"]

    # The result endpoint and the .gp5 both reflect the edit.
    again = client.get(f"/api/jobs/{job_id}/result").json()
    assert again["tab"] == saved["tab"]
    beats = _gp5_struck_frets(client, job_id)
    assert new_fret in beats[0]
    assert len(beats) == len(result["tab"]["tracks"][0]["events"]) - 1


def test_stale_revision_is_refused(client):
    job_id, result = _done_result(client)
    tracks = result["tab"]["tracks"]
    assert client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks}).status_code == 200
    stale = client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks})
    assert stale.status_code == 409
    assert "другой вкладке" in stale.json()["detail"]


def test_invalid_edit_gets_a_readable_422_and_changes_nothing(client):
    job_id, result = _done_result(client)
    tracks = result["tab"]["tracks"]
    tracks[0]["events"][0]["notes"][0]["string"] = 9
    response = client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Правка не сохранена: ")
    assert client.get(f"/api/jobs/{job_id}/result").json()["revision"] == 0


def test_revert_restores_the_pipeline_output(client):
    job_id, result = _done_result(client)
    original_frets = _gp5_struck_frets(client, job_id)
    tracks = copy.deepcopy(result["tab"]["tracks"])
    tracks[0]["events"] = tracks[0]["events"][:1]
    client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks})

    reverted = client.delete(f"/api/jobs/{job_id}/edits")
    assert reverted.status_code == 200
    body = reverted.json()
    assert body["edited"] is False and body["revision"] == 2
    assert body["tab"] == result["tab"]
    assert _gp5_struck_frets(client, job_id) == original_frets


def test_editing_requires_a_finished_job(client):
    assert client.put("/api/jobs/nope/tab", json={"baseRevision": 0, "tracks": []}).status_code == 404


def test_text_and_midi_exports_follow_edits(client):
    job_id = _upload_named(client, "Мой рифф.wav")
    assert _wait_for_terminal_stage(client, job_id)["stage"] == "done"
    result = client.get(f"/api/jobs/{job_id}/result").json()
    assert result["txtUrl"] == f"/api/jobs/{job_id}/export.txt"
    assert result["midiUrl"] == f"/api/jobs/{job_id}/export.mid"

    text = client.get(result["txtUrl"])
    assert text.status_code == 200
    assert text.headers["content-type"].startswith("text/plain")
    assert f"filename*=utf-8''{quote('Мой рифф.txt')}" in text.headers["content-disposition"]
    assert "e|" in text.text and "E|" in text.text

    midi = client.get(result["midiUrl"])
    assert midi.status_code == 200
    assert midi.content[:4] == b"MThd"

    # Edit: every note to fret 21 — unusual enough to find in the text export.
    tracks = copy.deepcopy(result["tab"]["tracks"])
    for event in tracks[0]["events"]:
        for note in event["notes"]:
            note["fret"] = 21
    assert client.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks}).status_code == 200
    edited_text = client.get(result["txtUrl"]).text
    assert "21" in edited_text and edited_text != text.text
    assert client.get(result["midiUrl"]).content != midi.content


def test_jobs_survive_a_restart_with_their_edits(tmp_path):
    from sse_starlette.sse import AppStatus

    jobs_root = tmp_path / "persist"
    AppStatus.should_exit_event = None
    with TestClient(create_app(jobs_root=jobs_root)) as first:
        job_id = _upload_named(first, "Песня.wav")
        assert _wait_for_terminal_stage(first, job_id)["stage"] == "done"
        result = first.get(f"/api/jobs/{job_id}/result").json()
        tracks = copy.deepcopy(result["tab"]["tracks"])
        tracks[0]["events"] = tracks[0]["events"][:2]
        assert first.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 0, "tracks": tracks}).status_code == 200

    # A job the restart catches mid-pipeline, and a folder with no metadata.
    interrupted = jobs_root / ("b" * 32)
    interrupted.mkdir()
    (interrupted / "upload.wav").write_bytes(b"x")
    (interrupted / "job.json").write_text(json.dumps({
        "id": "b" * 32, "title": "Недоделанная", "settings": SETTINGS,
        "status": {"id": "b" * 32, "stage": "separating", "progress": 0.4, "message": "..."},
        "createdAt": time.time() - 60, "finishedAt": None, "revision": 0, "uploadSuffix": ".wav",
    }), encoding="utf-8")
    orphan = jobs_root / ("c" * 32)
    orphan.mkdir()

    AppStatus.should_exit_event = None
    with TestClient(create_app(jobs_root=jobs_root)) as second:
        restored = second.get(f"/api/jobs/{job_id}/result")
        assert restored.status_code == 200
        body = restored.json()
        assert body["title"] == "Песня"
        assert body["edited"] is True and body["revision"] == 1
        assert len(body["tab"]["tracks"][0]["events"]) == 2
        assert second.get(f"/api/jobs/{job_id}/export.gp5").status_code == 200
        # Editing continues from the persisted revision.
        assert second.put(f"/api/jobs/{job_id}/tab", json={"baseRevision": 1, "tracks": tracks}).status_code == 200

        status = second.get(f"/api/jobs/{'b' * 32}").json()
        assert status["stage"] == "error"
        assert "перезапущен" in status["error"]
        assert not (interrupted / "upload.wav").exists()
        assert not orphan.exists()

        history = second.get("/api/jobs").json()
        assert [h["id"] for h in history] == [job_id, "b" * 32]  # newest first
        assert history[0] == {
            "id": job_id, "title": "Песня", "stage": "done", "createdAt": pytest.approx(history[0]["createdAt"]),
            "finishedAt": pytest.approx(history[0]["finishedAt"]), "durationSeconds": pytest.approx(history[0]["durationSeconds"]),
            "edited": True, "error": None,
        }
        assert history[0]["finishedAt"] >= history[0]["createdAt"]

        # Deleting from the history removes it for good.
        assert second.delete(f"/api/jobs/{job_id}").status_code == 204

    AppStatus.should_exit_event = None
    with TestClient(create_app(jobs_root=jobs_root)) as third:
        assert third.get(f"/api/jobs/{job_id}").status_code == 404


def test_expired_jobs_are_dropped_on_restart(tmp_path):
    from sse_starlette.sse import AppStatus

    jobs_root = tmp_path / "ttl"
    AppStatus.should_exit_event = None
    with TestClient(create_app(jobs_root=jobs_root, job_ttl_hours=1.0)) as first:
        job_id = _upload(first)
        assert _wait_for_terminal_stage(first, job_id)["stage"] == "done"

    meta_path = jobs_root / job_id / "job.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["finishedAt"] -= 2 * 3600  # finished two hours ago, by the wall clock
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    AppStatus.should_exit_event = None
    with TestClient(create_app(jobs_root=jobs_root, job_ttl_hours=1.0)) as second:
        assert second.get(f"/api/jobs/{job_id}").status_code == 404
        assert not (jobs_root / job_id).exists()
