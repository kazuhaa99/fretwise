import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

export type TimeListener = (seconds: number, playing: boolean) => void;

export interface AudioPlayerHandle {
  seekTo: (seconds: number) => void;
  /**
   * Receive the playback position every animation frame while playing (and
   * once on every seek/pause). Returns an unsubscribe function.
   *
   * This is a subscription rather than React state on purpose: the tab
   * viewer's playhead must move 60 times a second, and pushing that through
   * `App` state would re-render the whole page — including an SVG with
   * thousands of notes — on every frame.
   */
  subscribe: (listener: TimeListener) => () => void;
}

export type AudioSource = "original" | "guitar";

interface Props {
  originalUrl: string;
  guitarUrl: string;
}

function formatTime(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

const AudioPlayer = forwardRef<AudioPlayerHandle, Props>(function AudioPlayer({ originalUrl, guitarUrl }, ref) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const listenersRef = useRef(new Set<TimeListener>());
  const rafRef = useRef<number | null>(null);
  const [source, setSource] = useState<AudioSource>("guitar");
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const notify = useCallback((seconds: number, isPlaying: boolean) => {
    setTime(seconds);
    for (const listener of listenersRef.current) listener(seconds, isPlaying);
  }, []);

  const stopLoop = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }, []);

  // While playing, sample `currentTime` every frame. `timeupdate` alone fires
  // only ~4 times a second, which is what made the playhead stutter.
  const startLoop = useCallback(() => {
    stopLoop();
    const tick = () => {
      const el = audioRef.current;
      if (!el) return;
      notify(el.currentTime, true);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [notify, stopLoop]);

  useEffect(() => stopLoop, [stopLoop]);

  useImperativeHandle(
    ref,
    () => ({
      seekTo(seconds: number) {
        const el = audioRef.current;
        if (!el) return;
        el.currentTime = seconds;
        notify(seconds, !el.paused);
      },
      subscribe(listener: TimeListener) {
        listenersRef.current.add(listener);
        return () => {
          listenersRef.current.delete(listener);
        };
      },
    }),
    [notify],
  );

  // The element's `src` is owned by this effect alone (it used to be set both
  // here and as a JSX attribute, so every switch loaded the file twice). On a
  // switch we carry the position and play state over: the new file must load
  // its metadata before `currentTime` can be set, so that happens on
  // `loadedmetadata`, not immediately.
  const resumeRef = useRef<{ at: number; play: boolean } | null>(null);
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    const url = source === "guitar" ? guitarUrl : originalUrl;
    if (el.getAttribute("src") === url) return;
    if (el.getAttribute("src")) resumeRef.current = { at: el.currentTime, play: !el.paused };
    el.src = url;
    el.load();
  }, [source, guitarUrl, originalUrl]);

  const handleLoadedMetadata = (e: React.SyntheticEvent<HTMLAudioElement>) => {
    const el = e.currentTarget;
    setDuration(el.duration);
    const resume = resumeRef.current;
    resumeRef.current = null;
    if (resume) {
      el.currentTime = Math.min(resume.at, Number.isFinite(el.duration) ? el.duration : resume.at);
      notify(el.currentTime, resume.play);
      if (resume.play) el.play().catch(() => {});
    }
  };

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => {});
    else el.pause();
  };

  return (
    <div className="rounded-2xl border border-walnut-700 bg-walnut-900/60 p-5">
      <audio
        ref={audioRef}
        preload="metadata"
        data-testid="audio"
        onPlay={() => {
          setPlaying(true);
          startLoop();
        }}
        onPause={(e) => {
          setPlaying(false);
          stopLoop();
          notify(e.currentTarget.currentTime, false);
        }}
        onEnded={(e) => notify(e.currentTarget.currentTime, false)}
        onSeeked={(e) => notify(e.currentTarget.currentTime, !e.currentTarget.paused)}
        onTimeUpdate={(e) => {
          // Covers the paused case (e.g. scrubbing); while playing the
          // animation loop already reports every frame.
          if (e.currentTarget.paused) notify(e.currentTarget.currentTime, false);
        }}
        onLoadedMetadata={handleLoadedMetadata}
      />

      <div className="flex flex-wrap items-center gap-4">
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

        <div className="min-w-40 flex-1">
          <input
            type="range"
            aria-label="Позиция воспроизведения"
            min={0}
            max={duration || 0}
            step={0.01}
            value={Math.min(time, duration || 0)}
            onChange={(e) => {
              const t = Number(e.target.value);
              const el = audioRef.current;
              if (el) el.currentTime = t;
              notify(t, el ? !el.paused : false);
            }}
            className="w-full accent-brass"
          />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{formatTime(time)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>

        <div className="flex shrink-0 overflow-hidden rounded-lg ring-1 ring-walnut-600" role="group" aria-label="Источник звука">
          <button
            onClick={() => setSource("guitar")}
            aria-pressed={source === "guitar"}
            className={`px-3 py-2 text-xs font-medium transition-colors ${source === "guitar" ? "bg-brass text-walnut-950" : "bg-walnut-800 text-muted hover:text-cream"}`}
          >
            Только гитара
          </button>
          <button
            onClick={() => setSource("original")}
            aria-pressed={source === "original"}
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
