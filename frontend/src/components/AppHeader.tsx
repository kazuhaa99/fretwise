import type { ReactNode } from "react";
import type { ThemeChoice } from "../theme";
import { AutoThemeIcon, MoonIcon, SunIcon } from "./icons";
import { Segmented } from "./ui";

export function Logo() {
  return (
    <svg width="30" height="30" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect width="28" height="28" rx="8" fill="var(--accent-strong)" />
      <g stroke="var(--on-accent)" strokeWidth="1.6" strokeLinecap="round">
        <line x1="7" y1="9" x2="21" y2="9" />
        <line x1="7" y1="14" x2="21" y2="14" />
        <line x1="7" y1="19" x2="21" y2="19" />
      </g>
      <circle cx="12" cy="14" r="2.6" fill="var(--on-accent)" />
    </svg>
  );
}

interface Props {
  theme: ThemeChoice;
  onThemeChange: (theme: ThemeChoice) => void;
  onHome?: () => void;
  actions?: ReactNode;
}

export default function AppHeader({ theme, onThemeChange, onHome, actions }: Props) {
  const brand = (
    <span className="flex items-center gap-2.5">
      <Logo />
      <span className="text-[17px] font-bold tracking-tight">Fretwise</span>
    </span>
  );
  return (
    <header
      className="border-b border-line bg-bg"
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-2 sm:px-6">
        {onHome ? (
          <button type="button" onClick={onHome} className="-ml-1 flex min-h-11 items-center rounded-md px-1" aria-label="Fretwise — на главную">
            {brand}
          </button>
        ) : (
          <div className="flex min-h-11 items-center">{brand}</div>
        )}
        <div className="flex items-center gap-2">
          {actions}
          <Segmented<ThemeChoice>
            label="Тема оформления"
            size="sm"
            value={theme}
            onChange={onThemeChange}
            options={[
              { value: "system", label: <AutoThemeIcon size={18} />, ariaLabel: "Тема как в системе" },
              { value: "light", label: <SunIcon size={18} />, ariaLabel: "Светлая тема" },
              { value: "dark", label: <MoonIcon size={18} />, ariaLabel: "Тёмная тема" },
            ]}
          />
        </div>
      </div>
    </header>
  );
}
