import { useEffect, useId, useState, useSyncExternalStore } from "react";
import type { PlaybackState, Source } from "../lib/playback";
import { formatTime } from "../lib/format";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  CrosshairIcon,
  KeyboardIcon,
  LoopIcon,
  MetronomeIcon,
  MoreIcon,
  MuteIcon,
  PauseIcon,
  PlayIcon,
  TimerIcon,
  VolumeIcon,
} from "./icons";
import { IconButton, Popover, Segmented } from "./ui";

function subscribeMedia(query: string) {
  return (onChange: () => void) => {
    const mql = window.matchMedia?.(query);
    mql?.addEventListener?.("change", onChange);
    return () => mql?.removeEventListener?.("change", onChange);
  };
}

/** True from 640px up (and in environments without matchMedia, e.g. tests). */
function useWide(): boolean {
  const query = "(min-width: 640px)";
  return useSyncExternalStore(
    subscribeMedia(query),
    () => window.matchMedia?.(query).matches ?? true,
    () => true,
  );
}

export interface PlayerBarProps {
  subscribe: (listener: (state: PlaybackState) => void) => () => void;
  onToggle: () => void;
  onSeek: (seconds: number) => void;
  onPrevBar: () => void;
  onNextBar: () => void;
  currentBar: number;
  barCount: number;
  rate: number;
  onRateChange: (rate: number) => void;
  loopActive: boolean;
  loopLabel: string | null;
  onToggleLoop: () => void;
  countIn: boolean;
  onToggleCountIn: () => void;
  metronome: boolean;
  onToggleMetronome: () => void;
  follow: boolean;
  onToggleFollow: () => void;
  source: Source;
  onSourceChange: (source: Source) => void;
  volume: number;
  onVolumeChange: (volume: number) => void;
  onShowHelp: () => void;
}

const SPEED_PRESETS = [0.5, 0.75, 1];

function SpeedControl({ rate, onRateChange }: { rate: number; onRateChange: (r: number) => void }) {
  const id = useId();
  return (
    <Popover
      label="Скорость воспроизведения"
      trigger={({ open, toggle, id: panel }) => (
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={panel}
          aria-label={`Скорость ${Math.round(rate * 100)}%`}
          title="Скорость (+ / −)"
          className={`tabular inline-flex h-11 min-w-14 items-center justify-center rounded-md px-2.5 text-sm font-semibold transition-colors
            ${rate !== 1 ? "bg-accent-soft text-accent-strong" : "text-fg hover:bg-accent-soft hover:text-accent-strong"}`}
        >
          {Math.round(rate * 100)}%
        </button>
      )}
    >
      <div className="grid gap-3">
        <label htmlFor={id} className="flex items-baseline justify-between text-sm font-medium">
          <span>Скорость</span>
          <span className="tabular text-muted">{Math.round(rate * 100)}%</span>
        </label>
        <input
          id={id}
          type="range"
          min={25}
          max={150}
          step={5}
          value={Math.round(rate * 100)}
          onChange={(e) => onRateChange(Number(e.target.value) / 100)}
          className="h-11 w-full accent-[var(--accent-strong)]"
        />
        <div className="flex gap-1.5">
          {SPEED_PRESETS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => onRateChange(p)}
              aria-pressed={Math.abs(rate - p) < 0.001}
              className={`tabular min-h-9 flex-1 rounded-md border text-[13px] font-medium
                ${Math.abs(rate - p) < 0.001 ? "border-accent bg-accent-soft text-accent-strong" : "border-line text-fg hover:border-accent"}`}
            >
              {Math.round(p * 100)}%
            </button>
          ))}
        </div>
        <p className="text-[12px] text-muted">Высота звука не меняется.</p>
      </div>
    </Popover>
  );
}

function VolumeControl({ volume, onVolumeChange }: { volume: number; onVolumeChange: (v: number) => void }) {
  const id = useId();
  const [beforeMute, setBeforeMute] = useState(1);
  return (
    <Popover
      label="Громкость"
      align="end"
      trigger={({ open, toggle, id: panel }) => (
        <IconButton label={`Громкость ${Math.round(volume * 100)}%`} onClick={toggle} aria-expanded={open} aria-controls={panel}>
          {volume === 0 ? <MuteIcon /> : <VolumeIcon />}
        </IconButton>
      )}
    >
      <div className="grid gap-2">
        <label htmlFor={id} className="flex items-baseline justify-between text-sm font-medium">
          <span>Громкость</span>
          <span className="tabular text-muted">{Math.round(volume * 100)}%</span>
        </label>
        <div className="flex items-center gap-1">
          <IconButton
            label={volume === 0 ? "Включить звук" : "Выключить звук"}
            onClick={() => {
              if (volume === 0) onVolumeChange(beforeMute || 1);
              else {
                setBeforeMute(volume);
                onVolumeChange(0);
              }
            }}
          >
            {volume === 0 ? <MuteIcon /> : <VolumeIcon />}
          </IconButton>
          <input
            id={id}
            type="range"
            min={0}
            max={100}
            step={1}
            value={Math.round(volume * 100)}
            onChange={(e) => onVolumeChange(Number(e.target.value) / 100)}
            className="h-11 w-full accent-[var(--accent-strong)]"
          />
        </div>
      </div>
    </Popover>
  );
}

const SOURCE_OPTIONS: { value: Source; label: string }[] = [
  { value: "original", label: "Оригинал" },
  { value: "guitar", label: "Гитара" },
];

