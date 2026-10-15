/**
 * Playback engine for the result screen, independent of React.
 *
 * One <audio> element plays either the original mix or the isolated guitar.
 * On top of it:
 * - **Clock.** While playing, `currentTime` is sampled every animation frame
 *   and pushed to subscribers (the tab sheet's playhead) — `timeupdate` alone
 *   fires ~4×/s, which made the cursor stutter.
 * - **Speed 25–150% without changing pitch:** `playbackRate` with
 *   `preservesPitch`, which browsers implement natively (time-stretching).
 * - **Loop** over a range of bars, checked every frame.
 * - **Metronome and count-in** as short Web Audio clicks on the beat grid
 *   from the backend (`beatTimes`), scheduled a little ahead on the audio
 *   clock so they stay tight even when the main thread is busy.
 * - **Source switching** that keeps position and play state.
 *
 * Lifecycle: `connect()` / `disconnect()` (not constructor/dispose), so React
 * StrictMode's mount → unmount → mount in development reuses the instance.
 */

export type Source = "original" | "guitar";

export interface PlaybackState {
  time: number;
  duration: number;
  playing: boolean;
  /** Count-in clicks are sounding; audio starts when they end. */
  counting: boolean;
}

export type PlaybackListener = (state: PlaybackState) => void;

export interface LoopRange {
  start: number;
  end: number;
}

export const MIN_RATE = 0.25;
export const MAX_RATE = 1.5;

/** What makes the clicks. Swappable so tests don't need Web Audio. */
export interface Clicker {
  /** Current time on the clock `click` schedules against, in seconds. */
  now(): number;
  /** Start (resume) the audio clock; must be called from a user gesture. */
  resume(): Promise<void> | void;
  click(at: number, accent: boolean): void;
  close?(): void;
}

class WebAudioClicker implements Clicker {
  private ctx: AudioContext | null = null;

  private context(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext();
    return this.ctx;
  }

  now(): number {
    return this.context().currentTime;
  }

  resume(): Promise<void> {
    return this.context().resume();
  }

  click(at: number, accent: boolean): void {
    const ctx = this.context();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = accent ? 1760 : 1320;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(accent ? 0.5 : 0.3, at + 0.002);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.05);
    osc.connect(gain).connect(ctx.destination);
    osc.start(at);
    osc.stop(at + 0.06);
  }

  close(): void {
    void this.ctx?.close();
    this.ctx = null;
  }
}

/** Minimal surface of HTMLAudioElement the engine uses (so tests can fake it). */
export type MediaLike = Pick<
  HTMLAudioElement,
  | "currentTime"
  | "duration"
  | "paused"
  | "playbackRate"
  | "volume"
  | "src"
  | "play"
  | "pause"
  | "load"
  | "addEventListener"
  | "removeEventListener"
  | "getAttribute"
> & { preservesPitch?: boolean; preload?: string };

const LOOKAHEAD_SECONDS = 0.12;

export class PlaybackEngine {
  private readonly media: MediaLike;
  private readonly urls: Record<Source, string>;
  private readonly clicker: Clicker;
  private readonly raf: (cb: FrameRequestCallback) => number;
  private readonly caf: (id: number) => void;

  private listeners = new Set<PlaybackListener>();
  private frame: number | null = null;
  private countInTimer: ReturnType<typeof setTimeout> | null = null;
  private resumeAfterLoad: { at: number; play: boolean } | null = null;
  private connected = false;

  private beats: number[] = [];
  private beatsPerMeasure = 4;
  private nextBeat = 0;

  source: Source = "guitar";
  rate = 1;
  volume = 1;
  loop: LoopRange | null = null;
  metronome = false;
  countIn = false;
  private counting = false;

  constructor(
    media: MediaLike,
    urls: Record<Source, string>,
    options: {
      clicker?: Clicker;
      raf?: (cb: FrameRequestCallback) => number;
      caf?: (id: number) => void;
    } = {},
  ) {
    this.media = media;
    this.urls = urls;
    this.clicker = options.clicker ?? new WebAudioClicker();
    this.raf = options.raf ?? ((cb) => requestAnimationFrame(cb));
    this.caf = options.caf ?? ((id) => cancelAnimationFrame(id));
  }

