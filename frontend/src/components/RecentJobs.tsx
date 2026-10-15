import { useEffect, useState } from "react";
import type { JobStage, JobSummary } from "../types";
import { deleteJob, errorMessage, listJobs } from "../api/client";
import { formatTime, formatWhen } from "../lib/format";
import { TrashIcon } from "./icons";
import { Button, IconButton, Spinner } from "./ui";

const STAGE_BADGE: Partial<Record<JobStage, { label: string; className: string }>> = {
  done: { label: "готово", className: "bg-accent-soft text-accent-strong" },
  error: { label: "ошибка", className: "bg-danger-soft text-danger" },
  cancelled: { label: "отменено", className: "border border-line text-muted" },
};

function badge(stage: JobStage) {
  return STAGE_BADGE[stage] ?? { label: "в работе", className: "bg-accent-soft text-accent-strong" };
}

interface Props {
  onOpen: (jobId: string) => void;
}

/**
 * The job history on the upload screen: what was transcribed before (it
 * survives server restarts), with one click back into any result.
 */
export default function RecentJobs({ onOpen }: Props) {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listJobs()
      .then((list) => !cancelled && setJobs(list))
      .catch((e) => !cancelled && setError(errorMessage(e, "Не удалось загрузить историю")));
    return () => {
      cancelled = true;
    };
  }, []);

  const remove = async (id: string) => {
    setDeleting(id);
    try {
      await deleteJob(id);
      setJobs((list) => list?.filter((j) => j.id !== id) ?? null);
    } catch (e) {
      setError(errorMessage(e, "Не удалось удалить задачу"));
    } finally {
      setDeleting(null);
      setConfirming(null);
    }
  };

  if (error) return <p className="text-[13px] text-muted">{error}</p>;
  if (jobs === null) return null;

  return (
    <section aria-labelledby="recent-title" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <h2 id="recent-title" className="text-lg font-semibold">
        Недавние
      </h2>
      {jobs.length === 0 ? (
        <p className="text-[15px] text-muted">Здесь появятся разобранные треки — к ним можно будет вернуться в любой момент.</p>
      ) : (
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">
          {jobs.map((job) => {
            const b = badge(job.stage);
            const running = !["done", "error", "cancelled"].includes(job.stage);
            return (
              <li key={job.id} className="flex items-center gap-2 rounded-lg border border-line bg-surface pr-2">
                <button
                  type="button"
                  onClick={() => onOpen(job.id)}
                  className="flex min-h-14 min-w-0 flex-1 items-center gap-3 rounded-lg px-4 py-2 text-left hover:bg-accent-soft/50"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold" title={job.title}>
                      {job.title}
                    </span>
                    <span className="tabular block truncate text-[13px] text-muted">
                      {formatWhen(job.createdAt)}
                      {job.durationSeconds ? ` · ${formatTime(job.durationSeconds)}` : ""}
                      {job.stage === "error" && job.error ? ` · ${job.error.split("\n")[0]}` : ""}
                    </span>
                  </span>
                  {job.edited && (
                    <span className="hidden rounded-full border border-warm px-2 py-0.5 text-[12px] font-semibold text-warm sm:inline">изменено</span>
                  )}
                  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-semibold ${b.className}`}>
                    {running && <Spinner size={10} />}
                    {b.label}
                  </span>
                </button>
                {confirming === job.id ? (
                  <span className="flex items-center gap-1">
                    <Button variant="danger" loading={deleting === job.id} onClick={() => remove(job.id)}>
                      Удалить
                    </Button>
                    <Button variant="ghost" onClick={() => setConfirming(null)}>
                      Нет
                    </Button>
                  </span>
                ) : (
                  <IconButton
                    label={`Удалить «${job.title}»`}
                    onClick={() => setConfirming(job.id)}
                    disabled={running}
                    className="text-muted hover:bg-danger-soft hover:text-danger"
                  >
                    <TrashIcon />
                  </IconButton>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
