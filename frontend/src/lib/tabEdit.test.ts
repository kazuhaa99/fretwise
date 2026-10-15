import { describe, expect, it } from "vitest";
import type { TabTrack } from "../types";
import { historyReducer, initHistory } from "./editHistory";
import { resolveEditKey, typeDigit } from "./editKeys";
import { addNote, deleteNote, editContext, moveString, neighbourNote, setFret, shiftFret, snapToGrid } from "./tabEdit";

const ctx = editContext(["E2", "A2", "D3", "G3", "B3", "E4"], 0);
const track = (): TabTrack => ({
  name: "Guitar",
  events: [
    { time: 0.5, duration: 0.4, notes: [{ string: 0, fret: 3 }, { string: 1, fret: 5 }] },
    { time: 1.0, duration: 0.4, notes: [{ string: 2, fret: 7, technique: "vibrato" }] },
  ],
});

describe("tab edits", () => {
  it("sets and shifts frets within the neck, sharing untouched events", () => {
    const t = track();
    const edited = setFret(t, { event: 1, note: 0 }, 12, ctx)!;
    expect(edited.events[1].notes[0]).toEqual({ string: 2, fret: 12, technique: "vibrato" });
    expect(edited.events[0]).toBe(t.events[0]);
    expect(t.events[1].notes[0].fret).toBe(7); // original untouched
    expect(setFret(t, { event: 0, note: 0 }, 23, ctx)).toBeNull();
    expect(shiftFret(t, { event: 0, note: 0 }, -4, ctx)).toBeNull();
    expect(shiftFret(t, { event: 0, note: 0 }, 1, ctx)!.events[0].notes[0].fret).toBe(4);
  });

  it("respects the capo", () => {
    const capo5 = editContext(["E2", "A2", "D3", "G3", "B3", "E4"], 5);
    expect(setFret(track(), { event: 0, note: 0 }, 17, capo5)).not.toBeNull();
    expect(setFret(track(), { event: 0, note: 0 }, 18, capo5)).toBeNull();
  });

  it("moves a note to the next string keeping its pitch", () => {
    // D3 string fret 7 (A3) → G3 string fret 2
    const moved = moveString(track(), { event: 1, note: 0 }, 1, ctx)!;
    expect(moved.track.events[1].notes[0]).toMatchObject({ string: 3, fret: 2 });
    // G string 2 → B string would need fret -2: not possible
    expect(moveString(moved.track, { event: 1, note: 0 }, 1, ctx)).toBeNull();
    // string already used in the chord
    expect(moveString(track(), { event: 0, note: 0 }, 1, ctx)).toBeNull();
    // B3 → G3 string uses fret +4
    const t: TabTrack = { name: "g", events: [{ time: 0, duration: 1, notes: [{ string: 4, fret: 0 }] }] };
    expect(moveString(t, { event: 0, note: 0 }, -1, ctx)!.track.events[0].notes[0]).toMatchObject({ string: 3, fret: 4 });
  });

  it("deletes notes and whole chords, selecting a sensible neighbour", () => {
    const one = deleteNote(track(), { event: 0, note: 1 });
    expect(one.track.events[0].notes).toHaveLength(1);
    expect(one.ref).toEqual({ event: 0, note: 0 });
    const chordGone = deleteNote(track(), { event: 1, note: 0 });
    expect(chordGone.track.events).toHaveLength(1);
    expect(chordGone.ref).toEqual({ event: 0, note: 0 });
    const empty = deleteNote({ name: "g", events: [track().events[1]] }, { event: 0, note: 0 });
    expect(empty.track.events).toHaveLength(0);
    expect(empty.ref).toBeNull();
  });

  it("adds a note into an existing chord or as a new event in time order", () => {
    const intoChord = addNote(track(), 0.51, 4, ctx, 2)!;
    expect(intoChord.track.events).toHaveLength(2);
    expect(intoChord.track.events[0].notes).toHaveLength(3);
    expect(intoChord.ref).toEqual({ event: 0, note: 2 });

    const occupied = addNote(track(), 0.5, 0, ctx)!;
    expect(occupied.track).toEqual(track()); // just selects the existing note
    expect(occupied.ref).toEqual({ event: 0, note: 0 });

    const between = addNote(track(), 0.75, 5, ctx)!;
    expect(between.track.events.map((e) => e.time)).toEqual([0.5, 0.75, 1.0]);
    expect(between.ref).toEqual({ event: 1, note: 0 });
    expect(between.track.events[1].duration).toBeLessThanOrEqual(0.25);

    expect(addNote(track(), 2, 6, ctx)).toBeNull();
  });

  it("walks notes chronologically, low string first within a chord", () => {
    const t = track();
    expect(neighbourNote(t, null, 1)).toEqual({ event: 0, note: 0 });
    expect(neighbourNote(t, { event: 0, note: 0 }, 1)).toEqual({ event: 0, note: 1 });
    expect(neighbourNote(t, { event: 0, note: 1 }, 1)).toEqual({ event: 1, note: 0 });
    expect(neighbourNote(t, { event: 1, note: 0 }, 1)).toEqual({ event: 1, note: 0 }); // stays at the end
  });

  it("snaps new notes to sixteenths of the beat grid", () => {
    const beats = [0, 0.5, 1, 1.5];
    expect(snapToGrid(0.3, beats)).toBe(0.25);
    expect(snapToGrid(0.69, beats)).toBe(0.75);
    expect(snapToGrid(0.66, beats)).toBe(0.625);
    expect(snapToGrid(0.33, [])).toBe(0.375);
  });
});

