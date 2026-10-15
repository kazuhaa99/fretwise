import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { JobResult, TabTrack } from "../types";
import { ApiError, errorMessage, revertEdits, saveTab } from "../api/client";
import { historyReducer, initHistory } from "./editHistory";

export type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };

const SAVE_DEBOUNCE_MS = 500;

/**
 * Editing state for a result's tab: undo/redo history plus autosave.
 *
 * Every change is saved to the server shortly after it is made (debounced so
 * typing "1","2" for fret 12 is one save, not two). Saves never overlap — the
 * next one starts when the previous returns, with whatever is newest by then —
 * and each carries the revision it is based on. If the server reports a
 * conflict (the job was edited in another tab), autosave stops instead of
 * fighting over the file; after any other failure it waits for the next edit
 * rather than retrying the same rejected tab in a loop.
 */
export function useTabEditor(jobId: string, initial: JobResult) {
  const [history, dispatch] = useReducer(historyReducer, initial.tab.tracks, initHistory);
  const [edited, setEdited] = useState(initial.edited);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [settled, setSettled] = useState(0); // bumps when a save finishes, to pick up newer edits
  const revisionRef = useRef(initial.revision);
  const savedRef = useRef<TabTrack[]>(initial.tab.tracks); // what the server has
  const failedRef = useRef<TabTrack[] | null>(null); // last version the server rejected
  const inflightRef = useRef(false);
  const conflictRef = useRef(false);

  useEffect(() => {
    const tracks = history.present;
    if (tracks === savedRef.current || tracks === failedRef.current) return;
    if (inflightRef.current || conflictRef.current) return;
    const timer = setTimeout(async () => {
      inflightRef.current = true;
      setSave({ kind: "saving" });
      try {
        const result = await saveTab(jobId, revisionRef.current, tracks);
        revisionRef.current = result.revision;
        savedRef.current = tracks;
        failedRef.current = null;
        setEdited(result.edited);
        setSave({ kind: "saved" });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) conflictRef.current = true;
        failedRef.current = tracks;
        setSave({ kind: "error", message: errorMessage(e, "Не удалось сохранить правку. Проверь связь с сервером.") });
      } finally {
        inflightRef.current = false;
        setSettled((n) => n + 1);
      }
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [history.present, jobId, settled]);

  // Leaving with an unsaved edit: let the browser ask first.
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (history.present !== savedRef.current) e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [history.present]);

  const commit = useCallback((tracks: TabTrack[]) => dispatch({ type: "commit", tracks }), []);
  const undo = useCallback(() => dispatch({ type: "undo" }), []);
  const redo = useCallback(() => dispatch({ type: "redo" }), []);

  const revert = useCallback(async () => {
    setSave({ kind: "saving" });
    try {
      const result = await revertEdits(jobId);
      revisionRef.current = result.revision;
      savedRef.current = result.tab.tracks;
      failedRef.current = null;
      conflictRef.current = false;
      dispatch({ type: "reset", tracks: result.tab.tracks });
      setEdited(false);
      setSave({ kind: "saved" });
    } catch (e) {
      setSave({ kind: "error", message: errorMessage(e, "Не удалось сбросить правки") });
    }
  }, [jobId]);

  return {
    tracks: history.present,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    edited,
    save,
    commit,
    undo,
    redo,
    revert,
  };
}
