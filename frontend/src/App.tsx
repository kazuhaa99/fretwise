import { useCallback, useEffect, useRef, useState } from "react";
import UploadDropzone from "./components/UploadDropzone";
import SettingsPanel from "./components/SettingsPanel";
import ProcessingTimeline from "./components/ProcessingTimeline";
import TabViewer from "./components/TabViewer";
import AudioPlayer, { type AudioPlayerHandle } from "./components/AudioPlayer";
import { uploadJob, getJobResult, jobEventsUrl, gp5DownloadUrl } from "./api/client";
import { DEFAULT_SETTINGS, type JobResult, type JobStatus, type TabSettings } from "./types";

type View = "upload" | "processing" | "result";

export default function App() {
  const [view, setView] = useState<View>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<TabSettings>(DEFAULT_SETTINGS);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const playerRef = useRef<AudioPlayerHandle>(null);
  const esRef = useRef<EventSource | null>(null);

  const reset = () => {
    esRef.current?.close();
    esRef.current = null;
    setView("upload");
    setFile(null);
    setStatus(null);
    setResult(null);
    setError(null);
    setCurrentTime(0);
  };

  const startJob = useCallback(async () => {
    if (!file) return;
    setError(null);
    try {
      const job = await uploadJob(file, settings);
      setStatus(job);
      setView("processing");

      const es = new EventSource(jobEventsUrl(job.id));
      esRef.current = es;
      es.onmessage = async (ev) => {
        const data: JobStatus = JSON.parse(ev.data);
        setStatus(data);
        if (data.stage === "done") {
          es.close();
          esRef.current = null;
          try {
            const res = await getJobResult(job.id);
            setResult(res);
            setView("result");
          } catch (e) {
            setError(e instanceof Error ? e.message : "Не удалось загрузить результат");
          }
        } else if (data.stage === "error") {
          es.close();
          esRef.current = null;
        }
      };
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить файл");
    }
  }, [file, settings]);

  useEffect(() => () => esRef.current?.close(), []);

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
          <div className="mx-auto max-w-xl">
            <ProcessingTimeline status={status} />
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
