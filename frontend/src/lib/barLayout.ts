/**
 * Page layout for the tab sheet: bars in rows, like a printed score.
 *
 * Bars come from the backend's grid (`measureTimes` / `beatTimes`, the same
 * bar lines as the .gp5). Inside a bar, notes sit at their *beat* position,
 * but on an elastic axis: each gap between consecutive note columns is its
 * proportional width or a minimum width, whichever is larger. So a bar of
 * sixteenths is simply wider than a bar of whole notes — the width of a bar
 * follows how many notes it holds, never at the cost of numbers overlapping.
 * Bars are then packed greedily into rows of the available width and each
 * full row is stretched to the edge (justified), as engraved music is.
 *
 * Everything here is pure: the component renders whatever this returns, and
 * the playhead uses `locate()` to turn a time into (row, x).
 */

import type { TabEvent } from "../types";

export interface SheetOptions {
  /** Width available for one row of bars, in px. */
  width: number;
  /** Proportional width of one beat in sparse passages. */
  beatWidth: number;
  /** Minimum distance between two consecutive note columns. */
  minColumnGap: number;
  /** Space between a bar line and the first/last note slot. */
  barPadding: number;
  /** No bar narrower than this, however empty. */
  minBarWidth: number;
  /** Extra room in the very first bar for the time signature. */
  headerWidth: number;
  /** Stretch full rows to the full width. */
  justify: boolean;
}

export const DESKTOP_SHEET: Omit<SheetOptions, "width"> = {
  beatWidth: 40,
  minColumnGap: 30,
  barPadding: 16,
  minBarWidth: 132,
  headerWidth: 30,
  justify: true,
};

export const PHONE_SHEET: Omit<SheetOptions, "width"> = {
  beatWidth: 30,
  minColumnGap: 28,
  barPadding: 12,
  minBarWidth: 140,
  headerWidth: 26,
  justify: true,
};

const HALF_NOTE_BOX = 11;
// A note this close before a bar line belongs to the next bar (attack jitter).
const BAR_EPSILON = 0.03;

export interface PlacedEvent {
  /** Index into the track's `events`. */
  index: number;
  time: number;
  /** Beat position within the bar, 0 ≤ pos < beatsPerMeasure. */
  pos: number;
  /** x relative to the bar's left edge. */
  x: number;
}

export interface PlacedBar {
  index: number;
  start: number;
  end: number;
  /** x of the bar's left edge within its row. */
  x: number;
  width: number;
  /** x (relative to the bar) where note slots begin — after the header in bar 0. */
  contentX: number;
  row: number;
  events: PlacedEvent[];
}

export interface SheetRow {
  index: number;
  bars: PlacedBar[];
  width: number;
}

export interface SheetLayout {
  rows: SheetRow[];
  bars: PlacedBar[];
  beatsPerMeasure: number;
}

interface BarSpan {
  start: number;
  end: number;
}

/** Bar boundaries from the grid, with a 2-second fallback for old results. */
export function barSpans(measureTimes: number[], durationSeconds: number): BarSpan[] {
  const times = measureTimes.length > 0 ? measureTimes : fallbackMeasures(durationSeconds);
  return times.map((start, i) => {
    const next = times[i + 1];
    const previousLength = i > 0 ? start - times[i - 1] : 2;
    return { start, end: next ?? start + previousLength };
  });
}

function fallbackMeasures(duration: number): number[] {
  const out: number[] = [];
  for (let t = 0; t < Math.max(duration, 2); t += 2) out.push(t);
  return out;
}

