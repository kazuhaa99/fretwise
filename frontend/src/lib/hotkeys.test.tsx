import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HOTKEYS, resolveHotkey, useHotkeys, type HotkeyAction } from "./hotkeys";

function key(init: KeyboardEventInit & { code: string }, target?: HTMLElement): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  (target ?? document.body).dispatchEvent(event);
  return event;
}

function Harness({ handlers }: { handlers: Partial<Record<HotkeyAction, () => void>> }) {
  useHotkeys(handlers);
  return (
    <div>
      <input aria-label="text" />
      <input aria-label="slider" type="range" />
      <button>button</button>
    </div>
  );
}

describe("resolveHotkey", () => {
  const resolve = (init: KeyboardEventInit & { code: string }) => resolveHotkey(new KeyboardEvent("keydown", init));

  it("maps the documented keys", () => {
    expect(resolve({ code: "Space", key: " " })).toBe("togglePlay");
    expect(resolve({ code: "ArrowLeft", key: "ArrowLeft" })).toBe("prevBar");
    expect(resolve({ code: "ArrowRight", key: "ArrowRight" })).toBe("nextBar");
    expect(resolve({ code: "KeyL", key: "l" })).toBe("toggleLoop");
    expect(resolve({ code: "Equal", key: "=" })).toBe("speedUp");
    expect(resolve({ code: "NumpadAdd", key: "+" })).toBe("speedUp");
    expect(resolve({ code: "Minus", key: "-" })).toBe("speedDown");
    expect(resolve({ code: "KeyM", key: "m" })).toBe("toggleMetronome");
    expect(resolve({ code: "KeyC", key: "c" })).toBe("toggleCountIn");
    expect(resolve({ code: "KeyG", key: "g" })).toBe("toggleSource");
    expect(resolve({ code: "Slash", key: "?", shiftKey: true })).toBe("showHelp");
  });

  it("works on a Russian layout (matches the physical key)", () => {
    expect(resolve({ code: "KeyL", key: "д" })).toBe("toggleLoop");
    expect(resolve({ code: "KeyM", key: "ь" })).toBe("toggleMetronome");
  });

  it("leaves browser shortcuts alone", () => {
    expect(resolve({ code: "KeyL", key: "l", ctrlKey: true })).toBeNull();
    expect(resolve({ code: "Minus", key: "-", metaKey: true })).toBeNull();
    expect(resolve({ code: "KeyX", key: "x" })).toBeNull();
  });

  it("documents every action it can produce", () => {
    const documented = new Set(HOTKEYS.map((h) => h.action));
    for (const action of [
      "togglePlay", "prevBar", "nextBar", "toggleLoop", "speedUp", "speedDown",
      "toggleMetronome", "toggleCountIn", "toggleSource", "showHelp",
    ] as HotkeyAction[]) {
      expect(documented.has(action)).toBe(true);
    }
  });
});

describe("useHotkeys", () => {
  it("calls the handler and stops Space from scrolling the page", () => {
    const togglePlay = vi.fn();
    render(<Harness handlers={{ togglePlay }} />);
    const event = key({ code: "Space", key: " " });
    expect(togglePlay).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("ignores keys typed into a text field", () => {
    const toggleLoop = vi.fn();
    const { getByLabelText } = render(<Harness handlers={{ toggleLoop }} />);
    key({ code: "KeyL", key: "l" }, getByLabelText("text"));
    expect(toggleLoop).not.toHaveBeenCalled();
  });

  it("leaves arrow keys to a focused slider but still handles Space", () => {
    const nextBar = vi.fn();
    const togglePlay = vi.fn();
    const { getByLabelText } = render(<Harness handlers={{ nextBar, togglePlay }} />);
    key({ code: "ArrowRight", key: "ArrowRight" }, getByLabelText("slider"));
    expect(nextBar).not.toHaveBeenCalled();
    key({ code: "Space", key: " " }, getByLabelText("slider"));
    expect(togglePlay).toHaveBeenCalled();
  });

  it("doesn't let Space double-fire on a focused button", () => {
    const togglePlay = vi.fn();
    const { getByText } = render(<Harness handlers={{ togglePlay }} />);
    const event = key({ code: "Space", key: " " }, getByText("button"));
    expect(event.defaultPrevented).toBe(true);
    expect(togglePlay).toHaveBeenCalledTimes(1);
  });

  it("ignores keys without a handler and auto-repeat of Space", () => {
    const togglePlay = vi.fn();
    render(<Harness handlers={{ togglePlay }} />);
    const unhandled = key({ code: "KeyL", key: "l" });
    expect(unhandled.defaultPrevented).toBe(false);
    key({ code: "Space", key: " ", repeat: true });
    expect(togglePlay).not.toHaveBeenCalled();
  });
});