export default function PlayerBar(props: PlayerBarProps) {
  const { subscribe, onToggle, onSeek } = props;
  const wide = useWide();
  const [state, setState] = useState<PlaybackState>({ time: 0, duration: 0, playing: false, counting: false });

  // Re-render at most ~10×/s for the clock text; the tab cursor has its own
  // per-frame subscription.
  useEffect(
    () =>
      subscribe((next) =>
        setState((prev) =>
          Math.abs(prev.time - next.time) < 0.1 &&
          prev.playing === next.playing &&
          prev.counting === next.counting &&
          prev.duration === next.duration
            ? prev
            : next,
        ),
      ),
    [subscribe],
  );

  const busy = state.playing || state.counting;
  const playLabel = state.counting ? "Остановить отсчёт" : state.playing ? "Пауза" : "Играть";
  const position = (
    <span className="tabular text-[13px] whitespace-nowrap text-muted">
      {formatTime(state.time)} / {formatTime(state.duration)} · такт {props.currentBar}
      {state.counting && <span className="ml-1 font-semibold text-accent-strong">· отсчёт</span>}
    </span>
  );
  const scrubber = (
    <input
      type="range"
      aria-label="Позиция воспроизведения"
      min={0}
      max={state.duration || 0}
      step={0.01}
      value={Math.min(state.time, state.duration || 0)}
      onChange={(e) => onSeek(Number(e.target.value))}
      className="h-11 w-full min-w-24 accent-[var(--accent-strong)]"
    />
  );
  const loopButton = (
    <IconButton label={props.loopActive ? "Выключить петлю (L)" : "Петля (L)"} pressed={props.loopActive} onClick={props.onToggleLoop}>
      <LoopIcon />
      {props.loopActive && props.loopLabel && <span className="tabular">{props.loopLabel}</span>}
    </IconButton>
  );
  const toggles = (
    <>
      <IconButton label="Отсчёт перед стартом (C)" pressed={props.countIn} onClick={props.onToggleCountIn}>
        <TimerIcon />
      </IconButton>
      <IconButton label="Метроном (M)" pressed={props.metronome} onClick={props.onToggleMetronome}>
        <MetronomeIcon />
      </IconButton>
      <IconButton label="Следить за курсором" pressed={props.follow} onClick={props.onToggleFollow}>
        <CrosshairIcon />
      </IconButton>
    </>
  );
  const playButton = (big: boolean) => (
    <button
      type="button"
      onClick={onToggle}
      aria-label={playLabel}
      title={`${playLabel} (пробел)`}
      className={`grid shrink-0 place-items-center rounded-full bg-accent-strong text-on-accent transition-transform duration-150 hover:brightness-110 active:scale-95
        ${big ? "h-14 w-14" : "h-11 w-11"}`}
    >
      {busy ? <PauseIcon size={big ? 24 : 20} /> : <PlayIcon size={big ? 24 : 20} />}
    </button>
  );

  return (
    <div
      className="fixed inset-x-0 bottom-0 z-20"
      style={{
        paddingBottom: "max(12px, env(safe-area-inset-bottom, 0px))",
        paddingLeft: "max(12px, env(safe-area-inset-left, 0px))",
        paddingRight: "max(12px, env(safe-area-inset-right, 0px))",
      }}
    >
      <div role="region" aria-label="Плеер" className="mx-auto max-w-6xl rounded-lg border border-line bg-surface shadow-float">
        {wide ? (
          <div className="flex items-center gap-2 px-2 py-1.5">
            {playButton(false)}
            <div className="flex min-w-0 flex-1 items-center gap-3 pl-1">
              {position}
              {scrubber}
            </div>
            <span className="mx-1 h-7 w-px bg-line" aria-hidden="true" />
            <SpeedControl rate={props.rate} onRateChange={props.onRateChange} />
            {loopButton}
            {toggles}
            <span className="mx-1 h-7 w-px bg-line" aria-hidden="true" />
            <Segmented label="Источник звука" size="sm" value={props.source} onChange={props.onSourceChange} options={SOURCE_OPTIONS} />
            <VolumeControl volume={props.volume} onVolumeChange={props.onVolumeChange} />
            <IconButton label="Горячие клавиши (?)" onClick={props.onShowHelp}>
              <KeyboardIcon />
            </IconButton>
          </div>
        ) : (
          <div className="grid gap-1 px-3 pt-1 pb-2">
            {scrubber}
            <div className="flex items-center justify-between gap-2">
              {position}
              <div className="flex items-center">
                <SpeedControl rate={props.rate} onRateChange={props.onRateChange} />
                {loopButton}
              </div>
            </div>
            <div className="flex items-center justify-around">
              <IconButton label="Предыдущий такт" onClick={props.onPrevBar} disabled={props.currentBar <= 1}>
                <ChevronLeftIcon size={24} />
              </IconButton>
              {playButton(true)}
              <IconButton label="Следующий такт" onClick={props.onNextBar} disabled={props.currentBar >= props.barCount}>
                <ChevronRightIcon size={24} />
              </IconButton>
              <Popover
                label="Ещё настройки плеера"
                align="end"
                trigger={({ open, toggle, id }) => (
                  <IconButton label="Ещё" onClick={toggle} aria-expanded={open} aria-controls={id}>
                    <MoreIcon size={24} />
                  </IconButton>
                )}
              >
                <div className="grid gap-3">
                  <Segmented label="Источник звука" value={props.source} onChange={props.onSourceChange} options={SOURCE_OPTIONS} />
                  <div className="flex items-center justify-between">{toggles}</div>
                  <VolumeControl volume={props.volume} onVolumeChange={props.onVolumeChange} />
                </div>
              </Popover>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
