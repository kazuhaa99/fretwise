import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { TabData, TabEvent, TabNote } from "../types";
import type { PlaybackState } from "../lib/playback";
import { DESKTOP_SHEET, PHONE_SHEET, barSignatures, buildSheet, locate, type SheetLayout, type SheetRow } from "../lib/barLayout";
import { detectSections } from "../lib/sections";
import { prefersReducedMotion } from "../theme";
import { bendArrowLabel } from "./tabNotation";

/** Inclusive range of bar indices. */
export interface BarRange {
  start: number;
  end: number;
}

const TOP_BAND = 40; // bar numbers, section labels, bend labels, vibrato
const STRING_GAP = 18;
const BOTTOM_BAND = 26; // P.M. lines
const ROW_GAP = 6;
const PHONE_BREAKPOINT = 560;

interface Props {
  tab: TabData;
  trackIndex: number;
  subscribe: (listener: (state: PlaybackState) => void) => () => void;
  onSeek: (seconds: number) => void;
  selection: BarRange | null;
  loopActive: boolean;
  onSelect: (range: BarRange | null) => void;
  follow: boolean;
  onCurrentBarChange: (bar: number) => void;
  /** Bottom space covered by the floating player (for auto-scroll). */
  bottomInset: number;
}

interface NoteRef {
  event: TabEvent;
  note: TabNote;
  x: number;
}

function stringY(stringIndex: number, stringCount: number): number {
  return TOP_BAND + (stringCount - 1 - stringIndex) * STRING_GAP;
}

