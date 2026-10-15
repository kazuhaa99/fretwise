import { useEffect, useRef } from "react";
import { EDIT_KEYS } from "../lib/editKeys";
import { HOTKEYS } from "../lib/hotkeys";
import { CloseIcon } from "./icons";
import { IconButton, Kbd } from "./ui";

/** Modal list of keyboard shortcuts. Escape or the close button dismisses it;
 * focus moves into the dialog and back to what opened it. */
export default function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-[rgb(18_23_20/0.45)] p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-line bg-raised p-5 shadow-float"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id="shortcuts-title" className="text-xl font-semibold">
            Горячие клавиши
          </h2>
          <IconButton ref={closeRef} label="Закрыть" onClick={onClose}>
            <CloseIcon />
          </IconButton>
        </div>
        <table className="w-full text-[15px]">
          <tbody>
            {HOTKEYS.map((h) => (
              <tr key={h.action} className="border-b border-line last:border-0">
                <td className="w-28 py-2.5 pr-3 align-top">
                  <span className="inline-flex gap-1">
                    {h.keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </td>
                <td className="py-2.5">{h.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="mt-4 mb-1 text-sm font-semibold text-muted">В режиме правки</h3>
        <table className="w-full text-[15px]">
          <tbody>
            {EDIT_KEYS.map((h) => (
              <tr key={h.label} className="border-b border-line last:border-0">
                <td className="w-28 py-2 pr-3 align-top">
                  <span className="inline-flex flex-wrap gap-1">
                    {h.keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </td>
                <td className="py-2">{h.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-[13px] text-muted">
          Клавиши не срабатывают, пока курсор в поле ввода. Выделить несколько тактов для петли — потяни по номерам тактов
          или кликни с Shift.
        </p>
      </div>
    </div>
  );
}
