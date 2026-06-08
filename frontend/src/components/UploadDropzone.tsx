import { useCallback, useRef, useState } from "react";

const ACCEPTED_EXTENSIONS = [
  ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
  ".mp4", ".mov", ".mkv", ".webm", ".avi",
];

interface Props {
  onFileSelected: (file: File) => void;
}

function isAcceptable(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

export default function UploadDropzone({ onFileSelected }: Props) {
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const file = files[0];
      if (!isAcceptable(file)) {
        setRejected(`«${file.name}» — формат не поддерживается. Нужен аудио- или видеофайл.`);
        return;
      }
      setRejected(null);
      onFileSelected(file);
    },
    [onFileSelected],
  );

  return (
    <div className="w-full">
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={`group relative cursor-pointer rounded-2xl border-2 border-dashed transition-all duration-200
          ${dragging
            ? "border-brass bg-brass/10 scale-[1.01]"
            : "border-walnut-600 bg-walnut-900/60 hover:border-brass-dark hover:bg-walnut-900"}
          px-10 py-16 text-center`}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />

        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-walnut-800 ring-1 ring-walnut-600 group-hover:ring-brass-dark transition-colors">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="text-brass">
            <path d="M12 16V4M12 4l-4 4M12 4l4 4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>

        <p className="text-lg font-medium text-cream">
          Перетащи сюда трек или видео — или нажми, чтобы выбрать файл
        </p>
        <p className="mt-2 text-sm text-muted">
          MP3, WAV, FLAC, M4A · MP4, MOV, MKV — звук вытащим автоматически
        </p>
      </div>

      {rejected && (
        <p className="mt-3 text-sm text-red-400">{rejected}</p>
      )}
    </div>
  );
}
