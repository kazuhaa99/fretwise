import type { TabSettings } from "../types";

const TUNING_PRESETS: { label: string; tuning: string[] }[] = [
  { label: "Стандартный (E A D G B E)", tuning: ["E2", "A2", "D3", "G3", "B3", "E4"] },
  { label: "Drop D (D A D G B E)", tuning: ["D2", "A2", "D3", "G3", "B3", "E4"] },
  { label: "Полтона вниз (Eb)", tuning: ["Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"] },
  { label: "Открытый D (D A D F# A D)", tuning: ["D2", "A2", "D3", "Gb3", "A3", "D4"] },
];

interface Props {
  settings: TabSettings;
  onChange: (settings: TabSettings) => void;
  disabled?: boolean;
}

export default function SettingsPanel({ settings, onChange, disabled }: Props) {
  const presetIndex = TUNING_PRESETS.findIndex(
    (p) => p.tuning.join(",") === settings.tuning.join(","),
  );

  return (
    <div className={`rounded-2xl border border-walnut-700 bg-walnut-900/60 p-6 ${disabled ? "opacity-50 pointer-events-none" : ""}`}>
      <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-brass">
        Настройки разбора
      </h3>

      <div className="grid gap-5 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm text-cream/90">Строй гитары</span>
          <select
            value={presetIndex}
            onChange={(e) => onChange({ ...settings, tuning: TUNING_PRESETS[Number(e.target.value)].tuning })}
            className="w-full rounded-lg border border-walnut-600 bg-walnut-800 px-3 py-2 text-sm text-cream focus:border-brass focus:outline-none"
          >
            {TUNING_PRESETS.map((p, i) => (
              <option key={p.label} value={i}>{p.label}</option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-sm text-cream/90">Каподастр (лад)</span>
          <input
            type="number"
            min={0}
            max={12}
            value={settings.capo}
            onChange={(e) => onChange({ ...settings, capo: Math.max(0, Math.min(12, Number(e.target.value) || 0)) })}
            className="w-full rounded-lg border border-walnut-600 bg-walnut-800 px-3 py-2 text-sm text-cream focus:border-brass focus:outline-none"
          />
        </label>

        <label className="block sm:col-span-2">
          <span className="mb-1.5 flex items-baseline justify-between text-sm text-cream/90">
            <span>Удобство аппликатуры</span>
            <span className="text-xs text-muted">макс. растяжка — {settings.maxFretSpan} лада</span>
          </span>
          <input
            type="range"
            min={3}
            max={6}
            step={1}
            value={settings.maxFretSpan}
            onChange={(e) => onChange({ ...settings, maxFretSpan: Number(e.target.value) })}
            className="w-full accent-brass"
          />
          <span className="mt-1 flex justify-between text-xs text-muted">
            <span>Только то, что реально сыграть рукой</span>
            <span>Точнее к нотам, сложнее для пальцев</span>
          </span>
        </label>

        <label className="block sm:col-span-2">
          <span className="mb-1.5 flex items-baseline justify-between text-sm text-cream/90">
            <span>Минимизировать переходы по грифу</span>
            <span className="text-xs text-muted">вес — {settings.positionJumpWeight.toFixed(1)}</span>
          </span>
          <input
            type="range"
            min={0}
            max={2}
            step={0.1}
            value={settings.positionJumpWeight}
            onChange={(e) => onChange({ ...settings, positionJumpWeight: Number(e.target.value) })}
            className="w-full accent-brass"
          />
          <span className="mt-1 flex justify-between text-xs text-muted">
            <span>Точность нот важнее</span>
            <span>Удобство руки важнее</span>
          </span>
        </label>
      </div>
    </div>
  );
}
