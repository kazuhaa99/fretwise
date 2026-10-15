/**
 * Keyboard for edit mode. Separate from the playback shortcuts (hotkeys.ts):
 * while editing, ← / → walk between notes instead of bars, digits type a fret,
 * ↑ / ↓ move the note between strings, and Ctrl/Cmd+Z undoes.
 */
import { isTypingTarget } from "./hotkeys";

export type EditAction =
  | { type: "digit"; digit: number }
  | { type: "prevNote" }
  | { type: "nextNote" }
  | { type: "stringUp" }
  | { type: "stringDown" }
  | { type: "fretUp" }
  | { type: "fretDown" }
  | { type: "delete" }
  | { type: "deselect" }
  | { type: "undo" }
  | { type: "redo" };

export const EDIT_KEYS: { keys: string[]; label: string }[] = [
  { keys: ["0–9"], label: "Ввести лад (две цифры подряд — двузначный)" },
  { keys: ["↑", "↓"], label: "На соседнюю струну, высота сохраняется" },
  { keys: ["Shift", "↑ ↓"], label: "Лад выше / ниже" },
  { keys: ["←", "→"], label: "Предыдущая / следующая нота" },
  { keys: ["Delete"], label: "Удалить ноту" },
  { keys: ["Ctrl", "Z"], label: "Отменить" },
  { keys: ["Ctrl", "Shift", "Z"], label: "Повторить" },
  { keys: ["Esc"], label: "Снять выделение" },
];

export function resolveEditKey(event: KeyboardEvent): EditAction | null {
  if (isTypingTarget(event.target)) return null;
  const mod = event.ctrlKey || event.metaKey;
  if (mod && !event.altKey) {
    if (event.code === "KeyZ") return event.shiftKey ? { type: "redo" } : { type: "undo" };
    if (event.code === "KeyY") return { type: "redo" };
    return null;
  }
  if (event.altKey || mod) return null;

  const digit = /^Digit(\d)$|^Numpad(\d)$/.exec(event.code);
  if (digit) return { type: "digit", digit: Number(digit[1] ?? digit[2]) };
  switch (event.code) {
    case "ArrowLeft":
      return { type: "prevNote" };
    case "ArrowRight":
      return { type: "nextNote" };
    case "ArrowUp":
      return event.shiftKey ? { type: "fretUp" } : { type: "stringUp" };
    case "ArrowDown":
      return event.shiftKey ? { type: "fretDown" } : { type: "stringDown" };
    case "Delete":
    case "Backspace":
      return { type: "delete" };
    case "Escape":
      return { type: "deselect" };
  }
  return null;
}

/** Two digits typed within this window form one fret number ("1","2" → 12). */
export const DIGIT_WINDOW_MS = 700;

/** Combine a typed digit with the pending one, if any and still fresh. */
export function typeDigit(
  pending: { value: number; at: number } | null,
  digit: number,
  now: number,
  maxFret: number,
): { fret: number; pending: { value: number; at: number } | null } {
  if (pending && now - pending.at <= DIGIT_WINDOW_MS) {
    const combined = pending.value * 10 + digit;
    if (combined <= maxFret) return { fret: combined, pending: null };
  }
  // A first digit that could still start a valid two-digit fret stays pending.
  const canExtend = digit * 10 <= maxFret && digit > 0;
  return { fret: digit, pending: canExtend ? { value: digit, at: now } : null };
}
