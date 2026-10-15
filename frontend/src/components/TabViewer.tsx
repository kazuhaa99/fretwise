import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TabData } from "../types";
import type { TimeListener } from "./AudioPlayer";
import { techniqueMark } from "./tabNotation";
import { DEFAULT_AXIS_OPTIONS, buildTimeAxis, followScroll, timeToX, xToTime } from "./tabLayout";

const ROW_HEIGHT = 30;
const LEFT_GUTTER = DEFAULT_AXIS_OPTIONS.left;
const TOP_PAD = 34; // room for bar numbers and technique marks above the top string
const BOTTOM_PAD = 22;
const RIGHT_PAD = 80;

const ZOOM_LEVELS = [0.5, 0.75, 1, 1.5, 2, 3, 4];
const DEFAULT_ZOOM_INDEX = 2;

// After the user scrolls the tab themselves, auto-follow backs off for this
// long instead of yanking the view straight back to the playhead.
const MANUAL_SCROLL_GRACE_MS = 2500;

function noteLetter(name: string): string {
  // "E2" -> "E", "Eb4" -> "E♭"
  const m = name.match(/^([A-G])(b|#)?(-?\d)$/);
  if (!m) return name;
  return m[2] === "b" ? `${m[1]}♭` : m[2] === "#" ? `${m[1]}♯` : m[1];
}

interface Props {
  tab: TabData;
  /** Playback clock (see `AudioPlayerHandle.subscribe`). */
  subscribe?: (listener: TimeListener) => () => void;
  onSeek: (seconds: number) => void;
}

export default function TabViewer({ tab, subscribe, onSeek }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<SVGGElement>(null);
  const lastTimeRef = useRef({ seconds: 0, playing: false });
  const manualScrollUntilRef = useRef(0);
  const stringCount = tab.tuning.length;

  // Most stems hold a single guitar part; `tracks` only grows past one entry
  // when auto-detection finds a second simultaneous performance worth its own
  // staff (see `separate_voices` on the backend). The parent keys this
  // component by job id, so a new result remounts it and the selection starts
  // over at the first track — no reset effect needed.
  const [trackIndex, setTrackIndex] = useState(0);
  const [zoomIndex, setZoomIndex] = useState(DEFAULT_ZOOM_INDEX);
  const track = tab.tracks[Math.min(trackIndex, tab.tracks.length - 1)];
  const zoom = ZOOM_LEVELS[zoomIndex];

  const axis = useMemo(
    () =>
      buildTimeAxis(
        track.events.map((event) => event.time),
        tab.measureTimes ?? [],
        { ...DEFAULT_AXIS_OPTIONS, pixelsPerSecond: DEFAULT_AXIS_OPTIONS.pixelsPerSecond * zoom },
      ),
    [track, tab.measureTimes, zoom],
  );

  const staffHeight = (stringCount - 1) * ROW_HEIGHT;
  const contentEnd = Math.max(
    timeToX(axis, Math.max(tab.durationSeconds, 1)),
    ...axis.eventX,
    ...axis.measureX,
  );
  const width = contentEnd + RIGHT_PAD;
  const height = TOP_PAD + staffHeight + BOTTOM_PAD;

  // string index 0 = lowest (bottom line), last = highest (top line) — standard tab layout
  const yForString = (stringIndex: number) => TOP_PAD + (stringCount - 1 - stringIndex) * ROW_HEIGHT;

  const movePlayhead = useCallback(
    (seconds: number, playing: boolean) => {
      lastTimeRef.current = { seconds, playing };
      const x = timeToX(axis, seconds);
      playheadRef.current?.setAttribute("transform", `translate(${x} 0)`);

      const el = scrollRef.current;
      if (!el || performance.now() < manualScrollUntilRef.current) return;
      const target = followScroll(x, el.scrollLeft, el.clientWidth, playing);
      if (target !== null) el.scrollLeft = target;
    },
    [axis],
  );

  // Follow the playback clock; re-place the playhead whenever the axis
  // changes (zoom, track switch) so it never points at a stale position.
  useEffect(() => {
    movePlayhead(lastTimeRef.current.seconds, lastTimeRef.current.playing);
    return subscribe?.(movePlayhead);
  }, [subscribe, movePlayhead]);

  const noteManualScroll = () => {
    manualScrollUntilRef.current = performance.now() + MANUAL_SCROLL_GRACE_MS;
  };

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const seconds = Math.max(0, Math.min(xToTime(axis, x), tab.durationSeconds));
    manualScrollUntilRef.current = 0; // a deliberate seek should be followed
    onSeek(seconds);
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-walnut-700 bg-walnut-900/60">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-walnut-700 px-5 py-3">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted">Строй:</span>
          <span className="font-mono text-cream">
            {[...tab.tuning].reverse().map(noteLetter).join(" ")}
          </span>
          {tab.capo > 0 && (
            <span className="rounded-full bg-brass/15 px-2 py-0.5 text-xs text-brass">капо {tab.capo}</span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm text-muted">
          <span>
            {tab.tempoBpm} BPM · {tab.beatsPerMeasure ?? 4}/4
          </span>
          <div className="flex items-center overflow-hidden rounded-lg ring-1 ring-walnut-600" role="group" aria-label="Масштаб">
            <button
              type="button"
              onClick={() => setZoomIndex((i) => Math.max(0, i - 1))}
              disabled={zoomIndex === 0}
              aria-label="Уменьшить масштаб"
              className="bg-walnut-800 px-2.5 py-1 text-cream transition-colors hover:text-brass disabled:opacity-40"
            >
              −
            </button>
            <span className="min-w-12 bg-walnut-900 px-2 py-1 text-center text-xs tabular-nums" aria-live="polite">
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              onClick={() => setZoomIndex((i) => Math.min(ZOOM_LEVELS.length - 1, i + 1))}
              disabled={zoomIndex === ZOOM_LEVELS.length - 1}
              aria-label="Увеличить масштаб"
              className="bg-walnut-800 px-2.5 py-1 text-cream transition-colors hover:text-brass disabled:opacity-40"
            >
              +
            </button>
          </div>
        </div>
      </div>

      {tab.tracks.length > 1 && (
        <div className="flex items-center gap-2 border-b border-walnut-700 bg-walnut-900/40 px-5 py-2.5">
          <span className="text-xs text-muted">Партии:</span>
          {tab.tracks.map((t, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setTrackIndex(i)}
              aria-pressed={i === trackIndex}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                i === trackIndex ? "bg-brass/25 text-brass" : "text-muted hover:text-cream"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}

      <div
        ref={scrollRef}
        className="scrollbar-thin overflow-x-auto"
        onWheel={noteManualScroll}
        onPointerDown={noteManualScroll}
        onTouchStart={noteManualScroll}
      >
        <svg
          width={width}
          height={height}
          onClick={handleClick}
          className="block cursor-text bg-parchment"
          style={{ minWidth: "100%" }}
          role="img"
          aria-label="Табулатура"
        >
          {/* bar lines and bar numbers */}
          {axis.measureX.map((x, i) => (
            <g key={`bar-${i}`}>
              <line x1={x} y1={TOP_PAD} x2={x} y2={TOP_PAD + staffHeight} stroke="#5b4a36" strokeWidth={1.4} />
              <text x={x + 3} y={TOP_PAD - 20} fontSize={10} fontFamily="var(--font-mono)" fill="#8a7a5c" className="select-none">
                {i + 1}
              </text>
            </g>
          ))}

          {/* string lines */}
          {Array.from({ length: stringCount }, (_, i) => (
            <line
              key={i}
              x1={LEFT_GUTTER}
              y1={yForString(i)}
              x2={width - 24}
              y2={yForString(i)}
              stroke="#8a7a5c"
              strokeWidth={1.2}
            />
          ))}

          {/* string labels */}
          {tab.tuning.map((name, i) => (
            <text
              key={i}
              x={LEFT_GUTTER - 18}
              y={yForString(i) + 5}
              textAnchor="middle"
              className="select-none"
              fill="#5b4a36"
              fontSize={14}
              fontFamily="var(--font-mono)"
            >
              {noteLetter(name)}
            </text>
          ))}

          {/* notes */}
          {track.events.map((event, ei) => {
            const x = axis.eventX[ei];
            return event.notes.map((note, ni) => {
              const y = yForString(note.string);
              const mark = techniqueMark(note);
              return (
                <g key={`${ei}-${ni}`}>
                  <rect x={x - 11} y={y - 11} width={22} height={22} rx={5} fill="var(--color-parchment)" stroke="#8a7a5c" strokeWidth={1} />
                  <text x={x} y={y + 5} textAnchor="middle" fontSize={13} fontFamily="var(--font-mono)" fontWeight={600} fill="#2a221b">
                    {note.fret}
                  </text>
                  {mark && (
                    // Centered above the note so it never runs into the next column.
                    <text x={x} y={y - 14} textAnchor="middle" fontSize={10} fontFamily="var(--font-mono)" fill="#a9722f">
                      {mark}
                    </text>
                  )}
                </g>
              );
            });
          })}

          {/* playhead — positioned imperatively by `movePlayhead` every frame */}
          <g ref={playheadRef} transform={`translate(${LEFT_GUTTER} 0)`} pointerEvents="none" data-testid="playhead">
            <line x1={0} y1={TOP_PAD - 10} x2={0} y2={TOP_PAD + staffHeight + 10} stroke="#c4452e" strokeWidth={2} />
            <polygon points={`-6,${TOP_PAD - 10} 6,${TOP_PAD - 10} 0,${TOP_PAD - 1}`} fill="#c4452e" />
          </g>
        </svg>
      </div>

      <p className="border-t border-walnut-700 px-5 py-2.5 text-xs text-muted">
        Клик по партитуре — переход воспроизведения на это место. Плотные пассажи раздвигаются, чтобы цифры не налезали друг на друга; масштаб меняется кнопками «−» и «+». Цифра на линии — номер лада, знак над ней — приём игры (h — хаммер-он, p — пул-офф, / — слайд, b½ / b1 — бенд на полтона / на тон, ~ — вибрато, PM — палм-мьют, tr — тремоло).
        {tab.tracks.length > 1 && " Несколько партий гитары обнаружены автоматически — переключайтесь между ними вкладками выше."}
      </p>
    </div>
  );
}
