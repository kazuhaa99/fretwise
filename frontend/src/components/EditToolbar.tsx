import { useId, useState } from "react";
import type { TabNote } from "../types";
import type { SaveState } from "../lib/useTabEditor";
import { ArrowDownIcon, ArrowUpIcon, CheckIcon, RedoIcon, TrashIcon, UndoIcon } from "./icons";
import { Button, IconButton, Spinner } from "./ui";


interface Props {
  note: TabNote | null;
  stringCount: number;
  maxFret: number;
  canUndo: boolean;
  canRedo: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  edited: boolean;
  save: SaveState;
  onUndo: () => void;
  onRedo: () => void;
  onSetFret: (fret: number) => void;
  onShiftFret: (delta: number) => void;
  onMoveString: (direction: 1 | -1) => void;
  onDelete: () => void;
  onRevert: () => void;
  onDone: () => void;
  /** Bars selected on the sheet (drag over bar numbers / Shift+click), 1-based label like "80–81". */
  selectionLabel: string | null;
  onRefinger: (position: number) => void;
  refingering: boolean;
}

/** Number of the string as guitarists count: 1 = the thinnest (highest). */
function stringLabel(stringIndex: number, stringCount: number): string {
  return `${stringCount - stringIndex}-я струна`;
}

function SaveStatus({ save, edited }: { save: SaveState; edited: boolean }) {
  if (save.kind === "saving")
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
        <Spinner size={12} /> Сохраняем…
      </span>
    );
  if (save.kind === "error")
    return (
      <span role="alert" className="text-[13px] text-danger">
        {save.message}
      </span>
    );
  if (save.kind === "saved")
    return (
      <span className="inline-flex items-center gap-1 text-[13px] text-muted">
        <CheckIcon size={14} /> Сохранено, .gp5 обновлён
      </span>
    );
  return <span className="text-[13px] text-muted">{edited ? "Есть правки" : "Правок пока нет"}</span>;
}

/**
 * Edit-mode toolbar: undo/redo, the selected note's fret and string, delete,
 * save status and "revert all". Everything here is also on the keyboard (see
 * editKeys.ts) — the buttons are what makes editing possible on a phone.
 */
export default function EditToolbar(props: Props) {
  const { note } = props;
  const fretId = useId();
  const [confirmRevert, setConfirmRevert] = useState(false);
  const [position, setPosition] = useState(5);
  const positionId = useId();

  return (
    <div role="toolbar" aria-label="Правка табулатуры" className="grid gap-3 rounded-lg border border-accent bg-surface p-3 sm:p-4">
      <div className="flex flex-wrap items-center gap-2">
        <IconButton label="Отменить (Ctrl+Z)" onClick={props.onUndo} disabled={!props.canUndo}>
          <UndoIcon />
        </IconButton>
        <IconButton label="Повторить (Ctrl+Shift+Z)" onClick={props.onRedo} disabled={!props.canRedo}>
          <RedoIcon />
        </IconButton>
        <span className="mx-1 h-7 w-px bg-line" aria-hidden="true" />

        {note ? (
          <>
            <span className="text-sm font-medium">{stringLabel(note.string, props.stringCount)}</span>
            <div className="flex items-center gap-1">
              <Button variant="secondary" className="w-11 px-0" aria-label="Лад ниже" onClick={() => props.onShiftFret(-1)} disabled={note.fret <= 0}>
                −
              </Button>
              <label htmlFor={fretId} className="sr-only">
                Лад
              </label>
              <input
                id={fretId}
                type="number"
                inputMode="numeric"
                min={0}
                max={props.maxFret}
                value={note.fret}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  if (Number.isInteger(value)) props.onSetFret(value);
                }}
                className="tabular min-h-11 w-16 rounded-md border border-line-strong bg-surface px-2 text-center font-mono text-[15px] font-bold text-fg"
              />
              <Button variant="secondary" className="w-11 px-0" aria-label="Лад выше" onClick={() => props.onShiftFret(1)} disabled={note.fret >= props.maxFret}>
                +
              </Button>
            </div>
            <IconButton label="На струну выше (↑)" onClick={() => props.onMoveString(1)} disabled={!props.canMoveUp}>
              <ArrowUpIcon />
            </IconButton>
            <IconButton label="На струну ниже (↓)" onClick={() => props.onMoveString(-1)} disabled={!props.canMoveDown}>
              <ArrowDownIcon />
            </IconButton>
            <IconButton label="Удалить ноту (Delete)" onClick={props.onDelete} className="text-danger hover:bg-danger-soft hover:text-danger">
              <TrashIcon />
            </IconButton>
          </>
        ) : (
          <span className="text-[14px] text-muted">
            Выбери ноту кликом или кликни по струне, чтобы добавить новую. Лад вводится цифрами.
          </span>
        )}

        <span className="ml-auto">
          <Button onClick={props.onDone} icon={<CheckIcon />}>
            Готово
          </Button>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2">
        {props.selectionLabel ? (
          <>
            <span className="text-sm font-medium">
              {props.selectionLabel.includes("–") ? "Такты" : "Такт"} {props.selectionLabel}: переложить в позицию
            </span>
            <div className="flex items-center gap-1">
              <Button variant="secondary" className="w-11 px-0" aria-label="Позиция ниже" onClick={() => setPosition((p) => Math.max(1, p - 1))} disabled={position <= 1}>
                −
              </Button>
              <label htmlFor={positionId} className="sr-only">
                Лад позиции
              </label>
              <input
                id={positionId}
                type="number"
                inputMode="numeric"
                min={1}
                max={17}
                value={position}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isInteger(v)) setPosition(Math.max(1, Math.min(17, v)));
                }}
                className="tabular min-h-11 w-16 rounded-md border border-line-strong bg-surface px-2 text-center font-mono text-[15px] font-bold text-fg"
              />
              <Button variant="secondary" className="w-11 px-0" aria-label="Позиция выше" onClick={() => setPosition((p) => Math.min(17, p + 1))} disabled={position >= 17}>
                +
              </Button>
            </div>
            <Button variant="secondary" loading={props.refingering} onClick={() => props.onRefinger(position)}>
              Переложить
            </Button>
          </>
        ) : (
          <span className="text-[13px] text-muted">
            Чтобы переложить фрагмент в другую позицию, выдели такты: потяни по их номерам или кликни с Shift.
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
        <SaveStatus save={props.save} edited={props.edited} />
        {props.edited &&
          (confirmRevert ? (
            <span className="flex flex-wrap items-center gap-2 text-[13px]">
              Вернуть табулатуру как после распознавания? Все правки пропадут.
              <Button
                variant="danger"
                onClick={() => {
                  setConfirmRevert(false);
                  props.onRevert();
                }}
              >
                Сбросить
              </Button>
              <Button variant="ghost" onClick={() => setConfirmRevert(false)}>
                Оставить
              </Button>
            </span>
          ) : (
            <Button variant="ghost" onClick={() => setConfirmRevert(true)}>
              Сбросить все правки
            </Button>
          ))}
      </div>
    </div>
  );
}