/** One row of bars. Memoized: only rows whose highlight/selection changes re-render. */
const Row = memo(function Row({
  row,
  layout,
  events,
  sections,
  stringCount,
  currentBar,
  selection,
  loopActive,
  onBarPointerDown,
  onBarPointerEnter,
  onBarClick,
  onNoteClick,
}: {
  row: SheetRow;
  layout: SheetLayout;
  events: TabEvent[];
  sections: (string | null)[];
  stringCount: number;
  currentBar: number | null;
  selection: BarRange | null;
  loopActive: boolean;
  onBarPointerDown: (bar: number, e: React.PointerEvent) => void;
  onBarPointerEnter: (bar: number) => void;
  onBarClick: (bar: number, e: React.MouseEvent) => void;
  onNoteClick: (time: number) => void;
}) {
  const staffTop = TOP_BAND;
  const staffBottom = stringY(0, stringCount);
  const height = staffBottom + BOTTOM_BAND;
  const width = row.width;
  const isFirstRow = row.index === 0;

  // Notes in this row, for technique connectors (previous note on a string).
  const placed: NoteRef[] = [];
  for (const bar of row.bars) {
    for (const e of bar.events) {
      for (const note of events[e.index].notes) placed.push({ event: events[e.index], note, x: bar.x + e.x });
    }
  }
  const prevOnString = new Map<number, NoteRef>();
  const connectors: React.ReactNode[] = [];
  const marks: React.ReactNode[] = [];
  const palmRuns: { from: number; to: number }[] = [];

  placed.forEach((ref, i) => {
    const { note, x } = ref;
    const y = stringY(note.string, stringCount);
    const prev = prevOnString.get(note.string);
    const t = note.technique;
    if ((t === "hammer_on" || t === "pull_off") && prev) {
      const mid = (prev.x + x) / 2;
      connectors.push(
        <g key={`c${i}`}>
          <path d={`M${prev.x + 4} ${y - 10} Q ${mid} ${y - 22} ${x - 4} ${y - 10}`} fill="none" stroke="var(--text-muted)" strokeWidth={1.3} />
          <text x={mid} y={y - 20} textAnchor="middle" fontSize={10} fill="var(--text-muted)" fontFamily="var(--font-sans)">
            {t === "hammer_on" ? "H" : "P"}
          </text>
        </g>,
      );
    } else if (t === "slide" && prev) {
      const up = note.fret > prev.note.fret;
      connectors.push(
        <g key={`c${i}`}>
          <line x1={prev.x + 11} y1={up ? y + 4 : y - 4} x2={x - 11} y2={up ? y - 4 : y + 4} stroke="var(--text-muted)" strokeWidth={1.3} />
          <text x={(prev.x + x) / 2} y={y - 9} textAnchor="middle" fontSize={10} fill="var(--text-muted)" fontFamily="var(--font-sans)">
            sl.
          </text>
        </g>,
      );
    }
    if (t === "bend") {
      const label = bendArrowLabel(note.bendSemitones ?? 2);
      const topY = 12;
      marks.push(
        <g key={`b${i}`} stroke="var(--warm)" fill="none" strokeWidth={1.5} strokeLinecap="round">
          <path d={`M${x + 10} ${y - 2} Q ${x + 24} ${y - 2} ${x + 24} ${topY + 6}`} />
          <path d={`M${x + 20} ${topY + 11} L${x + 24} ${topY + 4} L${x + 28} ${topY + 11}`} />
          <text x={x + 24} y={topY} textAnchor="middle" fontSize={11} fontWeight={600} fill="var(--warm)" stroke="none" fontFamily="var(--font-sans)">
            {label}
          </text>
        </g>,
      );
    } else if (t === "vibrato") {
      const next = placed.slice(i + 1).find((p) => p.event !== ref.event);
      const span = Math.max(18, Math.min(60, (next ? next.x : x + 40) - x - 8));
      let d = `M${x - 6} ${staffTop - 9}`;
      for (let k = 0; k * 6 < span; k++) d += ` q 1.5 ${k % 2 ? 4 : -4} 3 0 t 3 0`;
      marks.push(<path key={`v${i}`} d={d} fill="none" stroke="var(--warm)" strokeWidth={1.4} />);
    } else if (t === "tremolo") {
      marks.push(
        <text key={`tr${i}`} x={x} y={staffTop - 8} textAnchor="middle" fontSize={10} fontWeight={600} fill="var(--warm)" fontFamily="var(--font-sans)">
          tr
        </text>,
      );
    }
    if (t === "palm_mute") {
      const last = palmRuns[palmRuns.length - 1];
      if (last && x - last.to < 70) last.to = x;
      else palmRuns.push({ from: x, to: x });
    }
    prevOnString.set(note.string, ref);
  });

  const inSelection = (bar: number) => selection !== null && bar >= selection.start && bar <= selection.end;
  const selectedBars = row.bars.filter((b) => inSelection(b.index));

  return (
    <svg width={width} height={height} className="block overflow-visible" aria-hidden="true" focusable="false">
      {/* current-bar highlight */}
      {row.bars
        .filter((b) => b.index === currentBar)
        .map((b) => (
          <rect key="hl" x={b.x} y={staffTop - 8} width={b.width} height={staffBottom - staffTop + 16} fill="var(--accent-soft)" />
        ))}

      {/* click targets: whole bar body seeks, top band selects bars */}
      {row.bars.map((b) => (
        <g key={`hit${b.index}`}>
          <rect
            x={b.x}
            y={staffTop - 8}
            width={b.width}
            height={staffBottom - staffTop + 16 + BOTTOM_BAND - 8}
            fill="transparent"
            style={{ cursor: "pointer" }}
            onClick={(e) => onBarClick(b.index, e)}
            data-bar={b.index}
          />
          <rect
            x={b.x}
            y={0}
            width={b.width}
            height={staffTop - 10}
            fill="transparent"
            style={{ cursor: "cell", touchAction: "none" }}
            onPointerDown={(e) => onBarPointerDown(b.index, e)}
            onPointerEnter={() => onBarPointerEnter(b.index)}
          />
        </g>
      ))}

      {/* strings */}
      <g pointerEvents="none">
      {Array.from({ length: stringCount }, (_, s) => (
        <line key={`s${s}`} x1={0} y1={stringY(s, stringCount)} x2={width} y2={stringY(s, stringCount)} stroke="var(--string)" strokeWidth={1} />
      ))}

      </g>
      {/* bar lines, numbers, sections */}
      <g pointerEvents="none">
      {row.bars.map((b, i) => (
        <g key={`bar${b.index}`}>
          <line x1={b.x + (i === 0 ? 0.75 : 0)} y1={staffTop} x2={b.x + (i === 0 ? 0.75 : 0)} y2={staffBottom} stroke="var(--line-strong)" strokeWidth={1.5} />
          {sections[b.index] && (
            <g>
              <rect x={b.x} y={staffTop - 29} width={18} height={18} rx={5} fill="var(--text)" />
              <text x={b.x + 9} y={staffTop - 16} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--surface)" fontFamily="var(--font-sans)">
                {sections[b.index]}
              </text>
            </g>
          )}
          <text x={b.x + (sections[b.index] ? 23 : 4)} y={staffTop - 14} fontSize={11} fill="var(--text-muted)" fontFamily="var(--font-mono)" className="tabular select-none">
            {b.index + 1}
          </text>
        </g>
      ))}
      <line x1={width - 0.75} y1={staffTop} x2={width - 0.75} y2={staffBottom} stroke="var(--line-strong)" strokeWidth={1.5} />
      </g>

      {/* time signature and tempo at the very start */}
      {isFirstRow && row.bars[0]?.index === 0 && (
        <g fontFamily="var(--font-sans)" fontWeight={700} fill="var(--text)" textAnchor="middle" className="select-none" pointerEvents="none">
          <text x={row.bars[0].contentX / 2 + 4} y={(staffTop + staffBottom) / 2 - 4} fontSize={22}>
            {layout.beatsPerMeasure}
          </text>
          <text x={row.bars[0].contentX / 2 + 4} y={(staffTop + staffBottom) / 2 + 20} fontSize={22}>
            4
          </text>
        </g>
      )}

      {/* selection / loop outline */}
      {selectedBars.length > 0 && (
        <g>
          <rect
            x={selectedBars[0].x + 1}
            y={staffTop - 10}
            width={selectedBars[selectedBars.length - 1].x + selectedBars[selectedBars.length - 1].width - selectedBars[0].x - 2}
            height={staffBottom - staffTop + 20}
            rx={3}
            fill={loopActive ? "none" : "var(--accent-soft)"}
            fillOpacity={loopActive ? 1 : 0.4}
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeDasharray="6 4"
            pointerEvents="none"
          />
        </g>
      )}

      <g pointerEvents="none">{connectors}</g>

      {/* notes */}
      <g fontFamily="var(--font-mono)" fontSize={15} fontWeight={700} textAnchor="middle">
        {row.bars.map((b) =>
          b.events.map((e) =>
            events[e.index].notes.map((note, k) => {
              const x = b.x + e.x;
              const y = stringY(note.string, stringCount);
              const label = String(note.fret);
              const w = label.length > 1 ? 22 : 16;
              return (
                <g key={`${e.index}-${k}`} onClick={() => onNoteClick(events[e.index].time)} style={{ cursor: "pointer" }}>
                  <rect x={x - w / 2} y={y - 9} width={w} height={18} fill={b.index === currentBar ? "var(--accent-soft)" : "var(--surface)"} />
                  <text x={x} y={y + 5} fill="var(--text)">
                    {label}
                  </text>
                </g>
              );
            }),
          ),
        )}
      </g>

      <g pointerEvents="none">{marks}</g>

      {/* palm mute: "P.M." with a dashed extender under the staff */}
      {palmRuns.map((run, i) => (
        <g key={`pm${i}`} stroke="var(--warm)" strokeWidth={1.5} fill="none" pointerEvents="none">
          <text x={run.from - 10} y={staffBottom + 20} fontSize={11} fontWeight={600} fill="var(--warm)" stroke="none" fontFamily="var(--font-sans)">
            P.M.
          </text>
          {run.to - run.from > 24 && (
            <>
              <path d={`M${run.from + 22} ${staffBottom + 16} H${run.to + 8}`} strokeDasharray="4 4" />
              <path d={`M${run.to + 8} ${staffBottom + 11} v10`} />
            </>
          )}
        </g>
      ))}
    </svg>
  );
});

