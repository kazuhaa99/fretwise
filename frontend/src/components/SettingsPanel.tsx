import { useId, useState } from "react";
import { DEFAULT_SETTINGS, type TabSettings } from "../types";
import { TUNING_PRESETS, parseTuning, presetFor, tuningLetters } from "../lib/tuning";
import { plural } from "../lib/format";
import { ChevronDownIcon, SlidersIcon } from "./icons";
import { Button, Segmented } from "./ui";

interface Props {
  settings: TabSettings;
  onChange: (settings: TabSettings) => void;
  /** Reports whether the custom tuning text currently parses. */
  onValidityChange?: (valid: boolean) => void;
  defaultOpen?: boolean;
}

const CUSTOM = "custom";

function settingsSummary(settings: TabSettings): string {
  const preset = presetFor(settings.tuning);
  const tuning = preset ? preset.label : tuningLetters(settings.tuning);
  const capo = settings.capo > 0 ? `капо ${settings.capo}` : "без капо";
  const span = `растяжка до ${settings.maxFretSpan} ${plural(settings.maxFretSpan, ["лада", "ладов", "ладов"])}`;
  return `${tuning} · ${capo} · ${span}`;
}

export default function SettingsPanel({ settings, onChange, onValidityChange, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const preset = presetFor(settings.tuning);
  const [custom, setCustom] = useState(!preset);
  const [customText, setCustomText] = useState(settings.tuning.join(" "));
  const [customError, setCustomError] = useState<string | null>(null);
  const ids = { panel: useId(), custom: useId(), customHint: useId(), capo: useId(), span: useId(), jump: useId() };

  const choosePreset = (value: string) => {
    if (value === CUSTOM) {
      setCustom(true);
      setCustomText(settings.tuning.join(" "));
      return;
    }
    const next = TUNING_PRESETS.find((p) => p.id === value)!;
    setCustom(false);
    setCustomError(null);
    onValidityChange?.(true);
    onChange({ ...settings, tuning: next.tuning });
  };

  const editCustom = (text: string) => {
    setCustomText(text);
    const parsed = parseTuning(text);
    if (parsed.ok) {
      setCustomError(null);
      onValidityChange?.(true);
      onChange({ ...settings, tuning: parsed.tuning });
    } else {
      setCustomError(parsed.error);
      onValidityChange?.(false);
    }
  };

  const setCapo = (capo: number) => onChange({ ...settings, capo: Math.max(0, Math.min(12, capo)) });

  return (
    <section className="rounded-lg border border-line bg-surface">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={ids.panel}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-14 w-full items-center gap-3 rounded-lg px-4 text-left sm:px-5"
      >
        <SlidersIcon className="shrink-0 text-accent-strong" />
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">Настройки разбора</span>
          <span className="block truncate text-[13px] text-muted">{settingsSummary(settings)}</span>
        </span>
        <ChevronDownIcon className={`shrink-0 text-muted transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div id={ids.panel} className="grid gap-6 border-t border-line px-4 py-5 sm:px-5">
          <fieldset className="grid gap-2">
            <legend className="mb-2 text-sm font-medium">Строй</legend>
            <Segmented
              label="Пресеты строя"
              className="flex-wrap"
              value={custom ? CUSTOM : (preset?.id ?? CUSTOM)}
              onChange={choosePreset}
              options={[
                ...TUNING_PRESETS.map((p) => ({ value: p.id, label: p.label })),
                { value: CUSTOM, label: "Свой…" },
              ]}
            />
            {custom ? (
              <div className="grid gap-1.5">
                <label htmlFor={ids.custom} className="text-sm">
                  Ноты открытых струн, от толстой к тонкой
                </label>
                <input
                  id={ids.custom}
                  value={customText}
                  onChange={(e) => editCustom(e.target.value)}
                  spellCheck={false}
                  autoCapitalize="characters"
                  aria-invalid={customError ? true : undefined}
                  aria-describedby={ids.customHint}
                  className={`min-h-11 w-full rounded-md border bg-surface px-3 font-mono text-[15px] text-fg
                    ${customError ? "border-danger shadow-[0_0_0_3px_var(--danger-soft)]" : "border-line-strong"}`}
                />
                <p id={ids.customHint} className={`text-[13px] ${customError ? "text-danger" : "text-muted"}`}>
                  {customError ?? `${settings.tuning.length} ${plural(settings.tuning.length, ["струна", "струны", "струн"])} · ноты в формате E2, F#3, Bb1`}
                </p>
              </div>
            ) : (
              <p className="text-[13px] text-muted">
                {tuningLetters(settings.tuning)}. От строя зависит, на каких ладах можно сыграть каждую ноту.
              </p>
            )}
          </fieldset>

          <div className="grid gap-2">
            <label htmlFor={ids.capo} className="text-sm font-medium">
              Каподастр
            </label>
            <div className="flex items-center gap-2">
              <Button variant="secondary" aria-label="Каподастр ниже" onClick={() => setCapo(settings.capo - 1)} disabled={settings.capo <= 0} className="w-11 px-0">
                −
              </Button>
              <input
                id={ids.capo}
                type="number"
                inputMode="numeric"
                min={0}
                max={12}
                value={settings.capo}
                onChange={(e) => setCapo(Number(e.target.value) || 0)}
                className="tabular min-h-11 w-20 rounded-md border border-line-strong bg-surface px-3 text-center text-[15px] text-fg"
              />
              <Button variant="secondary" aria-label="Каподастр выше" onClick={() => setCapo(settings.capo + 1)} disabled={settings.capo >= 12} className="w-11 px-0">
                +
              </Button>
              <span className="text-[13px] text-muted">{settings.capo === 0 ? "без каподастра" : `${settings.capo} лад`}</span>
            </div>
            <p className="text-[13px] text-muted">Лады в табулатуре будут считаться от каподастра, как принято в нотах.</p>
          </div>

          <div className="grid gap-2">
            <label htmlFor={ids.span} className="flex items-baseline justify-between gap-3 text-sm font-medium">
              <span>Удобство аппликатуры</span>
              <span className="tabular text-[13px] font-normal text-muted">
                растяжка до {settings.maxFretSpan} {plural(settings.maxFretSpan, ["лада", "ладов", "ладов"])}
              </span>
            </label>
            <input
              id={ids.span}
              type="range"
              min={3}
              max={6}
              step={1}
              value={settings.maxFretSpan}
              onChange={(e) => onChange({ ...settings, maxFretSpan: Number(e.target.value) })}
              className="h-11 w-full accent-[var(--accent-strong)]"
            />
            <p className="text-[13px] text-muted">
              Насколько широко может растянуться рука в одном аккорде. Меньше — только то, что точно дотянется, но часть нот
              аккорда может пропасть. Больше — ближе к записи, но тяжелее играть.
            </p>
          </div>

          <div className="grid gap-2">
            <label htmlFor={ids.jump} className="flex items-baseline justify-between gap-3 text-sm font-medium">
              <span>Переходы по грифу</span>
              <span className="tabular text-[13px] font-normal text-muted">вес {settings.positionJumpWeight.toFixed(1).replace(".", ",")}</span>
            </label>
            <input
              id={ids.jump}
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={settings.positionJumpWeight}
              onChange={(e) => onChange({ ...settings, positionJumpWeight: Number(e.target.value) })}
              className="h-11 w-full accent-[var(--accent-strong)]"
            />
            <p className="text-[13px] text-muted">
              Чем выше, тем дольше рука остаётся в одной позиции: меньше прыжков по грифу, но ноты чаще берутся выше. Ноль —
              каждая нота на самом удобном месте, даже ценой частых переходов.
            </p>
          </div>

          <div>
            <Button
              variant="ghost"
              onClick={() => {
                setCustom(false);
                setCustomError(null);
                onValidityChange?.(true);
                onChange(DEFAULT_SETTINGS);
              }}
            >
              Сбросить к стандартным
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
