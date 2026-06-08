import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

export interface AudioPlayerHandle {
  seekTo: (seconds: number) => void;
}

interface Props {
  originalUrl: string;
  guitarUrl: string;
  onTimeUpdate: (seconds: number) => void;
  onDuration: (seconds: number) => void;
}

function formatTime(s: number): string {
  if (!Number.isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

const AudioPlayer = forwardRef<AudioPlayerHandle, Props>(function AudioPlayer(
  { originalUrl, guitarUrl, onTimeUpdate, onDuration },
  ref,
) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [source, setSource] = useState<"original" | "guitar">("guitar");
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useImperativeHandle(ref, () => ({
    seekTo(seconds: number) {
      const el = audioRef.current;
      if (!el) return;
      el.currentTime = seconds;
      setTime(seconds);
      onTimeUpdate(seconds);
    },
  }));

  // Preserve playback position & state across source switches
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    const wasPlaying = playing;
    const at = time;
    el.src = source === "guitar" ? guitarUrl : originalUrl;
    el.currentTime = at;
    if (wasPlaying) el.play().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
    } else {
      el.play().catch(() => {});
    }
  };

  return (
    <div className="rounded-2xl border border-walnut-700 bg-walnut-900/60 p-5">
      <audio
        ref={audioRef}
        src={source === "guitar" ? guitarUrl : originalUrl}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => {
          const t = e.currentTarget.currentTime;
          setTime(t);
          onTimeUpdate(t);
        }}
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          setDuration(d);
          onDuration(d);
        }}
      />

      <div className="flex items-center gap-4">
        <button
          onClick={togglePlay}
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brass text-walnut-950 transition-transform hover:scale-105 active:scale-95"
          aria-label={playing ? "Пауза" : "Воспроизвести"}
        >
          {playing ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
          )}
        </button>

        <div className="flex-1">
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.01}
            value={time}
            onChange={(e) => {
              const t = Number(e.target.value);
              if (audioRef.current) audioRef.current.currentTime = t;
              setTime(t);
              onTimeUpdate(t);
            }}
            className="w-full accent-brass"
          />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{formatTime(time)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>

        <div className="flex shrink-0 overflow-hidden rounded-lg ring-1 ring-walnut-600">
          <button
            onClick={() => setSource("guitar")}
            className={`px-3 py-2 text-xs font-medium transition-colors ${source === "guitar" ? "bg-brass text-walnut-950" : "bg-walnut-800 text-muted hover:text-cream"}`}
          >
            Только гитара
          </button>
          <button
            onClick={() => setSource("original")}
            className={`px-3 py-2 text-xs font-medium transition-colors ${source === "original" ? "bg-brass text-walnut-950" : "bg-walnut-800 text-muted hover:text-cream"}`}
          >
            Оригинал
          </button>
        </div>
      </div>
    </div>
  );
});

export default AudioPlayer;
