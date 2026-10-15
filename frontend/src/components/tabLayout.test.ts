import { describe, expect, it } from "vitest";
import { DEFAULT_AXIS_OPTIONS, buildTimeAxis, followScroll, timeToX, xToTime } from "./tabLayout";

const opts = DEFAULT_AXIS_OPTIONS; // left 56, 90 px/s, note gap 30, bar gap 14

describe("buildTimeAxis", () => {
  it("keeps sparse passages proportional", () => {
    const axis = buildTimeAxis([1, 2, 4], [], opts);
    const [a, b, c] = axis.eventX;
    expect(b - a).toBeCloseTo(90);
    expect(c - b).toBeCloseTo(180);
  });

  it("gives every note in a fast passage at least the minimum gap", () => {
    // Sixteenth notes at 160 BPM: ~0.094 s apart -> 8.4 px proportionally.
    const times = Array.from({ length: 16 }, (_, i) => 1 + i * 0.094);
    const axis = buildTimeAxis(times, [], opts);
    for (let i = 1; i < axis.eventX.length; i++) {
      expect(axis.eventX[i] - axis.eventX[i - 1]).toBeGreaterThanOrEqual(opts.minNoteGap - 1e-9);
    }
  });

  it("returns to proportional spacing after a dense run", () => {
    const axis = buildTimeAxis([1, 1.05, 1.1, 3.1], [], opts);
    const [, , denseEnd, after] = axis.eventX;
    expect(after - denseEnd).toBeCloseTo(2 * 90);
  });

  it("puts a downbeat note just after its bar line, never on it", () => {
    const axis = buildTimeAxis([2], [0, 2], opts);
    expect(axis.eventX[0] - axis.measureX[1]).toBeGreaterThanOrEqual(opts.minBarGap);
  });

  it("is strictly increasing and handles pickup bars before zero", () => {
    const axis = buildTimeAxis([0.1, 0.12, 0.5], [-0.5, 1.5], opts);
    expect(axis.measureX[0]).toBe(opts.left);
    for (let i = 1; i < axis.anchors.length; i++) {
      expect(axis.anchors[i].t).toBeGreaterThan(axis.anchors[i - 1].t);
      expect(axis.anchors[i].x).toBeGreaterThan(axis.anchors[i - 1].x);
    }
  });

  it("scales sparse spacing with zoom but keeps the minimum gap fixed", () => {
    const zoomed = buildTimeAxis([1, 2, 2.01], [], { ...opts, pixelsPerSecond: 180 });
    expect(zoomed.eventX[1] - zoomed.eventX[0]).toBeCloseTo(180);
    expect(zoomed.eventX[2] - zoomed.eventX[1]).toBeCloseTo(opts.minNoteGap);
  });
});

describe("timeToX / xToTime", () => {
  const axis = buildTimeAxis([0.5, 0.55, 0.6, 2, 2.02], [0, 1.6], opts);

  it("maps note onsets exactly onto their columns", () => {
    [0.5, 0.55, 0.6, 2, 2.02].forEach((t, i) => expect(timeToX(axis, t)).toBeCloseTo(axis.eventX[i]));
  });

  it("round-trips anywhere on the axis, including past the last anchor", () => {
    for (const t of [0, 0.3, 0.52, 0.58, 1.1, 2.01, 5, 12.5]) {
      expect(xToTime(axis, timeToX(axis, t))).toBeCloseTo(t, 6);
    }
  });

  it("is monotonic, so the playhead never moves backwards while playing", () => {
    let prev = -Infinity;
    for (let t = 0; t < 4; t += 0.005) {
      const x = timeToX(axis, t);
      expect(x).toBeGreaterThanOrEqual(prev);
      prev = x;
    }
  });
});

describe("followScroll", () => {
  it("keeps the playhead at a fixed reading position while playing", () => {
    expect(followScroll(1000, 0, 800, true)).toBeCloseTo(1000 - 800 * 0.35);
    expect(followScroll(100, 0, 800, true)).toBeNull(); // clamped at 0 and already there
  });

  it("only jumps when paused if the playhead left the view", () => {
    expect(followScroll(400, 0, 800, false)).toBeNull();
    expect(followScroll(2000, 0, 800, false)).toBeCloseTo(2000 - 280);
    expect(followScroll(100, 1000, 800, false)).toBe(0);
  });
});
