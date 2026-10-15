import { describe, expect, it } from "vitest";
import { barSimilarity, detectSections } from "./sections";

// A "riff" is 4 bars of 6 tokens; variants drop or change a token per bar.
function riff(seed: number): string[][] {
  return Array.from({ length: 4 }, (_, bar) =>
    Array.from({ length: 6 }, (_, k) => `${k * 0.5}@${(seed + k) % 6}:${(seed * 3 + bar * 2 + k) % 15}`),
  );
}
function sloppy(bars: string[][], changedPerBar = 1): string[][] {
  return bars.map((bar) => bar.map((token, i) => (i < changedPerBar ? `${token}x` : token)));
}

describe("barSimilarity", () => {
  it("is 1 for equal bars and 0 for disjoint ones", () => {
    expect(barSimilarity(["a", "b"], ["b", "a"])).toBe(1);
    expect(barSimilarity(["a"], ["b"])).toBe(0);
    expect(barSimilarity([], [])).toBe(1);
  });
});

describe("detectSections", () => {
  it("labels a verse/chorus structure A B A B", () => {
    const A = riff(1);
    const B = riff(4);
    const labels = detectSections([...A, ...B, ...A, ...B]);
    expect(labels[0]).toBe("A");
    expect(labels[4]).toBe("B");
    expect(labels[8]).toBe("A");
    expect(labels[12]).toBe("B");
    expect(labels.filter(Boolean)).toHaveLength(4);
  });

  it("recognises repeats despite transcription noise (similar, not identical)", () => {
    const A = riff(2);
    const B = riff(5);
    const labels = detectSections([...A, ...B, ...sloppy(A), ...sloppy(B)]);
    expect([labels[0], labels[4], labels[8], labels[12]]).toEqual(["A", "B", "A", "B"]);
  });

  it("does not equate groups that only share a little", () => {
    const A = riff(2);
    const labels = detectSections([...A, ...riff(3), ...sloppy(A, 4), ...riff(5)]);
    expect(labels.every((l) => l === null)).toBe(true);
  });

  it("merges a repeated section played twice in a row into one label", () => {
    const A = riff(1);
    const B = riff(4);
    const labels = detectSections([...A, ...A, ...B, ...A]);
    expect(labels[0]).toBe("A");
    expect(labels[4]).toBeNull();
    expect(labels[8]).toBe("B");
    expect(labels[12]).toBe("A");
  });

  it("stays silent when nothing repeats", () => {
    const labels = detectSections([...riff(0), ...riff(1), ...riff(2), ...riff(3)]);
    expect(labels.every((l) => l === null)).toBe(true);
  });

  it("stays silent for short pieces", () => {
    expect(detectSections([...riff(1)]).every((l) => l === null)).toBe(true);
  });

  it("stays silent when most of the piece is unique", () => {
    const A = riff(1);
    const labels = detectSections([...A, ...riff(2), ...riff(3), ...riff(4), ...riff(5), ...A]);
    expect(labels.every((l) => l === null)).toBe(true);
  });

  it("stays silent when a group is ambiguously close to two sections", () => {
    const A = riff(1);
    // B shares half of every bar with A: distinct from A, but a group made of
    // the overlap is equally close to both.
    const B = A.map((bar, i) => [...bar.slice(0, 3), ...riff(4)[i].slice(3)]);
    const mid = A.map((bar, i) => [...bar.slice(0, 3), ...bar.slice(3, 5), riff(4)[i][5], riff(4)[i][4]]);
    const labels = detectSections([...A, ...B, ...mid, ...A, ...B]);
    expect(labels.every((l) => l === null)).toBe(true);
  });

  it("ignores near-empty groups instead of calling them a section", () => {
    const A = riff(1);
    const empty = [[], [], [], []];
    const labels = detectSections([...A, ...empty, ...A, ...empty]);
    expect(labels[0]).toBe("A");
    expect(labels[4]).toBeNull();
    expect(labels[8]).toBe("A");
  });
});
