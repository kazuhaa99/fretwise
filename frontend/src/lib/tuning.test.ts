import { describe, expect, it } from "vitest";
import { formatEta, formatSize, formatTime, plural } from "./format";
import { noteToMidi, parseTuning, presetFor, tuningLetters } from "./tuning";

describe("parseTuning (mirrors the backend rules)", () => {
  it("accepts presets and custom tunings, low string first", () => {
    expect(parseTuning("D2 A2 D3 G3 B3 E4")).toEqual({ ok: true, tuning: ["D2", "A2", "D3", "G3", "B3", "E4"] });
    expect(parseTuning("b1, e2, a2, d3, g3, b3, e4")).toMatchObject({ ok: true });
  });

  it("explains what is wrong, string by string", () => {
    expect(parseTuning("E2 A2 D3")).toEqual({ ok: false, error: "Нужно минимум 4 струны — сейчас 3." });
    expect(parseTuning("E2 A2 H3 G3 B3 E4")).toEqual({ ok: false, error: "Струна 3: «H3» не нота. В латинской записи это B." });
    expect(parseTuning("E2 A2 D3 G3 B3 E7")).toEqual({ ok: false, error: "Струна 6: E7 вне диапазона A0–A4." });
    expect(parseTuning("E2 ".repeat(9))).toMatchObject({ ok: false });
  });

  it("knows note numbers and presets", () => {
    expect(noteToMidi("E2")).toBe(40);
    expect(noteToMidi("Eb4")).toBe(63);
    expect(presetFor(["D2", "A2", "D3", "G3", "B3", "E4"])?.label).toBe("Drop D");
    expect(tuningLetters(["Eb2", "F#3"])).toBe("E♭ F♯");
  });
});

describe("format", () => {
  it("formats times, sizes and estimates in Russian", () => {
    expect(formatTime(102.7)).toBe("1:42");
    expect(formatSize(84.2 * 1024 * 1024)).toBe("84 МБ");
    expect(formatSize(3.25 * 1024 * 1024)).toBe("3,3 МБ");
    expect(formatEta(30)).toBe("меньше минуты");
    expect(formatEta(185)).toBe("≈ 3 минуты");
    expect(formatEta(null)).toBeNull();
    expect([1, 2, 5, 11, 21, 22].map((n) => plural(n, ["трек", "трека", "треков"]))).toEqual([
      "трек", "трека", "треков", "треков", "трек", "трека",
    ]);
  });
});
