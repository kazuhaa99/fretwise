import { useEffect, useRef } from "react";

/**
 * Keyboard shortcuts for the result screen.
 *
 * Letters are matched by `event.code` (the physical key), not `event.key`, so
 * they work the same on a Russian layout — "L" is the key labelled Д there.
 * Shortcuts never fire while the user is typing in a field, and never with
 * Ctrl/Cmd/Alt held (those belong to the browser).
 */
export type HotkeyAction =
  | "togglePlay"
  | "prevBar"
  | "nextBar"
  | "toggleLoop"
  | "speedUp"
  | "speedDown"
  | "toggleMetronome"
  | "toggleCountIn"
  | "toggleSource"
  | "showHelp";

export interface HotkeyInfo {
  action: HotkeyAction;
  keys: string[];
  label: string;
}

/** For the in-app shortcut list — the single source of what is documented. */
export const HOTKEYS: HotkeyInfo[] = [
  { action: "togglePlay", keys: ["Пробел"], label: "Играть / пауза" },
  { action: "prevBar", keys: ["←"], label: "Предыдущий такт" },
  { action: "nextBar", keys: ["→"], label: "Следующий такт" },
  { action: "toggleLoop", keys: ["L"], label: "Петля на такт или выделение; повторно — снять" },
  { action: "speedUp", keys: ["+"], label: "Быстрее на 5%" },
  { action: "speedDown", keys: ["−"], label: "Медленнее на 5%" },
  { action: "toggleMetronome", keys: ["M"], label: "Метроном" },
  { action: "toggleCountIn", keys: ["C"], label: "Отсчёт перед стартом" },
  { action: "toggleSource", keys: ["G"], label: "Оригинал ↔ только гитара" },
  { action: "showHelp", keys: ["?"], label: "Список клавиш" },
];

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    // Sliders and checkboxes don't take text; arrows on a focused slider are
    // still left to the slider (see resolveHotkey).
    return !["range", "checkbox", "radio", "button", "submit"].includes(type);
  }
  return false;
}

export function resolveHotkey(event: KeyboardEvent): HotkeyAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (isTypingTarget(event.target)) return null;
  const onSlider = event.target instanceof HTMLInputElement && event.target.type === "range";

  switch (event.code) {
    case "Space":
      return "togglePlay";
    case "ArrowLeft":
      return onSlider ? null : "prevBar";
    case "ArrowRight":
      return onSlider ? null : "nextBar";
    case "KeyL":
      return "toggleLoop";
    case "KeyM":
      return "toggleMetronome";
    case "KeyC":
      return "toggleCountIn";
    case "KeyG":
      return "toggleSource";
    case "Equal":
    case "NumpadAdd":
      return "speedUp";
    case "Minus":
    case "NumpadSubtract":
      return "speedDown";
    case "Slash":
      return event.shiftKey ? "showHelp" : null;
  }
  if (event.key === "?") return "showHelp";
  if (event.key === "+") return "speedUp";
  return null;
}

/** Bind handlers to window keydown while `enabled`. Handlers may change on
 * every render without re-binding the listener. */
export function useHotkeys(handlers: Partial<Record<HotkeyAction, () => void>>, enabled = true): void {
  const ref = useRef(handlers);
  useEffect(() => {
    ref.current = handlers;
  });

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat && event.code === "Space") return;
      const action = resolveHotkey(event);
      if (!action) return;
      const handler = ref.current[action];
      if (!handler) return;
      // Space would otherwise scroll the page or "click" the focused button too.
      event.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
