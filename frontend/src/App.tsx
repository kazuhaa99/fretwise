import { useCallback, useEffect, useRef, useState } from "react";
import AppHeader from "./components/AppHeader";
import ProcessingScreen from "./components/ProcessingScreen";
import ResultScreen from "./components/ResultScreen";
import UploadScreen from "./components/UploadScreen";
import { Button } from "./components/ui";
import { cancelJob, errorMessage, getJobResult, getJobStatus, isNotFound, jobEventsUrl, uploadJob } from "./api/client";
import { useTheme } from "./theme";
import { DEFAULT_SETTINGS, type JobResult, type JobStatus, type TabSettings } from "./types";

type View = "upload" | "restoring" | "processing" | "result";

/**
 * The current job lives in the URL (`?job=<id>`), so a page reload — or a
 * bookmarked/shared link on the same machine — comes back to the same
 * processing screen or result instead of an empty upload form. `replaceState`
 * rather than `pushState`: the job isn't a separate page the Back button
 * should step through.
 */
const JOB_PARAM = "job";
const SETTINGS_KEY = "fretwise-settings";

function jobIdFromUrl(): string | null {
  const id = new URLSearchParams(window.location.search).get(JOB_PARAM);
  return id && /^[0-9a-f]{32}$/.test(id) ? id : null;
}

function setJobIdInUrl(jobId: string | null): void {
  const url = new URL(window.location.href);
  if (jobId) url.searchParams.set(JOB_PARAM, jobId);
  else url.searchParams.delete(JOB_PARAM);
  window.history.replaceState(window.history.state, "", url);
}

/** Last used settings are remembered per browser (a convenience only). */
function loadSettings(): TabSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<TabSettings>;
    if (!Array.isArray(parsed.tuning)) return DEFAULT_SETTINGS;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function saveSettings(settings: TabSettings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Storage unavailable: settings just won't be remembered.
  }
}

const JOB_GONE_MESSAGE =
  "Задача не найдена на сервере — возможно, он был перезапущен или задача удалена. Загрузи файл ещё раз.";