export default function TabSheet({
  tab,
  trackIndex,
  subscribe,
  onSeek,
  selection,
  loopActive,
  onSelect,
  follow,
  onCurrentBarChange,
  bottomInset,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [width, setWidth] = useState(0);
  const [currentBar, setCurrentBar] = useState<number | null>(null);
  const [detached, setDetached] = useState(false); // user scrolled away while following
  const currentRowRef = useRef(-1);
  const lastTimeRef = useRef(0);
  const dragAnchor = useRef<number | null>(null);

  const track = tab.tracks[Math.min(trackIndex, tab.tracks.length - 1)];
  const events = track.events;
  const stringCount = tab.tuning.length;
  const rowHeight = stringY(0, stringCount) + BOTTOM_BAND;

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(Math.floor(entries[0].contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    const preset = width > 0 && width < PHONE_BREAKPOINT ? PHONE_SHEET : DESKTOP_SHEET;
    return buildSheet(
      events,
      tab.measureTimes ?? [],
      tab.beatTimes ?? [],
      tab.beatsPerMeasure ?? 4,
      tab.durationSeconds,
      { ...preset, width: Math.max(width, 280) },
    );
  }, [events, tab.measureTimes, tab.beatTimes, tab.beatsPerMeasure, tab.durationSeconds, width]);

  const sections = useMemo(() => detectSections(barSignatures(layout, events)), [layout, events]);

  const scrollRowIntoView = useCallback(
    (rowIndex: number, force = false) => {
      const el = rowRefs.current[rowIndex];
      if (!el || typeof el.getBoundingClientRect !== "function") return;
      const rect = el.getBoundingClientRect();
      const viewTop = 72;
      const viewBottom = window.innerHeight - bottomInset - 24;
      if (!force && rect.top >= viewTop && rect.bottom <= viewBottom) return;
      const target = window.scrollY + rect.top - Math.max(viewTop, (viewBottom - rect.height) * 0.35);
      window.scrollTo({ top: Math.max(0, target), behavior: prefersReducedMotion() ? "auto" : "smooth" });
    },
    [bottomInset],
  );

  const place = useCallback(
    (t: number) => {
      lastTimeRef.current = t;
      const pos = locate(layout, t);
      const cursor = cursorRef.current;
      const rowEl = rowRefs.current[pos.row];
      if (cursor && rowEl) {
        cursor.style.transform = `translate(${pos.x}px, ${rowEl.offsetTop + TOP_BAND - 10}px)`;
      }
      setCurrentBar((prev) => (prev === pos.bar ? prev : pos.bar));
      if (pos.row !== currentRowRef.current) {
        currentRowRef.current = pos.row;
        if (follow && !detached) scrollRowIntoView(pos.row);
      }
    },
    [layout, follow, detached, scrollRowIntoView],
  );

  useEffect(() => subscribe((state) => place(state.time)), [subscribe, place]);
  // Re-place after a relayout (resize, track switch) without waiting for playback.
  useEffect(() => {
    currentRowRef.current = -1;
    place(lastTimeRef.current);
  }, [layout, place]);

  useEffect(() => {
    if (currentBar !== null) onCurrentBarChange(currentBar);
  }, [currentBar, onCurrentBarChange]);

  // Manual scrolling while following detaches the view until "back to cursor".
  useEffect(() => {
    if (!follow) return;
    const detach = () => setDetached(true);
    const onKey = (e: KeyboardEvent) => {
      if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(e.key)) detach();
    };
    window.addEventListener("wheel", detach, { passive: true });
    window.addEventListener("touchmove", detach, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("wheel", detach);
      window.removeEventListener("touchmove", detach);
      window.removeEventListener("keydown", onKey);
    };
  }, [follow]);

  // Bar selection by dragging across bar numbers.
  useEffect(() => {
    const up = () => {
      dragAnchor.current = null;
    };
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);

  const onBarPointerDown = useCallback(
    (bar: number, e: React.PointerEvent) => {
      e.preventDefault();
      dragAnchor.current = bar;
      onSelect({ start: bar, end: bar });
    },
    [onSelect],
  );
  const onBarPointerEnter = useCallback(
    (bar: number) => {
      const anchor = dragAnchor.current;
      if (anchor === null) return;
      onSelect({ start: Math.min(anchor, bar), end: Math.max(anchor, bar) });
    },
    [onSelect],
  );
  const onBarClick = useCallback(
    (bar: number, e: React.MouseEvent) => {
      if (e.shiftKey) {
        const anchor = selection ? selection.start : (currentBar ?? bar);
        onSelect({ start: Math.min(anchor, bar), end: Math.max(anchor, bar) });
        return;
      }
      onSeek(Math.max(0, layout.bars[bar].start));
    },
    [layout, onSeek, onSelect, selection, currentBar],
  );
  const onNoteClick = useCallback((time: number) => onSeek(Math.max(0, time)), [onSeek]);

  const backToCursor = () => {
    setDetached(false);
    scrollRowIntoView(Math.max(0, currentRowRef.current), true);
  };

  const noteCount = events.reduce((n, e) => n + e.notes.length, 0);

  return (
    <div className="relative">
      <p className="sr-only">
        Табулатура: {layout.bars.length} тактов, {noteCount} нот. Пробел — воспроизведение, стрелки влево и вправо —
        переход по тактам.
      </p>
      <div ref={containerRef} className="relative" data-testid="tab-sheet">
        {width > 0 &&
          layout.rows.map((row) => (
            <div
              key={row.index}
              ref={(el) => {
                rowRefs.current[row.index] = el;
              }}
              className={row.width > width + 1 ? "overflow-x-auto" : ""}
              style={{ marginBottom: ROW_GAP }}
            >
              <Row
                row={row}
                layout={layout}
                events={events}
                sections={sections}
                stringCount={stringCount}
                currentBar={currentBar !== null && row.bars.some((b) => b.index === currentBar) ? currentBar : null}
                selection={selection && row.bars.some((b) => b.index >= selection.start && b.index <= selection.end) ? selection : null}
                loopActive={loopActive}
                onBarPointerDown={onBarPointerDown}
                onBarPointerEnter={onBarPointerEnter}
                onBarClick={onBarClick}
                onNoteClick={onNoteClick}
              />
            </div>
          ))}
        <div
          ref={cursorRef}
          aria-hidden="true"
          data-testid="cursor"
          className="pointer-events-none absolute top-0 left-0 w-[2.5px] rounded-full bg-accent opacity-60"
          style={{ height: rowHeight - TOP_BAND - BOTTOM_BAND + 20, willChange: "transform" }}
        />
      </div>
      {follow && detached && (
        <button
          type="button"
          onClick={backToCursor}
          className="fixed left-1/2 z-20 inline-flex min-h-11 -translate-x-1/2 items-center gap-2 rounded-full border border-line bg-raised px-4 text-sm font-semibold text-accent-strong shadow-float"
          style={{ bottom: bottomInset + 12 }}
        >
          Вернуться к курсору
        </button>
      )}
    </div>
  );
}
