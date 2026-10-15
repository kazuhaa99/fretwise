/**
 * Time axis for the tab viewer: maps seconds to x pixels and back.
 *
 * A purely proportional axis (x = t × pixels-per-second) is what made fast
 * passages unreadable — at 90 px/s, notes 0.1 s apart overlap their 22 px
 * boxes. Instead the axis is *elastic*: every gap between consecutive anchors
 * (note onsets and bar lines) gets its proportional width or a minimum width,
 * whichever is larger. Sparse passages stay proportional, so rhythm is still
 * visible at a glance; dense ones stretch just enough that every fret number
 * gets its own room. Between anchors the mapping is linear, so the playhead,
 * click-to-seek and bar lines all share one consistent axis.
 */

export interface AxisOptions {
  /** x of the axis origin (left gutter for string names). */
  left: number;
  /** Proportional scale for sparse passages (base scale × zoom). */
  pixelsPerSecond: number;
  /** Minimum distance between two consecutive note columns. */
  minNoteGap: number;
  /** Minimum distance between a bar line and the neighbouring note. */
  minBarGap: number;
}

export const DEFAULT_AXIS_OPTIONS: AxisOptions = {
  left: 56,
  pixelsPerSecond: 90,
  minNoteGap: 30,
  minBarGap: 14,
};

export interface Anchor {
  t: number;
  x: number;
}

export interface TimeAxis {
  anchors: Anchor[]; // strictly increasing in both t and x
  pixelsPerSecond: number;
  /** x of every note column, in the order of the `eventTimes` passed in. */
  eventX: number[];
  /** x of every bar line, in the order of the `measureTimes` passed in. */
  measureX: number[];
}

type Item = { t: number; kind: "event" | "bar"; index: number };

export function buildTimeAxis(
  eventTimes: number[],
  measureTimes: number[],
  options: AxisOptions = DEFAULT_AXIS_OPTIONS,
): TimeAxis {
  const { left, pixelsPerSecond: pps, minNoteGap, minBarGap } = options;

  const items: Item[] = [
    ...measureTimes.map((t, index) => ({ t, kind: "bar" as const, index })),
    ...eventTimes.map((t, index) => ({ t, kind: "event" as const, index })),
  ];
  // Bar lines sort before a note at the same instant: the note on a downbeat
  // belongs inside the new bar, right after its line.
  items.sort((a, b) => a.t - b.t || (a.kind === b.kind ? 0 : a.kind === "bar" ? -1 : 1));

  const start = Math.min(0, ...items.map((item) => item.t));
  const anchors: Anchor[] = [{ t: start, x: left }];
  const eventX = new Array<number>(eventTimes.length);
  const measureX = new Array<number>(measureTimes.length);

  let prev: { t: number; x: number; kind: Item["kind"] | "origin" } = { t: start, x: left, kind: "origin" };
  for (const item of items) {
    const minGap =
      prev.kind === "origin"
        ? item.kind === "event" ? minBarGap : 0
        : prev.kind === "event" && item.kind === "event"
          ? minNoteGap
          : minBarGap;
    const x = Math.max(prev.x + (item.t - prev.t) * pps, prev.x + minGap);

    if (item.kind === "event") eventX[item.index] = x;
    else measureX[item.index] = x;

    const last = anchors[anchors.length - 1];
    if (item.t > last.t) anchors.push({ t: item.t, x });
    else last.x = Math.max(last.x, x); // same instant (bar + note): keep the later x
    prev = { t: item.t, x, kind: item.kind };
  }

  return { anchors, pixelsPerSecond: pps, eventX, measureX };
}

/** Index of the last anchor with anchor[key] <= value (binary search). */
function floorIndex(anchors: Anchor[], key: "t" | "x", value: number): number {
  let lo = 0;
  let hi = anchors.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (anchors[mid][key] <= value) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export function timeToX(axis: TimeAxis, t: number): number {
  const { anchors, pixelsPerSecond } = axis;
  const first = anchors[0];
  if (t <= first.t) return first.x + (t - first.t) * pixelsPerSecond;
  const i = floorIndex(anchors, "t", t);
  const a = anchors[i];
  const b = anchors[i + 1];
  if (!b) return a.x + (t - a.t) * pixelsPerSecond;
  return a.x + ((t - a.t) / (b.t - a.t)) * (b.x - a.x);
}

export function xToTime(axis: TimeAxis, x: number): number {
  const { anchors, pixelsPerSecond } = axis;
  const first = anchors[0];
  if (x <= first.x) return first.t + (x - first.x) / pixelsPerSecond;
  const i = floorIndex(anchors, "x", x);
  const a = anchors[i];
  const b = anchors[i + 1];
  if (!b) return a.t + (x - a.x) / pixelsPerSecond;
  return a.t + ((x - a.x) / (b.x - a.x)) * (b.t - a.t);
}

/**
 * Where the scroll container should be so the playhead sits at a comfortable
 * reading position. While playing the tab scrolls continuously under a fixed
 * playhead (no page jumps); when paused we only move if the playhead left the
 * visible window, e.g. after a seek.
 */
export function followScroll(
  playheadX: number,
  scrollLeft: number,
  viewportWidth: number,
  playing: boolean,
  anchorFraction = 0.35,
): number | null {
  const target = Math.max(0, playheadX - viewportWidth * anchorFraction);
  if (playing) return Math.abs(target - scrollLeft) >= 0.5 ? target : null;
  const visible = playheadX >= scrollLeft + 24 && playheadX <= scrollLeft + viewportWidth - 24;
  return visible ? null : target;
}