  // -- lifecycle -------------------------------------------------------------

  connect(): void {
    if (this.connected) return;
    this.connected = true;
    const m = this.media;
    m.addEventListener("play", this.onPlay);
    m.addEventListener("pause", this.onPause);
    m.addEventListener("ended", this.onPause);
    m.addEventListener("seeked", this.onSeeked);
    m.addEventListener("timeupdate", this.onTimeUpdate);
    m.addEventListener("loadedmetadata", this.onLoadedMetadata);
    m.preload = "metadata";
    this.applyRate();
    m.volume = this.volume;
    if (!m.getAttribute("src")) {
      m.src = this.urls[this.source];
      m.load();
    }
  }

  disconnect(): void {
    if (!this.connected) return;
    this.connected = false;
    this.cancelCountIn();
    this.stopFrames();
    if (!this.media.paused) this.media.pause();
    const m = this.media;
    m.removeEventListener("play", this.onPlay);
    m.removeEventListener("pause", this.onPause);
    m.removeEventListener("ended", this.onPause);
    m.removeEventListener("seeked", this.onSeeked);
    m.removeEventListener("timeupdate", this.onTimeUpdate);
    m.removeEventListener("loadedmetadata", this.onLoadedMetadata);
  }

  // -- state -----------------------------------------------------------------

  get state(): PlaybackState {
    const duration = Number.isFinite(this.media.duration) ? this.media.duration : 0;
    return { time: this.media.currentTime, duration, playing: !this.media.paused, counting: this.counting };
  }

