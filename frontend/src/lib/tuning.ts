/**
 * Tuning presets and the client-side mirror of the backend's tuning rules
 * (`TabSettings._validate_tuning` in backend/app/models/schemas.py): 4–8
 * strings, each a note like E2 / F#3 / Bb1, open pitch between A0 and A4.
 * Validating while the user types means a typo never costs them an upload
 * round-trip — the server still validates, this is only a convenience.
 */

export interface TuningPreset {
  id: string;
  label: string;
  short: string;
  tuning: string[];
}

export const TUNING_PRESETS: TuningPreset[] = [
  { id: "standard", label: "Стандарт", short: "E A D G B E", tuning: ["E2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "drop-d", label: "Drop D", short: "D A D G B E", tuning: ["D2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "eb", label: "E♭", short: "полтона ниже", tuning: ["Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"] },
  { id: "open-d", label: "Open D", short: "D A D F# A D", tuning: ["D2", "A2", "D3", "Gb3", "A3", "D4"] },
];

const NOTE_RE = /^([A-Ga-g])(#|b)?(-?\d)$/;
const SEMITONES: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const MIN_MIDI = 21; // A0
const MAX_MIDI = 69; // A4
export const MIN_STRINGS = 4;
export const MAX_STRINGS = 8;

export function noteToMidi(name: string): number | null {
  const m = NOTE_RE.exec(name.trim());
  if (!m) return null;
  const accidental = m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0;
  return (Number(m[3]) + 1) * 12 + SEMITONES[m[1].toUpperCase()] + accidental;
}

export type TuningParse = { ok: true; tuning: string[] } | { ok: false; error: string };

/** Parse free text like "D2 A2 D3 G3 B3 E4" (spaces or commas), low string first. */
export function parseTuning(text: string): TuningParse {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  if (parts.length < MIN_STRINGS) return { ok: false, error: `Нужно минимум ${MIN_STRINGS} струны — сейчас ${parts.length}.` };
  if (parts.length > MAX_STRINGS) return { ok: false, error: `Допускается максимум ${MAX_STRINGS} струн — сейчас ${parts.length}.` };
  for (let i = 0; i < parts.length; i++) {
    const midi = noteToMidi(parts[i]);
    if (midi === null) {
      const hint = /^[Hh]/.test(parts[i]) ? " В латинской записи это B." : " Пример: E2, F#3, Bb1.";
      return { ok: false, error: `Струна ${i + 1}: «${parts[i]}» не нота.${hint}` };
    }
    if (midi < MIN_MIDI || midi > MAX_MIDI) {
      return { ok: false, error: `Струна ${i + 1}: ${parts[i]} вне диапазона A0–A4.` };
    }
  }
  return { ok: true, tuning: parts.map((p) => p[0].toUpperCase() + p.slice(1)) };
}

export function presetFor(tuning: string[]): TuningPreset | undefined {
  return TUNING_PRESETS.find((p) => p.tuning.join(" ") === tuning.join(" "));
}

/** "E A D G B E" from note names, flats/sharps as ♭/♯. */
export function tuningLetters(tuning: string[]): string {
  return tuning
    .map((n) => {
      const m = NOTE_RE.exec(n);
      if (!m) return n;
      return m[1].toUpperCase() + (m[2] === "b" ? "♭" : m[2] === "#" ? "♯" : "");
    })
    .join(" ");
}