/** Index of the last span with start ≤ t (0 if t is before all of them). */
export function spanIndexAt(spans: BarSpan[], t: number): number {
  let lo = 0;
  let hi = spans.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (spans[mid].start <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Beat position of `t` inside bar `barIndex`, from the tracked beats when
 * they line up with the bars, otherwise by plain time proportion. */
function beatPosition(t: number, bar: BarSpan, barIndex: number, beatTimes: number[], bpm: number): number {
  const first = barIndex * bpm;
  let pos: number;
  if (beatTimes.length >= first + bpm && Math.abs(beatTimes[first] - bar.start) < 1e-3) {
    const beats = beatTimes.slice(first, first + bpm).concat(bar.end);
    if (t <= beats[0]) pos = 0;
    else {
      let k = 0;
      while (k < bpm - 1 && beats[k + 1] <= t) k++;
      const span = beats[k + 1] - beats[k];
      pos = k + (span > 0 ? (t - beats[k]) / span : 0);
    }
  } else {
    const length = bar.end - bar.start;
    pos = length > 0 ? ((t - bar.start) / length) * bpm : 0;
  }
  return Math.min(Math.max(pos, 0), bpm - 1e-6);
}

export function buildSheet(
  events: TabEvent[],
  measureTimes: number[],
  beatTimes: number[],
  beatsPerMeasure: number,
  durationSeconds: number,
  options: SheetOptions,
): SheetLayout {
  const bpm = Math.max(1, beatsPerMeasure || 4);
  const spans = barSpans(measureTimes, durationSeconds);

  // 1. Distribute events into bars.
  const perBar: { index: number; time: number }[][] = spans.map(() => []);
  events.forEach((event, index) => {
    perBar[spanIndexAt(spans, event.time + BAR_EPSILON)].push({ index, time: event.time });
  });

  // 2. Natural (unjustified) layout of each bar on the elastic axis.
  const natural = spans.map((span, barIndex) => {
    const contentX = barIndex === 0 ? options.headerWidth : 0;
    const placed: PlacedEvent[] = [];
    let prevX = -Infinity;
    let prevPos = 0;
    for (const { index, time } of perBar[barIndex].sort((a, b) => a.time - b.time)) {
      const pos = beatPosition(time, span, barIndex, beatTimes, bpm);
      const proportional = contentX + options.barPadding + pos * options.beatWidth;
      const x =
        prevX === -Infinity
          ? proportional
          : Math.max(prevX + options.minColumnGap, prevX + (pos - prevPos) * options.beatWidth);
      placed.push({ index, time, pos, x });
      prevX = x;
      prevPos = pos;
    }
    const lastX = placed.length ? placed[placed.length - 1].x : 0;
    const width = Math.max(
      options.minBarWidth + contentX,
      contentX + options.barPadding * 2 + bpm * options.beatWidth,
      lastX + HALF_NOTE_BOX + options.barPadding,
    );
    return { span, contentX, placed, width };
  });

  // 3. Greedy rows, then justify every row except a short last one.
  const rows: SheetRow[] = [];
  const bars: PlacedBar[] = [];
  let current: typeof natural = [];
  let currentWidth = 0;
  const flush = (isLast: boolean) => {
    if (!current.length) return;
    const fill = currentWidth / options.width;
    const stretch =
      options.justify && currentWidth < options.width && (!isLast || fill >= 0.75) ? options.width / currentWidth : 1;
    const row: SheetRow = { index: rows.length, bars: [], width: 0 };
    let x = 0;
    for (const bar of current) {
      const barIndex = bars.length;
      const placedBar: PlacedBar = {
        index: barIndex,
        start: bar.span.start,
        end: bar.span.end,
        x,
        width: bar.width * stretch,
        contentX: bar.contentX * stretch,
        row: row.index,
        events: bar.placed.map((e) => ({ ...e, x: e.x * stretch })),
      };
      row.bars.push(placedBar);
      bars.push(placedBar);
      x += placedBar.width;
    }
    row.width = x;
    rows.push(row);
    current = [];
    currentWidth = 0;
  };
  natural.forEach((bar) => {
    if (current.length && currentWidth + bar.width > options.width) flush(false);
    current.push(bar);
    currentWidth += bar.width;
  });
  flush(true);

  return { rows, bars, beatsPerMeasure: bpm };
}

export interface SheetPosition {
  bar: number;
  row: number;
  /** x within the row. */
  x: number;
}

/** Where the playhead goes at time `t`: linear between the bar line, the
 * bar's note columns and the next bar line — so it reaches every note
 * exactly at its onset and never moves backwards. */
/** Piecewise-linear (time, x) anchors of one bar: bar line, note columns, next bar line. */
function barAnchors(bar: PlacedBar): { t: number; x: number }[] {
  const anchors: { t: number; x: number }[] = [{ t: bar.start, x: bar.contentX }];
  for (const e of bar.events) {
    const last = anchors[anchors.length - 1];
    if (e.time > last.t && e.x >= last.x) anchors.push({ t: e.time, x: e.x });
    // A note on (or a hair before) the downbeat: at that instant the cursor
    // belongs on the note, not on the bar line before it.
    else if (e.time <= last.t) last.x = Math.max(last.x, e.x);
  }
  const tail = anchors[anchors.length - 1];
  if (bar.end > tail.t) anchors.push({ t: bar.end, x: Math.max(bar.width, tail.x) });
  return anchors;
}

export function locate(layout: SheetLayout, t: number): SheetPosition {
  const { bars } = layout;
  if (!bars.length) return { bar: 0, row: 0, x: 0 };
  const bar = bars[spanIndexAt(bars, t)];
  const anchors = barAnchors(bar);

  let x: number;
  if (t <= anchors[0].t) x = anchors[0].x;
  else if (t >= anchors[anchors.length - 1].t) x = anchors[anchors.length - 1].x;
  else {
    let k = 0;
    while (anchors[k + 1].t <= t) k++;
    const a = anchors[k];
    const b = anchors[k + 1];
    x = a.x + ((t - a.t) / (b.t - a.t)) * (b.x - a.x);
  }
  return { bar: bar.index, row: bar.row, x: bar.x + Math.min(x, bar.width) };
}

export interface SheetHit {
  bar: number;
  /** Time under x (inverse of `locate`). */
  time: number;
  /** Index into the track's events when x is on an existing note column. */
  eventIndex: number | null;
}

const COLUMN_HIT_RADIUS = 12;

/** What's under x in a row — for adding a note where the user clicked. A
 * click close to an existing column joins that chord. */
export function hitTest(layout: SheetLayout, rowIndex: number, x: number): SheetHit | null {
  const row = layout.rows[rowIndex];
  if (!row || !row.bars.length) return null;
  let bar = row.bars[0];
  for (const b of row.bars) if (b.x <= x) bar = b;
  const local = x - bar.x;

  let nearest: PlacedEvent | null = null;
  for (const e of bar.events) {
    if (Math.abs(e.x - local) <= COLUMN_HIT_RADIUS && (!nearest || Math.abs(e.x - local) < Math.abs(nearest.x - local))) {
      nearest = e;
    }
  }
  if (nearest) return { bar: bar.index, time: nearest.time, eventIndex: nearest.index };

  const anchors = barAnchors(bar);
  let time: number;
  if (local <= anchors[0].x) time = anchors[0].t;
  else if (local >= anchors[anchors.length - 1].x) time = anchors[anchors.length - 1].t;
  else {
    let k = 0;
    while (anchors[k + 1].x <= local) k++;
    const a = anchors[k];
    const b = anchors[k + 1];
    time = b.x > a.x ? a.t + ((local - a.x) / (b.x - a.x)) * (b.t - a.t) : a.t;
  }
  return { bar: bar.index, time: Math.max(bar.start, Math.min(time, bar.end - 1e-3)), eventIndex: null };
}

/** Signature of each bar's content for section detection: which strings and
 * frets sound at which (quarter-beat-rounded) positions. */
export function barSignatures(layout: SheetLayout, events: TabEvent[]): string[][] {
  return layout.bars.map((bar) =>
    bar.events.flatMap((placed) =>
      events[placed.index].notes.map((n) => `${Math.round(placed.pos * 4) / 4}@${n.string}:${n.fret}`),
    ),
  );
}
