import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AudioPlayer, { type AudioPlayerHandle } from "./AudioPlayer";

// jsdom has no media pipeline: give <audio> a controllable clock and state.
function installFakeMedia() {
  const state = new WeakMap<HTMLMediaElement, { time: number; paused: boolean }>();
  const get = (el: HTMLMediaElement) => {
    if (!state.has(el)) state.set(el, { time: 0, paused: true });
    return state.get(el)!;
  };
  Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
    configurable: true,
    get() { return get(this).time; },
    set(v: number) { get(this).time = v; },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "paused", {
    configurable: true,
    get() { return get(this).paused; },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "duration", { configurable: true, get: () => 120 });
  const load = vi.fn();
  HTMLMediaElement.prototype.load = load;
  HTMLMediaElement.prototype.play = vi.fn(function (this: HTMLMediaElement) {
    get(this).paused = false;
    this.dispatchEvent(new Event("play"));
    return Promise.resolve();
  });
  HTMLMediaElement.prototype.pause = vi.fn(function (this: HTMLMediaElement) {
    get(this).paused = true;
    this.dispatchEvent(new Event("pause"));
  });
  return { load };
}

describe("AudioPlayer", () => {
  let media: ReturnType<typeof installFakeMedia>;
  beforeEach(() => {
    media = installFakeMedia();
  });

  it("sets the source once, not twice", () => {
    render(<AudioPlayer originalUrl="/o.wav" guitarUrl="/g.wav" />);
    const audio = screen.getByTestId("audio") as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe("/g.wav");
    expect(media.load).toHaveBeenCalledTimes(1);
  });

  it("switches between guitar and original without losing the position or play state", async () => {
    render(<AudioPlayer originalUrl="/o.wav" guitarUrl="/g.wav" />);
    const audio = screen.getByTestId("audio") as HTMLAudioElement;
    fireEvent.loadedMetadata(audio);

    await act(async () => {
      await audio.play();
    });
    audio.currentTime = 42.5;

    fireEvent.click(screen.getByRole("button", { name: "Оригинал" }));
    expect(audio.getAttribute("src")).toBe("/o.wav");
    expect(screen.getByRole("button", { name: "Оригинал" })).toHaveAttribute("aria-pressed", "true");

    // A freshly loaded source starts at 0 until its metadata arrives.
    audio.currentTime = 0;
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBe(42.5);
    expect(audio.play).toHaveBeenCalledTimes(2);
  });

  it("notifies subscribers on seek", () => {
    const ref = createRef<AudioPlayerHandle>();
    render(<AudioPlayer ref={ref} originalUrl="/o.wav" guitarUrl="/g.wav" />);
    const listener = vi.fn();
    const unsubscribe = ref.current!.subscribe(listener);
    act(() => ref.current!.seekTo(12));
    expect(listener).toHaveBeenLastCalledWith(12, false);
    unsubscribe();
    act(() => ref.current!.seekTo(13));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
