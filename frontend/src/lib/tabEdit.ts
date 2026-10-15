/**
 * Pure editing operations on a tab track (fret, string, delete, add).
 *
 * Every function returns a *new* track (structural sharing — untouched events
 * keep their identity, which keeps undo snapshots cheap) or `null` when the
 * edit isn't possible on a guitar: a fret off the neck, a string that's already
 * sounding in that chord. The rules mirror the server's validation in
 * backend/app/jobs/edits.py, so a save never fails for something the editor
 * allowed.
 */
import type { TabEvent, TabNote, TabTrack } from "../types";
import { noteToMidi } from "./tuning";

export interface NoteRef {
  event: number;
  note: number;
}

export interface EditContext {
  /** Open-string pitches (MIDI), low string first, capo NOT included. */
  tuningMidi: number[];
  capo: number;
  /** Frets available above the capo. */
  maxFret: number;
}

const MAX_FRET_FROM_NUT = 22;
/** Onsets closer than this belong to the same chord (matches the backend). */
export const CHORD_EPSILON = 0.03;
const DEFAULT_NEW_NOTE_DURATION = 0.25;

export function editContext(tuning: string[], capo: number): EditContext {
  return {
    tuningMidi: tuning.map((n) => noteToMidi(n) ?? 40),
    capo,
    maxFret: Math.max(0, MAX_FRET_FROM_NUT - capo),
  };
}

function replaceNote(track: TabTrack, ref: NoteRef, note: TabNote): TabTrack {
  const events = track.events.slice();
  const event = events[ref.event];
  const notes = event.notes.slice();
  notes[ref.note] = note;
  events[ref.event] = { ...event, notes };
  return { ...track, events };
}

export function getNote(track: TabTrack, ref: NoteRef | null): TabNote | null {
  if (!ref) return null;
  return track.events[ref.event]?.notes[ref.note] ?? null;
}

export function setFret(track: TabTrack, ref: NoteRef, fret: number, ctx: EditContext): TabTrack | null {
  const note = getNote(track, ref);
  if (!note || !Number.isInteger(fret) || fret < 0 || fret > ctx.maxFret) return null;
  if (fret === note.fret) return track;
  return replaceNote(track, ref, { ...note, fret });
}

export function shiftFret(track: TabTrack, ref: NoteRef, delta: number, ctx: EditContext): TabTrack | null {
  const note = getNote(track, ref);
  return note ? setFret(track, ref, note.fret + delta, ctx) : null;
}

/**
 * Move a note to the neighbouring string (`direction` +1 = the next higher
 * string, drawn above) keeping its pitch — the fret changes accordingly, the
 * way a guitarist re-fingers a note. Null if that would leave the neck or the
 * string already sounds in this chord.
 */
export function moveString(
  track: TabTrack,
  ref: NoteRef,
  direction: 1 | -1,
  ctx: EditContext,
): { track: TabTrack; ref: NoteRef } | null {
  const note = getNote(track, ref);
  if (!note) return null;
  const target = note.string + direction;
  if (target < 0 || target >= ctx.tuningMidi.length) return null;
  if (track.events[ref.event].notes.some((n, i) => i !== ref.note && n.string === target)) return null;
  const fret = note.fret + ctx.tuningMidi[note.string] - ctx.tuningMidi[target];
  if (fret < 0 || fret > ctx.maxFret) return null;
  return { track: replaceNote(track, ref, { ...note, string: target, fret }), ref };
}

/** Chronological order of all notes: by event, then low string to high. */
function orderedRefs(track: TabTrack): NoteRef[] {
  const refs: NoteRef[] = [];
  track.events.forEach((event, e) => {
    event.notes
      .map((n, i) => ({ i, s: n.string }))
      .sort((a, b) => a.s - b.s)
      .forEach(({ i }) => refs.push({ event: e, note: i }));
  });
  return refs;
}

export function neighbourNote(track: TabTrack, ref: NoteRef | null, direction: 1 | -1): NoteRef | null {
  const refs = orderedRefs(track);
  if (!refs.length) return null;
  if (!ref) return direction > 0 ? refs[0] : refs[refs.length - 1];
  const index = refs.findIndex((r) => r.event === ref.event && r.note === ref.note);
  const next = refs[index + direction];
  return next ?? refs[index] ?? null;
}

/** Delete a note (and its chord if it was the last note); returns what to select next. */
export function deleteNote(track: TabTrack, ref: NoteRef): { track: TabTrack; ref: NoteRef | null } {
  const event = track.events[ref.event];
  if (!event?.notes[ref.note]) return { track, ref: null };
  const events = track.events.slice();
  if (event.notes.length > 1) {
    const notes = event.notes.filter((_, i) => i !== ref.note);
    events[ref.event] = { ...event, notes };
    return { track: { ...track, events }, ref: { event: ref.event, note: Math.min(ref.note, notes.length - 1) } };
  }
  events.splice(ref.event, 1);
  const nextTrack = { ...track, events };
  if (!events.length) return { track: nextTrack, ref: null };
  const e = Math.min(ref.event, events.length - 1);
  return { track: nextTrack, ref: { event: e, note: 0 } };
}

/**
 * Add a note at `time` on `string`. Joins the chord at that moment if there is
 * one (or selects the note already on that string); otherwise inserts a new
 * event, as long as the next onset allows.
 */
export function addNote(
  track: TabTrack,
  time: number,
  string: number,
  ctx: EditContext,
  fret = 0,
): { track: TabTrack; ref: NoteRef } | null {
  if (string < 0 || string >= ctx.tuningMidi.length || fret < 0 || fret > ctx.maxFret || time < 0) return null;
  const events = track.events;
  const existing = events.findIndex((e) => Math.abs(e.time - time) <= CHORD_EPSILON);
  if (existing >= 0) {
    const event = events[existing];
    const onString = event.notes.findIndex((n) => n.string === string);
    if (onString >= 0) return { track, ref: { event: existing, note: onString } };
    const note: TabNote = { string, fret };
    const nextEvents = events.slice();
    nextEvents[existing] = { ...event, notes: [...event.notes, note] };
    return { track: { ...track, events: nextEvents }, ref: { event: existing, note: event.notes.length } };
  }

  let index = events.findIndex((e) => e.time > time);
  if (index < 0) index = events.length;
  const next = events[index];
  const duration = next ? Math.max(0.05, Math.min(DEFAULT_NEW_NOTE_DURATION, next.time - time)) : DEFAULT_NEW_NOTE_DURATION;
  const event: TabEvent = { time: Math.round(time * 1e4) / 1e4, duration, notes: [{ string, fret }] };
  const nextEvents = [...events.slice(0, index), event, ...events.slice(index)];
  return { track: { ...track, events: nextEvents }, ref: { event: index, note: 0 } };
}

/** Snap a time to the nearest sixteenth of the beat grid (or 1/8 s without one). */
export function snapToGrid(time: number, beatTimes: number[]): number {
  if (beatTimes.length < 2) return Math.max(0, Math.round(time * 8) / 8);
  let i = 0;
  while (i < beatTimes.length - 2 && beatTimes[i + 1] <= time) i++;
  const start = beatTimes[i];
  const period = beatTimes[i + 1] - start;
  const quarters = Math.round(((time - start) / period) * 4);
  return Math.max(0, Math.round((start + (quarters / 4) * period) * 1e4) / 1e4);
}
