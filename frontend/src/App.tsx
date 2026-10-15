import { useCallback, useEffect, useRef, useState } from "react";
import UploadDropzone from "./components/UploadDropzone";
import SettingsPanel from "./components/SettingsPanel";
import ProcessingTimeline from "./components/ProcessingTimeline";
import TabViewer from "./components/TabViewer";
import AudioPlayer, { type AudioPlayerHandle } from "./components/AudioPlayer";
import {
  uploadJob,
  getJobResult,
  getJobStatus,
  jobEventsUrl,
  gp5DownloadUrl,
  errorMessage,
  isNotFound,
} from "./api/client";
import { DEFAULT_SETTINGS, type JobResult, type JobStatus, type TabSettings } from "./types";

type View = "upload" | "processing" | "result";

const JOB_GONE_MESSAGE =
  "Задача не найдена на сервере — возможно, он был перезапущен или задача удалена. Загрузи файл ещё раз.";

export default function App() {
  const [view, setView] = useState<View>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<TabSettings>(DEFAULT_SETTINGS);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Lost contact with the job (backend down, job gone) — distinct from the
  // pipeline itself failing, which arrives as a normal `stage: "error"` status.
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const playerRef = useRef<AudioPlayerHandle>(null);
  const esRef = useRef<EventSource | null>(null);

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const reset = useCallback(() => {
    closeStream();
    setView("upload");
    setFile(null);
    setStatus(null);
    setResult(null);
    setError(null);
    setConnectionError(null);
    setCurrentTime(0);
  }, [closeStream]);

  const loadResult = useCallback(async (jobId: string) => {
    try {
      const res = await getJobResult(jobId);
      setResult(res);
      setView("result");
    } catch (e) {
      setConnectionError(isNotFound(e) ? JOB_GONE_MESSAGE : errorMessage(e, "Не удалось загрузить результат"));
    }
  }, []);

  /**
   * Follow a job's progress over SSE until it finishes.
   *
   * EventSource reconnects on its own after a dropped connection and only
   * tells us via `onerror` — with no status code, so it can't distinguish a
   * proxy hiccup from a dead backend or a job that no longer exists. Without
   * handling it, a crashed backend left the UI on "processing" forever. On
   * every error we ask the plain status endpoint what is going on: a healthy
   * answer means "let the browser reconnect" (or the job finished while we
   * were away), anything else stops the stream and tells the user.
   */
  const watchJob = useCallback(
    (jobId: string) => {
      closeStream();
      setConnectionError(null);
      const es = new EventSource(jobEventsUrl(jobId));
      esRef.current = es;

      const handleStatus = (data: JobStatus) => {
        setStatus(data);
        if (data.stage === "done") {
          closeStream();
          void loadResult(jobId);
        } else if (data.stage === "error") {
          closeStream();
        }
      };

      es.onmessage = (ev) => {
        if (esRef.current !== es) return;
        handleStatus(JSON.parse(ev.data) as JobStatus);
      };

      es.onerror = async () => {
        if (esRef.current !== es) return;
        try {
          const current = await getJobStatus(jobId);
          if (esRef.current === es) handleStatus(current);
        } catch (e) {
          if (esRef.current !== es) return;
          closeStream();
          setConnectionError(isNotFound(e) ? JOB_GONE_MESSAGE : errorMessage(e, "Сервер недоступен"));
        }
      };
    },
    [loadResult, closeStream],
  );

  const startJob = useCallback(async () => {
    if (!file) return;
    setError(null);
    setConnectionError(null);
    try {
      const job = await uploadJob(file, settings);
      setStatus(job);
      setView("processing");
      watchJob(job.id);
    } catch (e) {
      const message = errorMessage(e, "Не удалось загрузить файл");
      // A retry from the processing screen has no upload form to show the
      // error next to, so it goes into the processing view's error box.
      if (view === "processing") setConnectionError(message);
      else setError(message);
    }
  }, [file, settings, watchJob, view]);

  /** "Попробовать снова": resume watching the same job if the server still
   * has it, otherwise upload the same file again with the same settings. */
  const retry = useCallback(async () => {
    if (!status) return;
    if (status.stage !== "error") {
      try {
        await getJobStatus(status.id);
        watchJob(status.id);
        return;
      } catch (e) {
        if (!isNotFound(e)) {
          setConnectionError(errorMessage(e, "Сервер недоступен"));
          return;
        }
      }
    }
    if (file) {
      await startJob();
    } else {
      reset();
    }
  }, [status, file, watchJob, startJob, reset]);

  useEffect(() => () => esRef.current?.close(), []);

  const failed = status?.stage === "error" || connectionError !== null;

  return (
    <div className="min-h-screen">
      <header className="border-b border-walnut-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
          <button onClick={reset} className="flex items-center gap-3">
            <Logo />
            <span className="text-lg font-semibold tracking-tight text-cream">Fretwise</span>
          </button>
          {view !== "upload" && (
            <button onClick={reset} className="text-sm text-muted transition-colors hover:text-brass">
              Разобрать другой трек
            </button>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-12">
        {view === "upload" && (
          <div className="space-y-8">
            <div className="text-center">
              <h1 className="text-3xl font-semibold tracking-tight text-cream sm:text-4xl">
                Превратим запись в табулатуру для гитары
              </h1>
              <p className="mx-auto mt-3 max-w-xl text-muted">
                Загрузи трек или видео — модель выделит партию гитары, распознает ноты по высоте звука
                и соберёт табы, которые реально сыграть руками.
              </p>
            </div>

            <UploadDropzone onFileSelected={setFile} />

            {file && (
              <div className="flex items-center gap-3 rounded-xl border border-walnut-700 bg-walnut-900/60 px-4 py-3">
                <FileIcon />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-cream">{file.name}</p>
                  <p className="text-xs text-muted">{(file.size / (1024 * 1024)).toFixed(1)} МБ</p>
                </div>
                <button onClick={() => setFile(null)} className="text-sm text-muted hover:text-red-400">
                  Убрать
                </button>
              </div>
            )}

            <SettingsPanel settings={settings} onChange={setSettings} />

            {error && <p className="text-center text-sm text-red-400">{error}</p>}

            <div className="flex justify-center">
              <button
                onClick={startJob}
                disabled={!file}
                className="rounded-full bg-brass px-8 py-3.5 text-sm font-semibold text-walnut-950 transition-all hover:bg-brass-light disabled:cursor-not-allowed disabled:opacity-40"
              >
                Построить табы
              </button>
            </div>
          </div>
        )}

        {view === "processing" && status && (
          <div className="mx-auto max-w-xl space-y-6">
            <ProcessingTimeline status={status} connectionError={connectionError} />
            {failed && (
              <div className="flex flex-wrap justify-center gap-3">
                <button
                  onClick={retry}
                  className="rounded-full bg-brass px-6 py-3 text-sm font-semibold text-walnut-950 transition-colors hover:bg-brass-light"
                >
                  Попробовать снова
                </button>
                <button
                  onClick={reset}
                  className="rounded-full px-6 py-3 text-sm font-medium text-cream ring-1 ring-walnut-600 transition-colors hover:ring-brass"
                >
                  Загрузить другой файл
                </button>
              </div>
            )}
          </div>
        )}

        {view === "result" && result && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-2xl font-semibold text-cream">Готово — вот твоя табулатура</h2>
                <p className="mt-1 text-sm text-muted">
                  Можно проиграть запись, следить за курсором по партитуре или скачать файл для Guitar Pro.
                </p>
              </div>
              <a
                href={gp5DownloadUrl(result.job.id)}
                download
                className="flex items-center gap-2 rounded-full bg-brass px-6 py-3 text-sm font-semibold text-walnut-950 transition-colors hover:bg-brass-light"
              >
                <DownloadIcon />
                Скачать .gp5
              </a>
            </div>

            <AudioPlayer
              ref={playerRef}
              originalUrl={result.originalAudioUrl}
              guitarUrl={result.guitarAudioUrl}
              onTimeUpdate={setCurrentTime}
              onDuration={() => {}}
            />

            <TabViewer
              key={result.job.id}
              tab={result.tab}
              currentTime={currentTime}
              onSeek={(t) => playerRef.current?.seekTo(t)}
            />
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-5xl px-6 py-10 text-center text-xs text-muted">
        Fretwise работает локально: файлы и модели не покидают эту машину.
      </footer>
    </div>
  );
}

function Logo() {
  return (
    <svg width="34" height="34" viewBox="0 0 40 40" fill="none">
      <rect width="40" height="40" rx="10" fill="#2a221b" />
      <path
        d="M13 28V12m0 16c0 2.2 1.8 4 4 4s4-1.8 4-4-1.8-4-4-4M13 12l5 5M13 12l-5 5"
        stroke="#d99a4e"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line x1="22" y1="14" x2="34" y2="14" stroke="#c9b896" strokeWidth="1.2" />
      <line x1="22" y1="18" x2="34" y2="18" stroke="#c9b896" strokeWidth="1.2" />
      <line x1="22" y1="22" x2="34" y2="22" stroke="#c9b896" strokeWidth="1.2" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="shrink-0 text-brass">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14 2v6h6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 19h16" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
