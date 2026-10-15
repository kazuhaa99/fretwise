import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JobResult, TabTrack } from "../types";
import { barSpans } from "../lib/barLayout";
import { useHotkeys } from "../lib/hotkeys";
import { PlaybackEngine, type PlaybackState, type Source } from "../lib/playback";
import { tuningLetters, presetFor } from "../lib/tuning";
import { resolveEditKey, typeDigit } from "../lib/editKeys";
import { addNote, deleteNote, editContext, getNote, moveString, neighbourNote, setFret, shiftFret, type NoteRef } from "../lib/tabEdit";
import { useTabEditor } from "../lib/useTabEditor";
import { errorMessage, refingerEvents } from "../api/client";
import EditToolbar from "./EditToolbar";
import PlayerBar from "./PlayerBar";
import ShortcutsDialog from "./ShortcutsDialog";
import TabSheet, { type BarRange } from "./TabSheet";
import { KeyboardIcon, PencilIcon } from "./icons";
import DownloadMenu from "./DownloadMenu";
import { Button, IconButton, Segmented } from "./ui";

const PLAYER_INSET = 160; // generous: the phone player is taller than the desktop one

interface Props {
  result: JobResult;
  onReset: () => void;
}

export default function ResultScreen({ result, onReset }: Props) {
  const editor = useTabEditor(result.job.id, result);
  const tab = useMemo(() => ({ ...result.tab, tracks: editor.tracks }), [result.tab, editor.tracks]);
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
  const [editing, setEditing] = useState(false);
  const [selectedRaw, setSelected] = useState<NoteRef | null>(null);
  const pendingDigit = useRef<{ value: number; at: number } | null>(null);
  const ctx = useMemo(() => editContext(tab.tuning, tab.capo), [tab.tuning, tab.capo]);

  useEffect(() => {
    engine.connect();
    return () => engine.disconnect();
  }, [engine]);

  useEffect(() => engine.setBeats(tab.beatTimes ?? [], tab.beatsPerMeasure ?? 4), [engine, tab.beatTimes, tab.beatsPerMeasure]);

  const spans = useMemo(() => barSpans(tab.measureTimes ?? [], tab.durationSeconds), [tab.measureTimes, tab.durationSeconds]);
  const track = tab.tracks[Math.min(trackIndex, tab.tracks.length - 1)];
  const noteCount = track.events.reduce((n, e) => n + e.notes.length, 0);
  // Undo/redo or deleting can leave the selection pointing nowhere.
  const selected = getNote(track, selectedRaw) ? selectedRaw : null;
  const selectedNote = getNote(track, selected);

  // -- editing ---------------------------------------------------------------

  const commitTrack = useCallback(
    (next: TabTrack | null, nextSelected?: NoteRef | null) => {
      if (!next) return false;
      if (next !== track) editor.commit(editor.tracks.map((t, i) => (i === trackIndex ? next : t)));
      if (nextSelected !== undefined) setSelected(nextSelected);
      return true;
    },
    [editor, track, trackIndex],
  );

  const selectNote = useCallback(
    (ref: NoteRef | null) => {
      setSelected(ref);
      pendingDigit.current = null;
      const event = ref ? track.events[ref.event] : null;
      // Show where the note is in the recording (without starting playback).
      if (event && !engine.state.playing) engine.seek(event.time);
    },
    [track, engine],
  );

  const editActions = {
    setFret: (fret: number) => selected && commitTrack(setFret(track, selected, fret, ctx)),
    shiftFret: (delta: number) => selected && commitTrack(shiftFret(track, selected, delta, ctx)),
    moveString: (dir: 1 | -1) => {
      if (!selected) return;
      const moved = moveString(track, selected, dir, ctx);
      if (moved) commitTrack(moved.track, moved.ref);
    },
    remove: () => {
      if (!selected) return;
      const result = deleteNote(track, selected);
      commitTrack(result.track, result.ref);
    },
    add: (time: number, string: number) => {
      const added = addNote(track, time, string, ctx);
      if (added) {
        commitTrack(added.track);
        selectNote(added.ref);
      }
    },
  };

  // "Переложить в позицию": re-finger the selected bars on the server and
  // commit the result as one undoable edit.
  const [refingering, setRefingering] = useState(false);
  const [refingerError, setRefingerError] = useState<string | null>(null);
  const refingerSelection = async (position: number) => {
    if (!selection) return;
    const start = spans[selection.start].start - 0.03;
    const end = spans[selection.end].end - 0.03;
    const indices = track.events.map((e, i) => (e.time >= start && e.time < end ? i : -1)).filter((i) => i >= 0);
    if (!indices.length) return;
    setRefingering(true);
    setRefingerError(null);
    try {
      const moved = await refingerEvents(result.job.id, indices.map((i) => track.events[i]), position, "avoid");
      const events = track.events.slice();
      indices.forEach((index, k) => {
        events[index] = moved[k];
      });
      commitTrack({ ...track, events }, null);
    } catch (e) {
      setRefingerError(errorMessage(e, "Не получилось переложить такты"));
    } finally {
      setRefingering(false);
    }
  };

  useEffect(() => {
    if (!editing || helpOpen) return;
    const onKey = (e: KeyboardEvent) => {
      const action = resolveEditKey(e);
      if (!action) return;
      switch (action.type) {
        case "undo":
          editor.undo();
          break;
        case "redo":
          editor.redo();
          break;
        case "prevNote":
          selectNote(neighbourNote(track, selected, -1));
          break;
        case "nextNote":
          selectNote(neighbourNote(track, selected, 1));
          break;
        case "deselect":
          if (!selected) return;
          setSelected(null);
          break;
        default: {
          if (!selected) return;
          if (action.type === "digit") {
            const step = typeDigit(pendingDigit.current, action.digit, performance.now(), ctx.maxFret);
            pendingDigit.current = step.pending;
            editActions.setFret(step.fret);
          } else if (action.type === "stringUp") editActions.moveString(1);
          else if (action.type === "stringDown") editActions.moveString(-1);
          else if (action.type === "fretUp") editActions.shiftFret(1);
          else if (action.type === "fretDown") editActions.shiftFret(-1);
          else if (action.type === "delete") editActions.remove();
        }
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

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
      // In edit mode ← / → walk between notes instead (see the edit keys above).
      prevBar: editing ? undefined : () => goToBar(currentBar - 1),
      nextBar: editing ? undefined : () => goToBar(currentBar + 1),
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
          <p className="tabular text-[15px] text-muted">
            {meta}
            {editor.edited && <span className="ml-2 rounded-full border border-warm px-2 py-0.5 text-[12px] font-semibold text-warm">изменено</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {tab.tracks.length > 1 && (
            <Segmented
              label="Партия"
              value={String(trackIndex)}
              onChange={(v) => {
                setTrackIndex(Number(v));
                setSelected(null);
              }}
              options={tab.tracks.map((t, i) => ({ value: String(i), label: t.name }))}
            />
          )}
          <Button
            variant={editing ? "primary" : "secondary"}
            icon={<PencilIcon />}
            aria-pressed={editing}
            onClick={() => {
              setEditing((e) => !e);
              setSelected(null);
            }}
          >
            {editing ? "Правка" : "Править"}
          </Button>
          <DownloadMenu result={result} />
          <span className="hidden sm:block">
            <IconButton label="Горячие клавиши" onClick={() => setHelpOpen(true)}>
              <KeyboardIcon />
            </IconButton>
          </span>
        </div>
      </div>

      {editing && (
        <EditToolbar
          note={selectedNote}
          stringCount={tab.tuning.length}
          maxFret={ctx.maxFret}
          canUndo={editor.canUndo}
          canRedo={editor.canRedo}
          canMoveUp={selected !== null && moveString(track, selected, 1, ctx) !== null}
          canMoveDown={selected !== null && moveString(track, selected, -1, ctx) !== null}
          edited={editor.edited}
          save={editor.save}
          onUndo={editor.undo}
          onRedo={editor.redo}
          onSetFret={(fret) => editActions.setFret(fret)}
          onShiftFret={(delta) => editActions.shiftFret(delta)}
          onMoveString={(dir) => editActions.moveString(dir)}
          onDelete={editActions.remove}
          onRevert={() => {
            setSelected(null);
            void editor.revert();
          }}
          onDone={() => {
            setEditing(false);
            setSelected(null);
          }}
          selectionLabel={loopLabel}
          onRefinger={(position) => void refingerSelection(position)}
          refingering={refingering}
        />
      )}
      {editing && refingerError && (
        <p role="alert" className="text-[14px] text-danger">
          {refingerError}
        </p>
      )}

      {noteCount === 0 && !editing ? (
        <div className="grid justify-items-start gap-3 rounded-lg border border-line bg-surface p-6">
          <h2 className="text-xl font-semibold">В записи не нашлось гитарной партии</h2>
          <p className="max-w-[60ch] text-muted">
            Модель не услышала нот гитары. Так бывает, если гитары в записи нет, она очень тихая или звучит вместе с
            похожими по тембру инструментами. Попробуй другой фрагмент или запись, где гитара слышна отчётливее.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={onReset}>
              Загрузить другой файл
            </Button>
            <Button variant="ghost" icon={<PencilIcon />} onClick={() => setEditing(true)}>
              Расставить ноты вручную
            </Button>
          </div>
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
            editing={editing}
            selectedNote={selected}
            onSelectNote={selectNote}
            onAddNote={editActions.add}
          />
        </div>
      )}

      {(noteCount > 0 || editing) && (
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
