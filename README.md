# Fretwise

Fretwise is a local web app that turns audio or video into guitar tablature. It isolates the guitar part, detects notes and arranges them into tabs **a human can actually play**, so you never get impossible stretches like `1-3-15-17-18` in one chord.

![Fretwise tab viewer](docs/screenshot.png)

The resulting tab plays back in the browser with a cursor synced to the audio, and you can export it to `.gp5` for Guitar Pro. Everything runs locally: your files and the models stay on your machine.

## Features

- Upload any audio **or video** file (MP3, WAV, FLAC, M4A, MP4, MOV, MKV)
- Guitar stem separation with **Demucs** (`htdemucs_6s`)
- Note transcription (pitch, onset, duration) with Spotify's **Basic Pitch**
- **Playability-aware fingering**: a custom Viterbi-style dynamic program over the fretboard. It discards voicings outside the hand span or with two notes on one string, and minimises position shifts
- **Bends and vibrato** read from Basic Pitch's pitch contours, including whole-step bends the tracker splits into two notes
- **Bar lines that follow the music**: beat tracking, 3/4 vs 4/4 detection and downbeat alignment, with pickup bars when needed
- Tunable settings: guitar tuning (Standard, Drop D, Eb, Open D), capo, max fret span, position-shift weight
- Real-time processing progress over **Server-Sent Events**, with queue position, a remaining-time estimate and cancellation
- **Tab viewer laid out like a score**: bars wrap into rows, a bar's width follows how many notes it holds, bar numbers, time signature and tempo, automatic section labels (A, B…) when the material clearly repeats
- Playing techniques drawn the engraved way: P.M. with a dashed extender, bend arrows (½, full…), vibrato waves, hammer-on/pull-off arcs, slides
- **Floating player**: smooth cursor and current-bar highlight, click a note or bar to jump there, speed 25–150% without changing pitch, loop over selected bars, count-in, metronome on the detected beats, original mix or isolated guitar, volume, follow-the-cursor mode
- Keyboard shortcuts (Space, ←/→, L, +/−, M, C, G, ?) with an in-app list
- Light and dark themes («Шалфей» palette, WCAG AA contrast), usable from a 375 px phone to a wide monitor
- The job lives in the URL (`?job=…`), so a reload brings you back to the same progress screen or result
- **Fix notes in the browser**: change fret or string, delete or add notes, undo/redo (Ctrl+Z / Ctrl+Shift+Z); edits are saved on the server and every export includes them
- **Export** to Guitar Pro (`.gp5`, via PyGuitarPro), plain-text ASCII tab (`.txt`) and Standard MIDI (`.mid`), all on the same bar grid
- One-command **Docker Compose** setup

## Screens

| Upload | Processing | Result on a phone |
|---|---|---|
| ![Upload](docs/screenshots/upload-desktop-light.png) | ![Processing](docs/screenshots/processing-desktop-light.png) | ![Result, phone, dark](docs/screenshots/result-phone-dark.png) |

More in [`docs/screenshots/`](docs/screenshots). They are generated from the production build with a built-in demo job (no backend needed): `cd frontend && npm run build && npm run screenshots` (requires Playwright with Chromium).

## How it works

