import type { JobStage, JobStatus } from "../types";

const STAGES: { key: JobStage; label: string; hint: string }[] = [
  { key: "extracting_audio", label: "Извлечение звука", hint: "Достаём аудиодорожку из файла" },
  { key: "separating", label: "Разделение дорожек", hint: "Выделяем партию гитары из микса" },
  { key: "transcribing", label: "Распознавание нот", hint: "Анализируем высоту и длительность по спектру" },
  { key: "arranging", label: "Подбор аппликатуры", hint: "Раскладываем ноты по грифу так, чтобы было реально сыграть" },
  { key: "exporting", label: "Сборка табулатуры", hint: "Формируем Guitar Pro файл и партитуру" },
];

function stageIndex(stage: JobStage): number {
  if (stage === "queued") return -1;
  if (stage === "done") return STAGES.length;
  if (stage === "error") return -2;
  return STAGES.findIndex((s) => s.key === stage);
}

interface Props {
  status: JobStatus;
}

export default function ProcessingTimeline({ status }: Props) {
  const current = stageIndex(status.stage);

  return (
    <div className="w-full rounded-2xl border border-walnut-700 bg-walnut-900/60 p-8">
      <div className="mb-8 text-center">
        <p className="text-lg font-medium text-cream">{status.message || "Обрабатываем файл…"}</p>
        <p className="mt-1 text-sm text-muted">Это может занять несколько минут — модели работают локально, без облака</p>
      </div>

      <ol className="space-y-5">
        {STAGES.map((stage, i) => {
          const done = current > i || status.stage === "done";
          const active = current === i && status.stage !== "error";

          return (
            <li key={stage.key} className="flex items-start gap-4">
              <div
                className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ring-1 transition-colors
                  ${done ? "bg-brass text-walnut-950 ring-brass" : ""}
                  ${active ? "bg-walnut-800 text-brass ring-brass animate-pulse" : ""}
                  ${!done && !active ? "bg-walnut-800 text-muted ring-walnut-600" : ""}`}
              >
                {done ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  i + 1
                )}
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <p className={`font-medium ${active ? "text-brass" : done ? "text-cream" : "text-muted"}`}>
                    {stage.label}
                  </p>
                  {active && (
                    <span className="text-xs text-muted">{Math.round(status.progress * 100)}%</span>
                  )}
                </div>
                <p className="mt-0.5 text-sm text-muted">{stage.hint}</p>
                {active && (
                  <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-walnut-800">
                    <div
                      className="h-full rounded-full bg-brass transition-all duration-300"
                      style={{ width: `${Math.max(4, status.progress * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {status.stage === "error" && (
        <div className="mt-6 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-300">
          <p className="font-medium">Не получилось обработать файл</p>
          <p className="mt-1 text-red-300/80">{status.error || "Неизвестная ошибка. Попробуй другой файл."}</p>
        </div>
      )}
    </div>
  );
}
