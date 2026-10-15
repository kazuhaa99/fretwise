import { describe, expect, it } from "vitest";
import type { TabEvent } from "../types";
import { DESKTOP_SHEET, barSignatures, buildSheet, locate, type SheetOptions } from "./barLayout";

const ev = (time: number, string = 0, fret = 0): TabEvent => ({ time, duration: 0.1, notes: [{ string, fret }] });

// 120 BPM 4/4: bars every 2 s, beats every 0.5 s.
const measures = (n: number) => Array.from({ length: n }, (_, i) => i * 2);
const beats = (n: number) => Array.from({ length: n * 4 }, (_, i) => i * 0.5);
const opts = (width = 900): SheetOptions => ({ ...DESKTOP_SHEET, width });

describe("buildSheet", () => {
  it("puts every note into the bar it falls in", () => {
    const events = [ev(0.1), ev(1.9), ev(2.0), ev(5.5)];
    const sheet = buildSheet(events, measures(3), beats(3), 4, 6, opts());
    expect(sheet.bars.map((b) => b.events.map((e) => e.index))).toEqual([[0, 1], [2], [3]]);
  });

  it("treats a note a hair before the bar line as the next bar's downbeat", () => {
    const sheet = buildSheet([ev(1.98)], measures(2), beats(2), 4, 4, opts());
    expect(sheet.bars[1].events.map((e) => e.index)).toEqual([0]);
  });

  it("places notes by beat position inside the bar", () => {
    const sheet = buildSheet([ev(2.0), ev(3.0)], measures(2), beats(2), 4, 4, { ...opts(), justify: false });
    const [a, b] = sheet.bars[1].events;
    expect(a.pos).toBeCloseTo(0);
    expect(b.pos).toBeCloseTo(2);
    expect(b.x - a.x).toBeCloseTo(2 * DESKTOP_SHEET.beatWidth);
  });

  it("never lets note columns overlap, and widens busy bars", () => {
    const fast = Array.from({ length: 16 }, (_, i) => ev(2 + i * 0.125, i % 6, i));
    const sheet = buildSheet([ev(0), ...fast], measures(2), beats(2), 4, 4, { ...opts(2000), justify: false });
    const busy = sheet.bars[1];
    for (let i = 1; i < busy.events.length; i++) {
      expect(busy.events[i].x - busy.events[i - 1].x).toBeGreaterThanOrEqual(DESKTOP_SHEET.minColumnGap - 1e-9);
    }
    expect(busy.width).toBeGreaterThan(sheet.bars[0].width);
    // the last note still fits inside its bar
    expect(busy.events[busy.events.length - 1].x).toBeLessThan(busy.width);
  });

  it("wraps bars into rows that fit the width and justifies full rows", () => {
    const sheet = buildSheet([], measures(10), beats(10), 4, 20, opts(700));
    expect(sheet.rows.length).toBeGreaterThan(1);
    for (const row of sheet.rows.slice(0, -1)) expect(row.width).toBeCloseTo(700);
    for (const row of sheet.rows) {
      row.bars.forEach((bar, i) => {
        if (i > 0) expect(bar.x).toBeCloseTo(row.bars[i - 1].x + row.bars[i - 1].width);
      });
    }
    expect(sheet.bars.map((b) => b.index)).toEqual([...Array(10).keys()]);
  });

  it("leaves a short last row unstretched", () => {
    const sheet = buildSheet([], measures(5), beats(5), 4, 10, opts(700));
    const last = sheet.rows[sheet.rows.length - 1];
    expect(last.width).toBeLessThan(700 * 0.75);
  });

  it("supports 3/4 and reserves room for the time signature in bar 1", () => {
    const m = [0, 1.5, 3];
    const b = Array.from({ length: 9 }, (_, i) => i * 0.5);
    const sheet = buildSheet([ev(0), ev(1.5)], m, b, 3, 4.5, { ...opts(), justify: false });
    expect(sheet.beatsPerMeasure).toBe(3);
    expect(sheet.bars[0].contentX).toBe(DESKTOP_SHEET.headerWidth);
    expect(sheet.bars[1].contentX).toBe(0);
  });

  it("falls back to 2-second bars when the result has no bar lines", () => {
    const sheet = buildSheet([ev(0.5), ev(4.5)], [], [], 4, 6, opts());
    expect(sheet.bars).toHaveLength(3);
    expect(sheet.bars[2].events).toHaveLength(1);
  });
});

describe("locate", () => {
  const events = [ev(0.25), ev(0.5), ev(1.0), ev(2.0), ev(2.1), ev(2.2), ev(3.5)];
  const sheet = buildSheet(events, measures(3), beats(3), 4, 6, opts(400));

  it("hits every note column exactly at its onset", () => {
    for (const bar of sheet.bars) {
      for (const e of bar.events) {
        const pos = locate(sheet, e.time);
        expect(pos.bar).toBe(bar.index);
        expect(pos.x).toBeCloseTo(bar.x + e.x);
      }
    }
  });

  it("moves forward monotonically within a row and jumps rows at bar lines", () => {
    let prev = locate(sheet, 0);
    for (let t = 0.01; t < 6; t += 0.01) {
      const pos = locate(sheet, t);
      if (pos.row === prev.row) expect(pos.x).toBeGreaterThanOrEqual(prev.x - 1e-9);
      else expect(pos.row).toBe(prev.row + 1);
      prev = pos;
    }
  });

  it("reports the current bar for highlighting", () => {
    expect(locate(sheet, 0.1).bar).toBe(0);
    expect(locate(sheet, 2.05).bar).toBe(1);
    expect(locate(sheet, 99).bar).toBe(2);
  });
});

describe("barSignatures", () => {
  it("describes bars by beat position, string and fret", () => {
    const sheet = buildSheet([ev(0, 1, 3), ev(1, 2, 5)], measures(1), beats(1), 4, 2, opts());
    expect(barSignatures(sheet, [ev(0, 1, 3), ev(1, 2, 5)])[0]).toEqual(["0@1:3", "2@2:5"]);
  });
});