1. **Ingest**: `ffmpeg` extracts the audio track from any container, including video, and keeps it in stereo because Demucs separates stereo noticeably better.
2. **Separation**: [Demucs](https://github.com/facebookresearch/demucs) (`htdemucs_6s`) isolates the guitar from the mix.
3. **Transcription**: [Basic Pitch](https://github.com/spotify/basic-pitch) detects note pitch, onset and duration on the isolated stem. `expression.py` reads each note's pitch contour to mark bends and vibrato.
4. **Fingering**: `tab_builder.py` assigns notes to strings and frets. It never emits a chord whose fretted notes exceed the hand span or share a string, and it keeps the fretting hand in one position as long as possible.
5. **Rhythm**: `rhythm.py` tracks beats with librosa, chooses between 3/4 and 4/4 and finds the first downbeat, so bars line up with the music.
6. **Export**: [PyGuitarPro](https://github.com/Perlence/PyGuitarPro) writes the result to `.gp5` with the real tuning, the capo in the track's capo field, and bends and vibrato as Guitar Pro effects.
7. **UI**: a React app streams progress, plays the isolated or original track and renders the tab bar by bar with a playback cursor.

### HTTP API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Upload a file (`file`) with `settings` (JSON) and start a job |
| `GET` | `/api/jobs/{id}` | Job status: stage, progress, `queuePosition`, `etaSeconds`, `cancelRequested` |
| `GET` | `/api/jobs/{id}/events` | The same status as a Server-Sent Events stream |
| `POST` | `/api/jobs/{id}/cancel` | Cancel: immediately while queued, after the current step while running |
| `GET` | `/api/jobs/{id}/result` | Tab data (tracks, bars, beats, tempo, meter) and file URLs |
| `GET` | `/api/jobs/{id}/audio/{original\|guitar}` | Audio for playback (supports range requests) |
| `GET` | `/api/jobs/{id}/export.gp5` | Guitar Pro file named after the upload |
| `GET` | `/api/jobs/{id}/export.txt` | Plain-text (ASCII) tab |
| `GET` | `/api/jobs/{id}/export.mid` | Standard MIDI File |
| `PUT` | `/api/jobs/{id}/tab` | Save edited tracks (`baseRevision` + `tracks`); 409 if edited elsewhere |
| `DELETE` | `/api/jobs/{id}/edits` | Revert to the tab as transcribed |
| `DELETE` | `/api/jobs/{id}` | Delete a finished job and its files |

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic, sse-starlette |
| ML / audio | PyTorch, Demucs, Basic Pitch (ONNX), librosa, ffmpeg |
| Export | PyGuitarPro |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, Web Audio, Golos Text and JetBrains Mono (bundled) |
| Infra | Docker, Docker Compose, nginx |
| Tests | pytest, httpx, Vitest, Testing Library |

## Getting started

### Option 1: Docker (recommended)

```bash
docker compose up --build
```

Open <http://localhost:5173>. nginx serves the built frontend and proxies `/api/*` to the backend.

The first build takes a while because the backend image pulls PyTorch and the ML stack. The first processed track also downloads the Demucs weights. Named volumes keep state between restarts:

- `jobs_data`: uploaded files, intermediate stems and job results
- `model_cache`: Demucs weights (several hundred MB), referenced via `TORCH_HOME=/cache/torch`

The backend container runs as an unprivileged user, reports its health on `/api/health`, and the frontend only starts once that check passes.

Reset everything, including cached models and jobs:

```bash
docker compose down -v
```

### Option 2: Local development

Requirements: **Python 3.11** (the pinned NumPy/PyTorch versions don't support 3.13 yet), **Node.js 22+**. You don't need to install `ffmpeg` separately because the backend uses bundled binaries from `imageio-ffmpeg` / `static-ffmpeg`.

```bash
# Backend: FastAPI on http://127.0.0.1:8000
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend: Vite on http://localhost:5173 (proxies /api to the backend)
cd frontend
npm install
npm run dev
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FRETWISE_MAX_CONCURRENT_JOBS` | `1` | How many tracks are processed at once. Extra uploads wait in a queue and see their position. Each Demucs run can use several GB of RAM. |
| `FRETWISE_JOB_TTL_HOURS` | `24` | Finished jobs and their files are deleted after this many hours. Job folders left over from a previous run are removed on startup. |

A finished job can also be deleted right away with `DELETE /api/jobs/{id}`.

### Models

Models are downloaded on first use and cached:

- **Demucs `htdemucs_6s`**: several hundred MB, cached in `~/.cache/torch/hub/checkpoints/`. Downloading it makes the first track the slowest one.
- **Basic Pitch**: ships with the `basic-pitch` package, so there is nothing to download.

## Tests

```bash
cd backend
python -m pytest

cd frontend
npm test        # Vitest + Testing Library
npm run lint
```

The backend suite needs neither PyTorch nor Demucs nor network access, because separation is imported lazily and stubbed in tests. CI (`.github/workflows/ci.yml`) installs the requirements without `torch`/`torchaudio`/`demucs` and runs pytest, then runs the frontend's lint, tests and build.

The unit tests cover the fretboard model, the tab builder (span and string constraints, position minimisation), Guitar Pro export and the HTTP API. Demucs is replaced with a pass-through stub in tests because it is too heavy for unit tests. The full pipeline can be checked end to end with the scripts in `backend/scripts/`.

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── api/          # REST + SSE endpoints: upload, jobs, tabs/export
│   │   ├── jobs/         # Background job manager
│   │   ├── models/       # Pydantic schemas
│   │   ├── pipeline/     # ingest → separation → transcription → tab_builder → gp_export
│   │   └── main.py       # FastAPI app
│   ├── scripts/          # Smoke / end-to-end helpers
│   ├── tests/            # pytest suite + synthetic audio fixture
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/          # Typed API client
│   │   ├── components/   # UploadScreen, SettingsPanel, ProcessingScreen, ResultScreen, TabSheet, PlayerBar
│   │   ├── lib/          # barLayout, sections, playback, hotkeys, tuning (pure, unit-tested)
│   │   └── App.tsx
│   ├── scripts/          # screenshots.mjs (Playwright, demo job, no backend)
│   ├── nginx.conf        # Production proxy config
│   └── Dockerfile
└── docker-compose.yml
```

## License

[MIT](LICENSE)