export default function App() {
  const [theme, setTheme] = useTheme();
  const [view, setView] = useState<View>(() => (jobIdFromUrl() ? "restoring" : "upload"));
  const [file, setFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<TabSettings>(loadSettings);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  // Lost contact with the job (backend down, job gone) — distinct from the
  // pipeline itself failing, which arrives as a normal `stage: "error"` status.
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => saveSettings(settings), [settings]);

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
    setCancelPending(false);
    setJobIdInUrl(null);
  }, [closeStream]);

  const loadResult = useCallback(async (jobId: string) => {
    try {
      const res = await getJobResult(jobId);
      setResult(res);
      setView("result");
    } catch (e) {
      // Shown in the processing view's error box (with retry), which is also
      // where a restore-from-URL lands when the result can't be fetched.
      setView("processing");
      setConnectionError(isNotFound(e) ? JOB_GONE_MESSAGE : errorMessage(e, "Не удалось загрузить результат"));
    }
  }, []);

  /**
   * Follow a job's progress over SSE until it finishes.
   *
   * EventSource reconnects on its own after a dropped connection and only
   * tells us via `onerror` — with no status code, so it can't distinguish a
   * proxy hiccup from a dead backend or a job that no longer exists. On every
   * error we ask the plain status endpoint what is going on: a healthy answer
   * means "let the browser reconnect" (or the job finished while we were
   * away), anything else stops the stream and tells the user.
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
        } else if (data.stage === "error" || data.stage === "cancelled") {
          closeStream();
          setCancelPending(false);
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
    setCancelPending(false);
    setSubmitting(true);
    try {
      const job = await uploadJob(file, settings);
      setStatus(job);
      setView("processing");
      setJobIdInUrl(job.id);
      watchJob(job.id);
    } catch (e) {
      const message = errorMessage(e, "Не удалось загрузить файл");
      // A retry from the processing screen has no upload form to show the
      // error next to, so it goes into the processing view's error box.
      if (view === "processing") setConnectionError(message);
      else setError(message);
    } finally {
      setSubmitting(false);
    }
  }, [file, settings, watchJob, view]);

  /** "Попробовать снова": resume watching the same job if the server still
   * has it, otherwise upload the same file again with the same settings. */
  const retry = useCallback(async () => {
    if (!status) return;
    if (status.stage !== "error" && status.stage !== "cancelled") {
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
    if (file) await startJob();
    else reset();
  }, [status, file, watchJob, startJob, reset]);

  const cancel = useCallback(async () => {
    if (!status) return;
    setCancelPending(true);
    try {
      const next = await cancelJob(status.id);
      setStatus(next);
      if (next.stage === "cancelled") {
        closeStream();
        setCancelPending(false);
      }
    } catch (e) {
      setCancelPending(false);
      setConnectionError(errorMessage(e, "Не удалось отменить обработку"));
    }
  }, [status, closeStream]);

  /** Pick up wherever a job is: processing, result or failure. Used for
   * `?job=<id>` on load (reload, bookmark) and for opening a job from the
   * history list. */
  const restoreJob = useCallback(
    async (jobId: string, isCancelled: () => boolean = () => false) => {
      try {
        const current = await getJobStatus(jobId);
        if (isCancelled()) return;
        setStatus(current);
        if (current.stage === "done") {
          await loadResult(jobId);
        } else {
          setView("processing");
          if (current.stage !== "error" && current.stage !== "cancelled") watchJob(jobId);
        }
      } catch (e) {
        if (isCancelled()) return;
        setJobIdInUrl(null);
        setView("upload");
        setError(
          isNotFound(e)
            ? "Задача из ссылки больше не существует — её удалили или срок хранения истёк. Загрузи файл заново."
            : errorMessage(e, "Не удалось восстановить задачу из ссылки"),
        );
      }
    },
    [loadResult, watchJob],
  );

  useEffect(() => {
    const jobId = jobIdFromUrl();
    if (!jobId) return;
    let cancelled = false;
    (async () => {
      await restoreJob(jobId, () => cancelled);
    })();
    return () => {
      cancelled = true;
    };
  }, [restoreJob]);

  const openJob = useCallback(
    (jobId: string) => {
      setError(null);
      setConnectionError(null);
      setFile(null);
      setJobIdInUrl(jobId);
      setView("restoring");
      void restoreJob(jobId);
    },
    [restoreJob],
  );

  useEffect(() => () => esRef.current?.close(), []);

  const headerActions =
    view === "processing" || view === "result" ? (
      <span className="hidden sm:block">
        <Button variant="ghost" onClick={reset}>
          Новый файл
        </Button>
      </span>
    ) : null;

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-raised focus:px-4 focus:py-2 focus:shadow-float"
      >
        К содержимому
      </a>
      <AppHeader theme={theme} onThemeChange={setTheme} onHome={view === "upload" ? undefined : reset} actions={headerActions} />

      <main
        id="main"
        className="mx-auto max-w-6xl px-4 pt-8 pb-16 sm:px-6 sm:pt-10"
        style={{
          paddingLeft: "max(16px, env(safe-area-inset-left, 0px))",
          paddingRight: "max(16px, env(safe-area-inset-right, 0px))",
        }}
      >
        {view === "upload" && (
          <UploadScreen
            file={file}
            onFileChange={setFile}
            settings={settings}
            onSettingsChange={setSettings}
            onSubmit={startJob}
            submitting={submitting}
            error={error}
            onOpenJob={openJob}
          />
        )}

        {view === "restoring" && (
          <div className="mx-auto grid max-w-3xl gap-4" role="status" aria-label="Загружаем задачу">
            <div className="h-8 w-2/3 rounded-md bg-line" style={{ animation: "fw-pulse 1.6s ease-in-out infinite" }} />
            <div className="h-40 rounded-lg bg-surface" />
            <p className="text-muted">Загружаем задачу…</p>
          </div>
        )}

        {view === "processing" && status && (
          <ProcessingScreen
            status={status}
            title={file?.name ?? null}
            connectionError={connectionError}
            cancelPending={cancelPending}
            canReupload={file !== null}
            onCancel={cancel}
            onRetry={retry}
            onReset={reset}
          />
        )}

        {view === "result" && result && <ResultScreen key={result.job.id} result={result} onReset={reset} />}
      </main>

      {view !== "result" && (
        <footer className="mx-auto max-w-6xl px-4 pb-10 text-center text-[13px] text-muted sm:px-6">
          Fretwise работает локально: файлы и модели не покидают этот компьютер.
        </footer>
      )}
    </div>
  );
}
