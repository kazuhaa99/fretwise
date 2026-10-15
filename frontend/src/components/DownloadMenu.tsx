import type { JobResult } from "../types";
import { gp5DownloadUrl } from "../api/client";
import { ChevronDownIcon, DownloadIcon } from "./icons";
import { Popover } from "./ui";

/** Every export reflects the current tab, edits included (built on request). */
export default function DownloadMenu({ result }: { result: JobResult }) {
  const formats = [
    { href: result.gp5Url || gp5DownloadUrl(result.job.id), label: "Guitar Pro (.gp5)", hint: "Guitar Pro, TuxGuitar, MuseScore" },
    { href: result.txtUrl || `/api/jobs/${result.job.id}/export.txt`, label: "Текстовые табы (.txt)", hint: "Для форумов, заметок и печати" },
    { href: result.midiUrl || `/api/jobs/${result.job.id}/export.mid`, label: "MIDI (.mid)", hint: "Для DAW и нотных редакторов" },
  ];
  return (
    <Popover
      label="Скачать табулатуру"
      placement="bottom"
      align="end"
      trigger={({ open, toggle, id }) => (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={id}
          aria-haspopup="true"
          className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line-strong bg-surface px-4 text-[15px] font-semibold text-fg transition-colors hover:border-accent hover:text-accent-strong"
        >
          <DownloadIcon /> Скачать <ChevronDownIcon size={16} className={open ? "rotate-180" : ""} />
        </button>
      )}
    >
      {(close) => (
        <ul className="-m-1 grid min-w-64 gap-0.5">
          {formats.map((f) => (
            <li key={f.label}>
              <a
                href={f.href}
                download
                onClick={close}
                className="grid min-h-11 rounded-md px-3 py-2 text-fg hover:bg-accent-soft hover:text-accent-strong"
              >
                <span className="text-[15px] font-semibold">{f.label}</span>
                <span className="text-[13px] text-muted">{f.hint}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}
