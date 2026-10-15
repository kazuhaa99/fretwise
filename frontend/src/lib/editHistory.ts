/**
 * Undo / redo for tab edits as a plain reducer over immutable snapshots.
 *
 * Snapshots are whole track lists, but cheap: edits share every untouched
 * event with the previous snapshot (see tabEdit.ts). A server-side revert or
 * a fresh load replaces the history entirely — there's nothing meaningful to
 * undo back into across that boundary.
 */
import type { TabTrack } from "../types";

export const HISTORY_LIMIT = 200;

export interface EditHistory {
  past: TabTrack[][];
  present: TabTrack[];
  future: TabTrack[][];
}

export type HistoryAction =
  | { type: "commit"; tracks: TabTrack[] }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "reset"; tracks: TabTrack[] };

export function initHistory(tracks: TabTrack[]): EditHistory {
  return { past: [], present: tracks, future: [] };
}

export function historyReducer(state: EditHistory, action: HistoryAction): EditHistory {
  switch (action.type) {
    case "commit":
      if (action.tracks === state.present) return state;
      return {
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        present: action.tracks,
        future: [],
      };
    case "undo": {
      if (!state.past.length) return state;
      const previous = state.past[state.past.length - 1];
      return { past: state.past.slice(0, -1), present: previous, future: [state.present, ...state.future] };
    }
    case "redo": {
      if (!state.future.length) return state;
      const [next, ...rest] = state.future;
      return { past: [...state.past, state.present], present: next, future: rest };
    }
    case "reset":
      return initHistory(action.tracks);
  }
}
