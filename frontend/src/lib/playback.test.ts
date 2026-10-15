import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlaybackEngine, type Clicker, type MediaLike } from "./playback";

class FakeMedia extends EventTarget {
  currentTime = 0;
  duration = 120;
  paused = true;
  playbackRate = 1;
  volume = 1;
  preservesPitch?: boolean;
  preload = "";
  private srcValue = "";
  loads = 0;
  get src() {
    return this.srcValue;
  }
  set src(v: string) {
    this.srcValue = v;
  }
  getAttribute(name: string) {
    return name === "src" && this.srcValue ? this.srcValue : null;
  }
  load() {
    this.loads++;
  }
  play = vi.fn(async () => {
    this.paused = false;
    this.dispatchEvent(new Event("play"));
  });
  pause = vi.fn(() => {
    this.paused = true;
    this.dispatchEvent(new Event("pause"));
  });
}

class FakeClicker implements Clicker {
  time = 10;
  clicks: { at: number; accent: boolean }[] = [];
  now() {
    return this.time;
  }
  resume() {}
  click(at: number, accent: boolean) {
    this.clicks.push({ at, accent });
  }
}

function setup() {
  const media = new FakeMedia();
  const clicker = new FakeClicker();
  const frames: FrameRequestCallback[] = [];
  const engine = new PlaybackEngine(media as unknown as MediaLike, { original: "/o.wav", guitar: "/g.wav" }, {
    clicker,
    raf: (cb) => frames.push(cb),
    caf: () => {},
  });
  engine.connect();
  const frame = () => {
    const pending = frames.splice(0);
    pending.forEach((cb) => cb(0));
  };
  return { media, clicker, engine, frame };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("PlaybackEngine", () => {
  it("sets the guitar source once on connect", () => {
    const { media, engine } = setup();
    expect(media.src).toBe("/g.wav");
    engine.connect(); // StrictMode re-mount
    expect(media.loads).toBe(1);
  });

  it("streams the position every frame while playing", async () => {
    const { media, engine, frame } = setup();
    const seen: number[] = [];
    engine.subscribe((s) => seen.push(s.time));
    await engine.play();
    media.currentTime = 1.25;
    frame();
    media.currentTime = 1.27;
    frame();
    expect(seen.slice(-2)).toEqual([1.25, 1.27]);
  });

  it("changes speed without changing pitch, within 25–150%", () => {
    const { media, engine } = setup();
    expect(engine.setRate(0.75)).toBe(0.75);
    expect(media.playbackRate).toBe(0.75);
    expect(media.preservesPitch).toBe(true);
    expect(engine.setRate(0.1)).toBe(0.25);
    expect(engine.setRate(3)).toBe(1.5);
  });

  it("loops a range of bars", async () => {
    const { media, engine, frame } = setup();
    engine.setLoop({ start: 4, end: 8 });
    expect(media.currentTime).toBe(4); // jumped into the loop
    await engine.play();
    media.currentTime = 8.01;
    frame();
    expect(media.currentTime).toBe(4);
  });

  it("switches source without losing position or play state", async () => {
    const { media, engine } = setup();
    await engine.play();
    media.currentTime = 42.5;
    engine.setSource("original");
    expect(media.src).toBe("/o.wav");
    media.currentTime = 0; // the new file starts at 0 until metadata loads
    media.paused = true;
    media.dispatchEvent(new Event("loadedmetadata"));
    expect(media.currentTime).toBe(42.5);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  it("counts in one bar before starting, at the current speed", async () => {
    const { media, clicker, engine } = setup();
    engine.setBeats([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5], 4);
    engine.setRate(0.5);
    engine.setCountIn(true);
    const states: boolean[] = [];
    engine.subscribe((s) => states.push(s.counting));
    await engine.play();
    expect(clicker.clicks).toHaveLength(4);
    expect(clicker.clicks[0].accent).toBe(true);
    expect(clicker.clicks[1].at - clicker.clicks[0].at).toBeCloseTo(1.0); // 0.5 s beat at half speed
    expect(media.play).not.toHaveBeenCalled();
    expect(states.at(-1)).toBe(true);
    vi.advanceTimersByTime(4100);
    expect(media.play).toHaveBeenCalledTimes(1);
  });

  it("pausing during the count-in cancels it", async () => {
    const { media, engine } = setup();
    engine.setCountIn(true);
    await engine.play();
    engine.pause();
    vi.advanceTimersByTime(5000);
    expect(media.play).not.toHaveBeenCalled();
    expect(engine.state.counting).toBe(false);
  });

  it("clicks the metronome on beats, accenting the downbeat", async () => {
    const { media, clicker, engine, frame } = setup();
    engine.setBeats([0, 0.5, 1, 1.5, 2, 2.5], 4);
    engine.setMetronome(true);
    await engine.play();
    for (const t of [0, 0.45, 0.95, 1.45, 1.95]) {
      media.currentTime = t;
      frame();
    }
    expect(clicker.clicks.map((c) => c.accent)).toEqual([true, false, false, false, true]);
    // each beat clicked once, scheduled slightly ahead of when it sounds
    expect(clicker.clicks[1].at).toBeCloseTo(clicker.time + 0.05);
  });

  it("restarts the metronome from the right beat after a seek", async () => {
    const { media, clicker, engine, frame } = setup();
    engine.setBeats([0, 0.5, 1, 1.5, 2, 2.5], 4);
    engine.setMetronome(true);
    await engine.play();
    engine.seek(1.98);
    frame();
    expect(clicker.clicks).toHaveLength(1);
    expect(clicker.clicks[0].accent).toBe(true); // beat 4 = next bar's downbeat
    expect(media.currentTime).toBe(1.98);
  });

  it("disconnect stops playback and listeners", async () => {
    const { media, engine } = setup();
    const listener = vi.fn();
    engine.subscribe(listener);
    await engine.play();
    engine.disconnect();
    expect(media.paused).toBe(true);
    const calls = listener.mock.calls.length;
    media.dispatchEvent(new Event("seeked"));
    expect(listener.mock.calls.length).toBe(calls);
  });
});
