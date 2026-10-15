import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import type { TabSettings } from "../types";
import { formatSize, formatTime } from "../lib/format";
import SettingsPanel from "./SettingsPanel";
import { MusicFileIcon, UploadIcon, VideoFileIcon } from "./icons";
import { Alert, Button } from "./ui";

const AUDIO_EXTENSIONS = [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"];
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv", ".webm", ".avi"];
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024;

const ACCEPT = [...AUDIO_EXTENSIONS, ...VIDEO_EXTENSIONS, "audio/*", "video/*"].join(",");

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/** Client-side check before uploading: same formats and limit as the server,
 * so the user learns about a problem in a second, not after the upload. */
function checkFile(file: File): string | null {
  const ext = extensionOf(file.name);
  if (![...AUDIO_EXTENSIONS, ...VIDEO_EXTENSIONS].includes(ext)) {
    return `«${file.name}»: формат ${ext || "без расширения"} не подходит. Нужен аудио- или видеофайл из списка ниже.`;
  }
  if (file.size === 0) return `«${file.name}» пустой.`;
  if (file.size > MAX_UPLOAD_BYTES) return `«${file.name}» весит ${formatSize(file.size)} — больше лимита в 500 МБ.`;
  return null;
}

function isVideo(file: File): boolean {
  return VIDEO_EXTENSIONS.includes(extensionOf(file.name)) || file.type.startsWith("video/");
}

function FilePreview({ file, onRemove }: { file: File; onRemove: () => void }) {
  const [duration, setDuration] = useState<number | null>(null);
  const video = isVideo(file);
  // A local preview of the chosen file; revoked when the file changes.
  const url = useMemo(() => (typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-lg border border-line bg-surface p-3 sm:p-4">
      <div className="flex items-center gap-3">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-md bg-accent-soft text-accent-strong">
          {video ? <VideoFileIcon /> : <MusicFileIcon />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold" title={file.name}>
            {file.name}
          </p>
          <p className="tabular text-[13px] text-muted">
            {video ? "видео" : "аудио"} · {formatSize(file.size)}
            {duration !== null && ` · ${formatTime(duration)}`}
            {video && " · звук извлечём сами"}
          </p>
        </div>
        <Button variant="ghost" onClick={onRemove}>
          Убрать
        </Button>
      </div>
      {url &&
        (video ? (
          <video
            src={url}
            controls
            muted
            playsInline
            preload="metadata"
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
            className="max-h-56 w-full rounded-md bg-bg"
          />
        ) : (
          <audio src={url} controls preload="metadata" onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)} className="w-full" />
        ))}
    </div>
  );
}

interface Props {
  file: File | null;
  onFileChange: (file: File | null) => void;
  settings: TabSettings;
  onSettingsChange: (settings: TabSettings) => void;
  onSubmit: () => void;
  submitting: boolean;
  /** Server or restore error shown above the form. */
  error: string | null;
}

export default function UploadScreen({ file, onFileChange, settings, onSettingsChange, onSubmit, submitting, error }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [rejection, setRejection] = useState<string | null>(null);
  const [settingsValid, setSettingsValid] = useState(true);
  const hintId = useId();

  const take = useCallback(
    (candidate: File | undefined | null) => {
      if (!candidate) return;
      const problem = checkFile(candidate);
      setRejection(problem);
      if (!problem) onFileChange(candidate);
    },
    [onFileChange],
  );

  // Ctrl/Cmd+V anywhere on the page: take a pasted file.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const pasted = e.clipboardData?.files?.[0];
      if (pasted) {
        e.preventDefault();
        take(pasted);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [take]);

  let notice: ReactNode = null;
  if (error) notice = <Alert title="Не получилось">{error}</Alert>;

  return (
    <div className="mx-auto grid max-w-3xl grid-cols-[minmax(0,1fr)] gap-6">
      <div className="grid gap-2">
        <h1 className="text-[28px] leading-tight font-bold sm:text-[34px]">Табулатура из любой записи</h1>
        <p className="max-w-[60ch] text-muted">
          Загрузи трек или видео. Fretwise выделит гитару, распознает ноты и разложит их так, чтобы это можно было
          сыграть руками. Всё считается на этом компьютере.
        </p>
      </div>

      {notice}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          take(e.dataTransfer.files?.[0]);
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) inputRef.current?.click();
        }}
        className={`grid cursor-pointer justify-items-center gap-3 rounded-lg border-2 border-dashed px-4 py-10 text-center transition-colors duration-150 sm:py-14
          ${dragging ? "border-accent bg-accent-soft" : rejection ? "border-danger bg-surface" : "border-line-strong bg-surface hover:border-accent"}`}
        data-testid="dropzone"
      >
        <span className="pointer-events-none grid h-14 w-14 place-items-center rounded-full bg-accent-soft text-accent-strong">
          <UploadIcon size={28} />
        </span>
        <p className="pointer-events-none text-lg font-semibold">
          {dragging ? "Отпусти, чтобы загрузить" : "Перетащи файл сюда, вставь его или выбери"}
        </p>
        <p id={hintId} className="pointer-events-none max-w-[52ch] text-[13px] text-muted">
          MP3, WAV, FLAC, M4A, AAC, OGG · MP4, MOV, MKV, WEBM, AVI · до 500 МБ
        </p>
        <Button variant="secondary" onClick={() => inputRef.current?.click()} aria-describedby={hintId}>
          Выбрать файл
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            take(e.target.files?.[0]);
            e.target.value = "";
          }}
          data-testid="file-input"
        />
        {rejection && (
          <p role="alert" className="text-[15px] text-danger">
            {rejection}
          </p>
        )}
      </div>

      {file && <FilePreview key={`${file.name}:${file.size}:${file.lastModified}`} file={file} onRemove={() => onFileChange(null)} />}

      <SettingsPanel settings={settings} onChange={onSettingsChange} onValidityChange={setSettingsValid} />

      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" onClick={onSubmit} disabled={!file || !settingsValid} loading={submitting}>
          {submitting ? "Загружаем файл…" : "Построить табы"}
        </Button>
        {!file && <span className="text-[13px] text-muted">Сначала выбери файл</span>}
        {file && !settingsValid && <span className="text-[13px] text-danger">Исправь строй в настройках</span>}
      </div>
    </div>
  );
}
