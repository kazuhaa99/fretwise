import type { TabNote } from "../types";

const TECHNIQUE_MARKS: Record<NonNullable<TabNote["technique"]>, string> = {
  hammer_on: "h",
  pull_off: "p",
  slide: "/",
  bend: "b",
  vibrato: "~",
  palm_mute: "PM",
  tremolo: "tr",
};

const QUARTER_FRACTIONS = ["", "¼", "½", "¾"];

/**
 * Standard bend notation, counted in whole tones: ¼ (quarter-tone curl),
 * ½ (half-step), 1 ("full", a whole step), 1½, ...
 */
export function bendLabel(semitones: number): string {
  const quarterTones = Math.max(1, Math.round(semitones * 2));
  const whole = Math.floor(quarterTones / 4);
  return `b${whole > 0 ? whole : ""}${QUARTER_FRACTIONS[quarterTones % 4]}`;
}

export function techniqueMark(note: TabNote): string | null {
  if (!note.technique) return null;
  if (note.technique === "bend" && note.bendSemitones) return bendLabel(note.bendSemitones);
  return TECHNIQUE_MARKS[note.technique];
}
