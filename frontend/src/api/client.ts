import type { JobResult, JobStatus, JobSummary, TabEvent, TabSettings, TabTrack } from "../types";

const BASE = "/api";

/**
 * An HTTP error with a message that is safe to show the user as-is.
 *
 * `status` is 0 when the request never reached the server (network down,
 * backend not running), so callers can tell "the server said no" apart from
 * "there is no server" — the processing screen reacts differently to each.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** One entry of FastAPI's default validation error list. */
interface PydanticError {
  loc?: (string | number)[];
  msg?: string;
}

const FIELD_LABELS: Record<string, string> = {
  file: "файл",
  settings: "настройки",
  tuning: "строй",
  capo: "каподастр",
  maxFretSpan: "максимальная растяжка",
  positionJumpWeight: "вес переходов по грифу",
};

const STATUS_FALLBACKS: Record<number, string> = {
  404: "Задача не найдена",
  409: "Обработка ещё не завершена",
  413: "Файл слишком большой",
  422: "Сервер не принял запрос",
};

function describePydanticErrors(errors: PydanticError[]): string {
  const parts = errors.map((err) => {
    const field = (err.loc ?? []).find((part) => typeof part === "string" && part in FIELD_LABELS);
    const label = field ? FIELD_LABELS[field as string] : null;
    // FastAPI's own messages are English ("Field required"); the one that
    // actually happens in practice — a missing form part — gets a translation.
    const msg = err.msg === "Field required" ? "обязательное поле не заполнено" : (err.msg ?? "некорректное значение");
    return label ? `${label} — ${msg}` : msg;
  });
  return `Некорректные данные запроса: ${parts.join("; ")}`;
}

/**
 * Build a human-readable Russian message from an error response.
 *
 * The backend reports problems as `{"detail": "..."}` — already a Russian
 * sentence for everything our own code raises — but FastAPI's built-in
 * request validation sends `{"detail": [{loc, msg, type}, ...]}` instead, and
 * proxies (nginx 413/502) send HTML. Showing any of that raw used to produce
 * messages like `Upload failed: 422 {"detail":...}`.
 */
export function describeErrorResponse(status: number, body: string): string {
  let detail: unknown;
  try {
    detail = (JSON.parse(body) as { detail?: unknown })?.detail;
  } catch {
    detail = undefined;
  }

  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) return describePydanticErrors(detail as PydanticError[]);

  if (status in STATUS_FALLBACKS) return STATUS_FALLBACKS[status];
  if (status >= 500) return `Ошибка сервера (${status}). Попробуй ещё раз чуть позже.`;
  return `Запрос не выполнен (код ${status})`;
}

export const NETWORK_ERROR_MESSAGE = "Не удалось связаться с сервером. Проверь, что бэкенд запущен, и попробуй снова.";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, NETWORK_ERROR_MESSAGE);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, describeErrorResponse(res.status, body));
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** The message to show for any caught error, with a context-specific fallback. */
export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  return fallback;
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export async function uploadJob(file: File, settings: TabSettings): Promise<JobStatus> {
  const form = new FormData();
  form.append("file", file);
  form.append("settings", JSON.stringify(settings));
  return request<JobStatus>(`${BASE}/jobs`, { method: "POST", body: form });
}

/** Job history, newest first. */
export async function listJobs(): Promise<JobSummary[]> {
  return request<JobSummary[]>(`${BASE}/jobs`);
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`${BASE}/jobs/${jobId}`);
}

export async function getJobResult(jobId: string): Promise<JobResult> {
  return request<JobResult>(`${BASE}/jobs/${jobId}/result`);
}

export async function deleteJob(jobId: string): Promise<void> {
  return request<void>(`${BASE}/jobs/${jobId}`, { method: "DELETE" });
}

/** Save an edited copy of the tab's tracks; the server rebuilds the .gp5. */
export async function saveTab(jobId: string, baseRevision: number, tracks: TabTrack[]): Promise<JobResult> {
  return request<JobResult>(`${BASE}/jobs/${jobId}/tab`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ baseRevision, tracks }),
  });
}

/** Re-finger notes (same pitches, new strings/frets) around a position.
 * Nothing is saved — the caller commits the result as an ordinary edit. */
export async function refingerEvents(
  jobId: string,
  events: TabEvent[],
  position: number | null,
  openStrings: "auto" | "avoid" = "avoid",
): Promise<TabEvent[]> {
  const res = await request<{ events: TabEvent[] }>(`${BASE}/jobs/${jobId}/refinger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events, position, openStrings }),
  });
  return res.events;
}

/** Throw away every manual edit and go back to the pipeline's output. */
export async function revertEdits(jobId: string): Promise<JobResult> {
  return request<JobResult>(`${BASE}/jobs/${jobId}/edits`, { method: "DELETE" });
}

export function jobEventsUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/events`;
}

export async function cancelJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" });
}

export function gp5DownloadUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/export.gp5`;
}