describe("undo / redo", () => {
  it("steps back and forward and drops the redo branch on a new edit", () => {
    const a = [track()];
    const b = [setFret(a[0], { event: 0, note: 0 }, 5, ctx)!];
    const c = [setFret(b[0], { event: 0, note: 0 }, 7, ctx)!];
    let h = initHistory(a);
    h = historyReducer(h, { type: "commit", tracks: b });
    h = historyReducer(h, { type: "commit", tracks: c });
    h = historyReducer(h, { type: "undo" });
    expect(h.present).toBe(b);
    h = historyReducer(h, { type: "undo" });
    expect(h.present).toBe(a);
    expect(historyReducer(h, { type: "undo" })).toBe(h); // nothing left
    h = historyReducer(h, { type: "redo" });
    expect(h.present).toBe(b);
    h = historyReducer(h, { type: "commit", tracks: a });
    expect(h.future).toHaveLength(0);
    expect(historyReducer(h, { type: "reset", tracks: c })).toEqual({ past: [], present: c, future: [] });
  });
});

describe("edit keys", () => {
  const key = (init: KeyboardEventInit & { code: string }) => resolveEditKey(new KeyboardEvent("keydown", init));

  it("maps editing keys, including Ctrl/Cmd+Z and Ctrl+Shift+Z", () => {
    expect(key({ code: "Digit7" })).toEqual({ type: "digit", digit: 7 });
    expect(key({ code: "Numpad0" })).toEqual({ type: "digit", digit: 0 });
    expect(key({ code: "ArrowUp" })).toEqual({ type: "stringUp" });
    expect(key({ code: "ArrowUp", shiftKey: true })).toEqual({ type: "fretUp" });
    expect(key({ code: "ArrowLeft" })).toEqual({ type: "prevNote" });
    expect(key({ code: "Delete" })).toEqual({ type: "delete" });
    expect(key({ code: "Backspace" })).toEqual({ type: "delete" });
    expect(key({ code: "KeyZ", ctrlKey: true })).toEqual({ type: "undo" });
    expect(key({ code: "KeyZ", metaKey: true, shiftKey: true })).toEqual({ type: "redo" });
    expect(key({ code: "KeyY", ctrlKey: true })).toEqual({ type: "redo" });
    expect(key({ code: "KeyZ", key: "я", ctrlKey: true })).toEqual({ type: "undo" }); // Russian layout
    expect(key({ code: "KeyC", ctrlKey: true })).toBeNull(); // copy stays the browser's
  });

  it("types two-digit frets within a short window", () => {
    let step = typeDigit(null, 1, 0, 22);
    expect(step.fret).toBe(1);
    step = typeDigit(step.pending, 2, 300, 22);
    expect(step).toEqual({ fret: 12, pending: null });
    // too slow: second digit starts over
    expect(typeDigit({ value: 1, at: 0 }, 5, 2000, 22).fret).toBe(5);
    // 3 can't start a valid two-digit fret (30+ > 22)
    expect(typeDigit(null, 3, 0, 22).pending).toBeNull();
    // 2 then 5 = 25 > 22: start over with 5
    expect(typeDigit({ value: 2, at: 0 }, 5, 100, 22).fret).toBe(5);
  });
});
