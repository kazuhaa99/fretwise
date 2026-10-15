import type { JobStage, JobStatus } from "../types";
import { formatEta, plural } from "../lib/format";
import { CheckIcon } from "./icons";
import { Alert, Button, Spinner } from "./ui";

const STAGES: { key: JobStage; label: string; hint: string }[] = [
  { key: "extracting_audio", label: "Извлечение звука", hint: "Достаём аудиодорожку из файла" },
  { key: "separating", label: "Разделение дорожек", hint: "Выделяем гитару из микса — самый долгий шаг" },
  { key: "transcribing", label: "Распознавание нот", hint: "Находим высоту, начало и длительность нот" },
  { key: "arranging", label: "Подбор аппликатуры", hint: "Раскладываем ноты так, чтобы их можно было сыграть" },
  { key: "exporting", label: "Сборка табулатуры", hint: "Размечаем такты и готовим файл Guitar Pro" },
];

// Fret positions of the classic inlay markers, one per pipeline stage.
const INLAY_FRETS = [3, 5, 7, 9, 12];

function stageIndex(stage: JobStage): number {
  if (stage === "done") return STAGES.length;
  return STAGES.findIndex((s) => s.key === stage);
}

/** x of fret `n` on a neck drawn from `nut` to `end` (12th fret near the end),
 * spaced like a real fretboard: each fret 2^(1/12) closer than the last. */
function fretX(n: number, nut: number, end: number): number {
  const scale = (end - nut) / (1 - 2 ** (-13 / 12));
  return nut + scale * (1 - 2 ** (-n / 12));
}

/**
 * Processing stages as inlay markers on a guitar neck — the one decorative
 * element in the app (direction A borrows it from direction C). The neck fills
 * with the accent tint up to the current stage; done markers are solid,
 * the current one has a ring, pending ones are outlines.
 */
function FretboardStages({ current, progress, idle }: { current: number; progress: number; idle: boolean }) {
  const nut = 14;
  const end = 626;
  const top = 18;
  const bottom = 78;
  const markerX = INLAY_FRETS.map((f) => (fretX(f - 1, nut, end) + fretX(f, nut, end)) / 2);
  const prevX = current <= 0 ? nut : markerX[Math.min(current, markerX.length) - 1];
  const nextX = current >= markerX.length ? end : markerX[current];
  const fillTo = idle ? nut : current >= markerX.length ? end : prevX + (nextX - prevX) * progress;
  const strings = [0, 1, 2, 3, 4, 5].map((i) => top + 6 + i * ((bottom - top - 12) / 5));

  return (
    <svg viewBox="0 0 640 96" className="block h-auto w-full" aria-hidden="true" focusable="false">
      <rect x={nut} y={top} width={end - nut} height={bottom - top} rx={6} fill="var(--surface)" stroke="var(--line)" />
      <rect x={nut} y={top} width={Math.max(0, fillTo - nut)} height={bottom - top} rx={6} fill="var(--accent-soft)" style={{ transition: "width 300ms ease-out" }} />
      {Array.from({ length: 13 }, (_, i) => i + 1).map((n) => (
        <line key={n} x1={fretX(n, nut, end)} y1={top} x2={fretX(n, nut, end)} y2={bottom} stroke="var(--line)" strokeWidth={1.5} />
      ))}
      <line x1={nut} y1={top - 2} x2={nut} y2={bottom + 2} stroke="var(--line-strong)" strokeWidth={4} strokeLinecap="round" />
      {strings.map((y, i) => (
        <line key={i} x1={nut} y1={y} x2={end} y2={y} stroke="var(--string)" strokeWidth={1 + i * 0.15} />
      ))}
      {markerX.map((x, i) => {
        const done = !idle && i < current;
        const active = !idle && i === current;
        const ys = INLAY_FRETS[i] === 12 ? [top + 18, bottom - 18] : [(top + bottom) / 2];
        return ys.map((y, k) => (
          <circle
            key={`${i}-${k}`}
            cx={x}
            cy={y}
            r={active ? 8 : done ? 7 : 6}
            fill={done ? "var(--accent-strong)" : active ? "var(--surface)" : "var(--surface)"}
            stroke={done ? "var(--accent-strong)" : active ? "var(--accent)" : "var(--line-strong)"}
            strokeWidth={active ? 3 : 1.5}
            style={active ? { animation: "fw-pulse 1.6s ease-in-out infinite" } : undefined}
          />
        ));
      })}
    </svg>
  );
}

interface Props {
  status: JobStatus;
  title: string | null;
  connectionError: string | null;
  cancelPending: boolean;
  canReupload: boolean;
  onCancel: () => void;
  onRetry: () => void;
  onReset: () => void;
}

