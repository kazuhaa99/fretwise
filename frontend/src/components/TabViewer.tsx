import { useEffect, useMemo, useRef, useState } from "react";
import type { TabData } from "../types";
import { techniqueMark } from "./tabNotation";

const PIXELS_PER_SECOND = 90;
const ROW_HEIGHT = 30;
const LEFT_GUTTER = 56;
const TOP_PAD = 22;
const BOTTOM_PAD = 22;

function noteLetter(name: string): string {
  // "E2" -> "E", "Eb4" -> "E♭"
  const m = name.match(/^([A-G])(b|#)?(\d)$/);
  if (!m) return name;
  return m[2] === "b" ? `${m[1]}♭` : m[2] === "#" ? `${m[1]}♯` : m[1];
}

interface Props {
  tab: TabData;
  currentTime: number;
  onSeek: (seconds: number) => void;
}

export default function TabViewer({ tab, currentTime, onSeek }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stringCount = tab.tuning.length;

  // Most stems hold a single guitar part; `tracks` only grows past one entry
  // when auto-detection finds a second simultaneous performance worth its own
  // staff (see `separate_voices` on the backend). The parent keys this
  // component by job id, so a new result remounts it and the selection starts
  // over at the first track — no reset effect needed.
  const [trackIndex, setTrackIndex] = useState(0);
  const track = tab.tracks[Math.min(trackIndex, tab.tracks.length - 1)];

  const staffHeight = (stringCount - 1) * ROW_HEIGHT;
  const width = LEFT_GUTTER + Math.max(tab.durationSeconds, 1) * PIXELS_PER_SECOND + 80;
  const height = TOP_PAD + staffHeight + BOTTOM_PAD;

  const playheadX = LEFT_GUTTER + currentTime * PIXELS_PER_SECOND;

  // string index 0 = lowest (bottom line), last = highest (top line) — standard tab layout
  const yForString = (stringIndex: number) => TOP_PAD + (stringCount - 1 - stringIndex) * ROW_HEIGHT;

  // Auto-scroll to keep the playhead comfortably in view
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const target = playheadX - el.clientWidth * 0.35;
    if (Math.abs(el.scrollLeft - target) > el.clientWidth * 0.55 || target > el.scrollLeft + el.clientWidth - 120) {
      el.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
    }
  }, [Math.floor(currentTime)]); // eslint-disable-line react-hooks/exhaustive-deps

  const measureLines = useMemo(() => {
    // Light vertical guides every 2 seconds to help track position
    const lines: number[] = [];
    for (let t = 0; t <= tab.durationSeconds + 1; t += 2) lines.push(t);
    return lines;
  }, [tab.durationSeconds]);

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = e.currentTarget;
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left + (scrollRef.current?.scrollLeft ?? 0);
    const seconds = Math.max(0, (x - LEFT_GUTTER) / PIXELS_PER_SECOND);
    onSeek(Math.min(seconds, tab.durationSeconds));
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-walnut-700 bg-walnut-900/60">
      <div className="flex items-center justify-between border-b border-walnut-700 px-5 py-3">
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted">Строй:</span>
          <span className="font-mono text-cream">
            {[...tab.tuning].reverse().map(noteLetter).join(" ")}
          </span>
          {tab.capo > 0 && (
            <span className="rounded-full bg-brass/15 px-2 py-0.5 text-xs text-brass">капо {tab.capo}</span>
          )}
        </div>
        <span className="text-sm text-muted">{tab.tempoBpm} BPM</span>
      </div>

      {tab.tracks.length > 1 && (
        <div className="flex items-center gap-2 border-b border-walnut-700 bg-walnut-900/40 px-5 py-2.5">
          <span className="text-xs text-muted">Партии:</span>
          {tab.tracks.map((t, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setTrackIndex(i)}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                i === trackIndex ? "bg-brass/25 text-brass" : "text-muted hover:text-cream"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}

      <div ref={scrollRef} className="scrollbar-thin overflow-x-auto">
        <svg
          width={width}
          height={height}
          onClick={handleClick}
          className="block cursor-text bg-parchment"
          style={{ minWidth: "100%" }}
        >
          {/* time guides */}
          {measureLines.map((t) => (
            <line
              key={t}
              x1={LEFT_GUTTER + t * PIXELS_PER_SECOND}
              y1={TOP_PAD - 6}
              x2={LEFT_GUTTER + t * PIXELS_PER_SECOND}
              y2={TOP_PAD + staffHeight + 6}
              stroke="#cdbe9f"
              strokeWidth={1}
            />
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
          {track.events.map((event, ei) =>
            event.notes.map((note, ni) => {
              const x = LEFT_GUTTER + event.time * PIXELS_PER_SECOND;
              const y = yForString(note.string);
              const mark = techniqueMark(note);
              return (
                <g key={`${ei}-${ni}`}>
                  <rect x={x - 11} y={y - 11} width={22} height={22} rx={5} fill="var(--color-parchment)" stroke="#8a7a5c" strokeWidth={1} />
                  <text x={x} y={y + 5} textAnchor="middle" fontSize={13} fontFamily="var(--font-mono)" fontWeight={600} fill="#2a221b">
                    {note.fret}
                  </text>
                  {mark && (
                    <text x={x + 13} y={y - 8} fontSize={10} fontFamily="var(--font-mono)" fill="#a9722f">
                      {mark}
                    </text>
                  )}
                </g>
              );
            }),
          )}

          {/* playhead */}
          <line
            x1={playheadX}
            y1={TOP_PAD - 10}
            x2={playheadX}
            y2={TOP_PAD + staffHeight + 10}
            stroke="#c4452e"
            strokeWidth={2}
          />
          <polygon
            points={`${playheadX - 6},${TOP_PAD - 10} ${playheadX + 6},${TOP_PAD - 10} ${playheadX},${TOP_PAD - 1}`}
            fill="#c4452e"
          />
        </svg>
      </div>

      <p className="border-t border-walnut-700 px-5 py-2.5 text-xs text-muted">
        Клик по партитуре — переход воспроизведения на это место. Цифра на линии — номер лада, буква над ней — приём игры (h — хаммер-он, p — пул-офф, / — слайд, b½ / b1 — бенд на полтона / на тон, ~ — вибрато, PM — палм-мьют, tr — тремоло).
        {tab.tracks.length > 1 && " Несколько партий гитары обнаружены автоматически — переключайтесь между ними вкладками выше."}
      </p>
    </div>
  );
}