  subscribe(listener: PlaybackListener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(): void {
    const state = this.state;
    for (const listener of this.listeners) listener(state);
  }

  setBeats(beatTimes: number[], beatsPerMeasure: number): void {
    this.beats = beatTimes;
    this.beatsPerMeasure = Math.max(1, beatsPerMeasure);
    this.resetMetronome(this.media.currentTime);
  }

  // -- transport -------------------------------------------------------------

  async play(): Promise<void> {
    if (this.counting || !this.media.paused) return;
    const t = this.media.currentTime;
    if (this.loop && (t < this.loop.start || t >= this.loop.end)) this.seek(this.loop.start);
    if (this.countIn || this.metronome) await this.clicker.resume();
    if (this.countIn) this.startCountIn();
    else await this.startMedia();
  }

  pause(): void {
    if (this.counting) {
      this.cancelCountIn();
      this.emit();
      return;
    }
    this.media.pause();
  }

  toggle(): void {
    if (this.counting || !this.media.paused) this.pause();
    else void this.play();
  }

  seek(seconds: number): void {
    const duration = Number.isFinite(this.media.duration) ? this.media.duration : Infinity;
    const t = Math.max(0, Math.min(seconds, duration));
    this.media.currentTime = t;
    this.resetMetronome(t);
    this.emit();
  }

  private async startMedia(): Promise<void> {
    try {
      await this.media.play();
    } catch {
      // Autoplay refusal or a source error: stay paused; the UI shows play.
      this.emit();
    }
  }

  // -- settings --------------------------------------------------------------

  setRate(rate: number): number {
    this.rate = Math.round(Math.min(MAX_RATE, Math.max(MIN_RATE, rate)) * 100) / 100;
    this.applyRate();
    this.resetMetronome(this.media.currentTime);
    return this.rate;
  }

  private applyRate(): void {
    const m = this.media as MediaLike & { webkitPreservesPitch?: boolean; mozPreservesPitch?: boolean };
    m.preservesPitch = true;
    m.webkitPreservesPitch = true;
    m.mozPreservesPitch = true;
    m.playbackRate = this.rate;
  }

  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume));
    this.media.volume = this.volume;
  }

  setLoop(loop: LoopRange | null): void {
    this.loop = loop && loop.end > loop.start ? loop : null;
    if (this.loop) {
      const t = this.media.currentTime;
      if (t < this.loop.start || t >= this.loop.end) this.seek(this.loop.start);
    }
  }

  setMetronome(on: boolean): void {
    this.metronome = on;
    if (on) void this.clicker.resume();
    this.resetMetronome(this.media.currentTime);
  }

  setCountIn(on: boolean): void {
    this.countIn = on;
  }

  /** Switch original ↔ guitar, carrying position and play state over. The new
   * file must load its metadata before `currentTime` can be set, so that
   * happens in `onLoadedMetadata`. */
  setSource(source: Source): void {
    if (source === this.source) return;
    this.source = source;
    this.resumeAfterLoad = { at: this.media.currentTime, play: !this.media.paused };
    this.media.src = this.urls[source];
    this.media.load();
  }

  // -- count-in & metronome --------------------------------------------------

  /** Beat period (seconds of music) around time `t`. */
  beatPeriodAt(t: number): number {
    const beats = this.beats;
    if (beats.length >= 2) {
      let i = 0;
      while (i < beats.length - 2 && beats[i + 1] <= t) i++;
      return beats[i + 1] - beats[i];
    }
    return 0.5;
  }

  private startCountIn(): void {
    this.counting = true;
    const period = this.beatPeriodAt(this.media.currentTime) / this.rate;
    const start = this.clicker.now() + 0.05;
    for (let i = 0; i < this.beatsPerMeasure; i++) this.clicker.click(start + i * period, i === 0);
    this.emit();
    this.countInTimer = setTimeout(() => {
      this.countInTimer = null;
      this.counting = false;
      void this.startMedia();
    }, (0.05 + this.beatsPerMeasure * period) * 1000);
  }

  private cancelCountIn(): void {
    if (this.countInTimer) clearTimeout(this.countInTimer);
    this.countInTimer = null;
    this.counting = false;
  }

  private resetMetronome(t: number): void {
    let i = 0;
    while (i < this.beats.length && this.beats[i] < t - 1e-3) i++;
    this.nextBeat = i;
  }

  private scheduleClicks(t: number): void {
    if (!this.metronome) return;
    const horizon = t + LOOKAHEAD_SECONDS * this.rate;
    const now = this.clicker.now();
    while (this.nextBeat < this.beats.length && this.beats[this.nextBeat] < horizon) {
      const beat = this.beats[this.nextBeat];
      if (beat >= t - 0.02) {
        this.clicker.click(now + Math.max(0, beat - t) / this.rate, this.nextBeat % this.beatsPerMeasure === 0);
      }
      this.nextBeat++;
    }
  }

  // -- frame loop ------------------------------------------------------------

  private tick = (): void => {
    this.frame = null;
    if (this.media.paused) return;
    let t = this.media.currentTime;
    if (this.loop && t >= this.loop.end - 0.005) {
      t = this.loop.start;
      this.media.currentTime = t;
      this.resetMetronome(t);
    }
    this.scheduleClicks(t);
    this.emit();
    this.frame = this.raf(this.tick);
  };

  private startFrames(): void {
    this.stopFrames();
    this.frame = this.raf(this.tick);
  }

  private stopFrames(): void {
    if (this.frame !== null) this.caf(this.frame);
    this.frame = null;
  }

  // -- media events ----------------------------------------------------------

  private onPlay = (): void => {
    this.resetMetronome(this.media.currentTime);
    this.startFrames();
    this.emit();
  };

  private onPause = (): void => {
    this.stopFrames();
    this.emit();
  };

  private onSeeked = (): void => {
    this.resetMetronome(this.media.currentTime);
    this.emit();
  };

  private onTimeUpdate = (): void => {
    if (this.media.paused) this.emit();
  };

  private onLoadedMetadata = (): void => {
    this.applyRate();
    this.media.volume = this.volume;
    const resume = this.resumeAfterLoad;
    this.resumeAfterLoad = null;
    if (resume) {
      const duration = Number.isFinite(this.media.duration) ? this.media.duration : resume.at;
      this.media.currentTime = Math.min(resume.at, duration);
      this.resetMetronome(this.media.currentTime);
      if (resume.play) void this.startMedia();
    }
    this.emit();
  };
}