function errorParts(raw: string | null | undefined): { summary: string; details: string | null } {
  const text = (raw ?? "").trim();
  if (!text) return { summary: "Неизвестная ошибка. Попробуй другой файл.", details: null };
  const [first, ...rest] = text.split("\n");
  return { summary: first, details: rest.length ? rest.join("\n") : null };
}

export default function ProcessingScreen({
  status,
  title,
  connectionError,
  cancelPending,
  canReupload,
  onCancel,
  onRetry,
  onReset,
}: Props) {
  const failed = status.stage === "error";
  const cancelled = status.stage === "cancelled";
  const queued = status.stage === "queued";
  const lost = connectionError !== null;
  const stopped = failed || cancelled || lost;
  const current = stageIndex(status.stage);
  const cancelling = cancelPending || Boolean(status.cancelRequested);
  const eta = formatEta(status.etaSeconds);

  let heading: string;
  if (cancelled) heading = "Обработка остановлена";
  else if (failed) heading = "Не получилось разобрать файл";
  else if (lost) heading = "Нет связи с сервером";
  else if (queued) heading = "В очереди";
  else if (cancelling) heading = "Отменяем после текущего шага…";
  else heading = current >= 0 ? STAGES[Math.min(current, STAGES.length - 1)].label : "Обработка";

  const { summary, details } = errorParts(status.error);

  return (
    <div className="mx-auto grid max-w-3xl grid-cols-[minmax(0,1fr)] gap-6">
      <div className="grid gap-1">
        {title && <p className="truncate text-[13px] text-muted" title={title}>{title}</p>}
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h1 className="text-[26px] leading-tight font-bold sm:text-[28px]" aria-live="polite">
            {heading}
          </h1>
          {!stopped && eta && !queued && (
            <span className="tabular text-sm text-muted">осталось {eta}</span>
          )}
        </div>
        {!stopped && (
          <p className="text-muted">
            {queued && status.queuePosition
              ? `Перед вами ${status.queuePosition} ${plural(status.queuePosition, ["трек", "трека", "треков"])}. Начнём, как только освободится обработчик.`
              : status.message}
          </p>
        )}
      </div>

      <div className="rounded-lg border border-line bg-surface p-4 sm:p-5">
        <FretboardStages current={Math.max(current, 0)} progress={status.progress} idle={queued} />
        <ol className="mt-4 grid gap-3">
          {STAGES.map((stage, i) => {
            const done = current > i;
            const active = current === i && !stopped;
            return (
              <li key={stage.key} className="grid grid-cols-[28px_1fr_auto] items-center gap-3" aria-current={active ? "step" : undefined}>
                <span
                  className={`grid h-7 w-7 place-items-center rounded-full border-[1.5px] text-xs font-bold
                    ${done ? "border-accent-strong bg-accent-strong text-on-accent" : active ? "border-accent text-accent-strong shadow-[0_0_0_4px_var(--accent-soft)]" : "border-line-strong text-muted"}`}
                >
                  {done ? <CheckIcon size={14} strokeWidth={3} /> : i + 1}
                </span>
                <span className="grid min-w-0 gap-1.5">
                  <span className={done || active ? "font-medium" : "text-muted"}>{stage.label}</span>
                  {active && (
                    <>
                      <span className="text-[13px] text-muted">{stage.hint}</span>
                      <span className="h-1.5 overflow-hidden rounded-full bg-accent-soft" role="progressbar" aria-label={stage.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(status.progress * 100)}>
                        <span className="block h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(3, status.progress * 100)}%` }} />
                      </span>
                    </>
                  )}
                </span>
                <span className="tabular text-[13px] text-muted">
                  {done ? "готово" : active ? `${Math.round(status.progress * 100)}%` : ""}
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      {failed && (
        <div className="grid gap-2">
          <Alert title="Не получилось обработать файл">{summary}</Alert>
          {details && (
            <details className="text-[13px] text-muted">
              <summary className="min-h-11 cursor-pointer py-2">Подробности для отчёта</summary>
              <pre className="overflow-x-auto rounded-md border border-line bg-surface p-3 font-mono text-xs whitespace-pre-wrap">{details}</pre>
            </details>
          )}
        </div>
      )}
      {lost && !failed && !cancelled && <Alert title="Связь с обработкой потеряна">{connectionError}</Alert>}
      {cancelled && <p className="text-muted">Файлы этой задачи удалены. Можно загрузить запись снова — например, с другими настройками.</p>}

      <div className="flex flex-wrap gap-3">
        {stopped ? (
          <>
            {(!cancelled || canReupload) && (
              <Button onClick={onRetry}>{cancelled ? "Загрузить снова" : "Попробовать снова"}</Button>
            )}
            <Button variant="secondary" onClick={onReset}>
              Загрузить другой файл
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={onCancel} disabled={cancelling} aria-live="polite">
            {cancelling ? (
              <>
                <Spinner /> Отменяем после текущего шага…
              </>
            ) : (
              "Отменить"
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
