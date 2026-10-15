import { useCallback, useEffect, useMemo, useState } from "react";
import type { JobResult } from "../types";
import { gp5DownloadUrl } from "../api/client";
import { barSpans } from "../lib/barLayout";
import { useHotkeys } from "../lib/hotkeys";
import { PlaybackEngine, type PlaybackState, type Source } from "../lib/playback";
import { tuningLetters, presetFor } from "../lib/tuning";
import PlayerBar from "./PlayerBar";
import ShortcutsDialog from "./ShortcutsDialog";
import TabSheet, { type BarRange } from "./TabSheet";
import { DownloadIcon, KeyboardIcon } from "./icons";
import { Button, IconButton, Segmented } from "./ui";

const PLAYER_INSET = 160; // generous: the phone player is taller than the desktop one

interface Props {
  result: JobResult;
  onReset: () => void;
}

export default function ResultScreen({ result, onReset }: Props) {
  const { tab } = result;
  const [engine] = useState(
    () => new PlaybackEngine(new Audio(), { original: result.originalAudioUrl, guitar: result.guitarAudioUrl }),
  );
  const [trackIndex, setTrackIndex] = useState(0);
  const [rate, setRate] = useState(1);
  const [volume, setVolume] = useState(1);
  const [source, setSource] = useState<Source>("guitar");
  const [countIn, setCountIn] = useState(false);
  const [metronome, setMetronome] = useState(false);
  const [follow, setFollow] = useState(true);
  const [selection, setSelection] = useState<BarRange | null>(null);
  const [loopActive, setLoopActive] = useState(false);
  const [currentBar, setCurrentBar] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    engine.connect();
    return () => engine.disconnect();
  }, [engine]);

  useEffect(() => engine.setBeats(tab.beatTimes ?? [], tab.beatsPerMeasure ?? 4), [engine, tab.beatTimes, tab.beatsPerMeasure]);

  const spans = useMemo(() => barSpans(tab.measureTimes ?? [], tab.durationSeconds), [tab.measureTimes, tab.durationSeconds]);
  const track = tab.tracks[Math.min(trackIndex, tab.tracks.length - 1)];
  const noteCount = track.events.reduce((n, e) => n + e.notes.length, 0);

  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const subscribe = useCallback((listener: (state: PlaybackState) => void) => engine.subscribe(listener), [engine]);
  const seek = useCallback((t: number) => engine.seek(t), [engine]);

  const loopRange = useCallback(
    (range: BarRange) => ({ start: Math.max(0, spans[range.start].start), end: spans[range.end].end }),
    [spans],
  );

  // Keep the engine's loop in sync with the selection while looping.
  useEffect(() => {
    if (!loopActive) engine.setLoop(null);
    else if (selection) engine.setLoop(loopRange(selection));
  }, [engine, loopActive, selection, loopRange]);

  const toggleLoop = () => {
    if (loopActive) {
      setLoopActive(false);
      return;
    }
    if (!selection) setSelection({ start: currentBar, end: currentBar });
    setLoopActive(true);
  };

  const changeRate = (next: number) => setRate(engine.setRate(next));
  const changeVolume = (next: number) => {
    engine.setVolume(next);
    setVolume(next);
  };
  const changeSource = (next: Source) => {
    engine.setSource(next);
    setSource(next);
  };
  const toggleCountIn = () => {
    engine.setCountIn(!countIn);
    setCountIn(!countIn);
  };
  const toggleMetronome = () => {
    engine.setMetronome(!metronome);
    setMetronome(!metronome);
  };
  const goToBar = (bar: number) => {
    const target = Math.max(0, Math.min(spans.length - 1, bar));
    engine.seek(Math.max(0, spans[target].start));
  };

  useHotkeys(
    {
      togglePlay: () => engine.toggle(),
      prevBar: () => goToBar(currentBar - 1),
      nextBar: () => goToBar(currentBar + 1),
      toggleLoop,
      speedUp: () => changeRate(rate + 0.05),
      speedDown: () => changeRate(rate - 0.05),
      toggleMetronome,
      toggleCountIn,
      toggleSource: () => changeSource(source === "guitar" ? "original" : "guitar"),
      showHelp: () => setHelpOpen(true),
    },
    !helpOpen,
  );

  const preset = presetFor(tab.tuning);
  const meta = [
    preset ? preset.label : tuningLetters(tab.tuning),
    tab.capo > 0 ? `капо ${tab.capo}` : null,
    `${tab.tempoBpm} BPM`,
    `${tab.beatsPerMeasure ?? 4}/4`,
  ]
    .filter(Boolean)
    .join(" · ");

  const loopLabel = selection ? (selection.start === selection.end ? `${selection.start + 1}` : `${selection.start + 1}–${selection.end + 1}`) : null;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5" style={{ paddingBottom: PLAYER_INSET }}>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="truncate text-[24px] leading-tight font-bold sm:text-[28px]" title={result.title}>
            {result.title || "Табулатура"}
          </h1>
          <p className="tabular text-[15px] text-muted">{meta}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {tab.tracks.length > 1 && (
            <Segmented
              label="Партия"
              value={String(trackIndex)}
              onChange={(v) => setTrackIndex(Number(v))}
              options={tab.tracks.map((t, i) => ({ value: String(i), label: t.name }))}
            />
          )}
          <a
            href={gp5DownloadUrl(result.job.id)}
            download
            className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line-strong bg-surface px-4 text-[15px] font-semibold text-fg transition-colors hover:border-accent hover:text-accent-strong"
          >
            <DownloadIcon /> Скачать .gp5
          </a>
          <span className="hidden sm:block">
            <IconButton label="Горячие клавиши" onClick={() => setHelpOpen(true)}>
              <KeyboardIcon />
            </IconButton>
          </span>
        </div>
      </div>

      {noteCount === 0 ? (
        <div className="grid justify-items-start gap-3 rounded-lg border border-line bg-surface p-6">
          <h2 className="text-xl font-semibold">В записи не нашлось гитарной партии</h2>
          <p className="max-w-[60ch] text-muted">
            Модель не услышала нот гитары. Так бывает, если гитары в записи нет, она очень тихая или звучит вместе с
            похожими по тембру инструментами. Попробуй другой фрагмент или запись, где гитара слышна отчётливее.
          </p>
          <Button variant="secondary" onClick={onReset}>
            Загрузить другой файл
          </Button>
        </div>
      ) : (
        <div className="rounded-lg border border-line bg-surface px-3 pt-4 pb-1 sm:px-5">
          <TabSheet
            key={trackIndex}
            tab={tab}
            trackIndex={trackIndex}
            subscribe={subscribe}
            onSeek={seek}
            selection={selection}
            loopActive={loopActive}
            onSelect={setSelection}
            follow={follow}
            onCurrentBarChange={setCurrentBar}
            bottomInset={PLAYER_INSET}
          />
        </div>
      )}

      {noteCount > 0 && (
        <PlayerBar
          subscribe={subscribe}
          onToggle={() => engine.toggle()}
          onSeek={seek}
          onPrevBar={() => goToBar(currentBar - 1)}
          onNextBar={() => goToBar(currentBar + 1)}
          currentBar={currentBar + 1}
          barCount={spans.length}
          rate={rate}
          onRateChange={changeRate}
          loopActive={loopActive}
          loopLabel={loopLabel}
          onToggleLoop={toggleLoop}
          countIn={countIn}
          onToggleCountIn={toggleCountIn}
          metronome={metronome}
          onToggleMetronome={toggleMetronome}
          follow={follow}
          onToggleFollow={() => setFollow((f) => !f)}
          source={source}
          onSourceChange={changeSource}
          volume={volume}
          onVolumeChange={changeVolume}
          onShowHelp={() => setHelpOpen(true)}
        />
      )}

      <ShortcutsDialog open={helpOpen} onClose={closeHelp} />
    </div>
  );
}
