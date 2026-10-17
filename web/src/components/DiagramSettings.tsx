import { useCallback, useEffect, useState, type ReactNode } from "react";
import { RotateCcw, SlidersHorizontal, X } from "lucide-react";
import { Button } from "./ui";

export type DiagramTheme = "default" | "dark" | "forest" | "neutral" | "base";
export type CurveType = "linear" | "basis" | "cardinal" | "step" | "monotoneX";

/** Настройки отрисовки диаграммы mermaid (сохраняются в браузере). */
export interface DiagramSettings {
  theme: DiagramTheme;
  lineColor: string;
  lineWidth: number;
  curve: CurveType;
  nodeColor: string;
  nodeBorder: string;
  nodeBorderWidth: number;
  cornerRadius: number;
  nodeSpacing: number;
  rankSpacing: number;
  padding: number;
  textColor: string;
  fontFamily: string;
  fontSize: number;
  htmlLabels: boolean;
}

/**
 * Значения по умолчанию подобраны так, чтобы линии были плавными (basis),
 * а подписи рисовались векторным текстом (htmlLabels=false) — без «квадратиков».
 */
export const DEFAULT_DIAGRAM_SETTINGS: DiagramSettings = {
  theme: "dark",
  lineColor: "#b91c1c",
  lineWidth: 2,
  curve: "basis",
  nodeColor: "#1b1b21",
  nodeBorder: "#dc2626",
  nodeBorderWidth: 1.5,
  cornerRadius: 8,
  nodeSpacing: 60,
  rankSpacing: 60,
  padding: 12,
  textColor: "#e5e5ea",
  fontFamily: "Inter, system-ui, sans-serif",
  fontSize: 14,
  htmlLabels: false,
};

const STORAGE_KEY = "ts.diagram.settings";

export function loadDiagramSettings(): DiagramSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_DIAGRAM_SETTINGS };
    const saved = JSON.parse(raw) as Partial<DiagramSettings>;
    return { ...DEFAULT_DIAGRAM_SETTINGS, ...saved };
  } catch {
    return { ...DEFAULT_DIAGRAM_SETTINGS };
  }
}

/** Хранилище настроек диаграммы: состояние + запись в localStorage. */
export function useDiagramSettings() {
  const [settings, setSettings] = useState<DiagramSettings>(loadDiagramSettings);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* приватный режим — просто не сохраняем */
    }
  }, [settings]);

  const patch = useCallback(
    (p: Partial<DiagramSettings>) => setSettings((s) => ({ ...s, ...p })),
    [],
  );
  const reset = useCallback(() => setSettings({ ...DEFAULT_DIAGRAM_SETTINGS }), []);

  return { settings, patch, reset };
}

// ---------- элементы формы ----------

const LABEL = "text-xs font-medium text-ink-300";
const INPUT =
  "rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-sm text-white focus:border-dragon-600 focus:outline-none";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className={LABEL}>{label}</span>
      {children}
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (v: number) => void;
}) {
  return (
    <Row label={`${label} · ${value}${suffix ?? ""}`}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-dragon-600"
      />
    </Row>
  );
}

function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Row label={label}>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-10 cursor-pointer rounded-md border border-ink-600 bg-ink-900 p-0.5"
        />
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`${INPUT} w-full font-mono`}
        />
      </div>
    </Row>
  );
}

const FONT_FAMILIES = [
  "Inter, system-ui, sans-serif",
  "JetBrains Mono, SFMono-Regular, ui-monospace, Menlo, monospace",
  "Georgia, 'Times New Roman', serif",
  "system-ui, -apple-system, sans-serif",
];

/**
 * Панель настроек диаграммы: тема, цвета, линии, узлы, текст.
 * Открывается кнопкой-шестерёнкой рядом с диаграммой.
 */
export function DiagramSettingsPanel({
  settings,
  patch,
  reset,
  onClose,
}: {
  settings: DiagramSettings;
  patch?: (p: Partial<DiagramSettings>) => void;
  reset?: () => void;
  onClose: () => void;
}) {
  const set = (p: Partial<DiagramSettings>) => patch?.(p);
  return (
    <div className="card mb-4 animate-fade-up p-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
          <SlidersHorizontal size={15} /> Настройки диаграммы
        </h3>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            icon={<RotateCcw size={14} />}
            onClick={reset}
            title="Вернуть настройки по умолчанию"
          >
            Сброс
          </Button>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-ink-400 hover:bg-ink-800 hover:text-white"
            aria-label="Закрыть настройки"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="flex flex-col gap-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Общее</h4>

          <Row label="Тема">
            <select
              value={settings.theme}
              onChange={(e) => set({ theme: e.target.value as DiagramTheme })}
              className={INPUT}
            >
              <option value="dark">Тёмная (по умолчанию)</option>
              <option value="default">Обычная</option>
              <option value="forest">Лесная</option>
              <option value="neutral">Нейтральная</option>
              <option value="base">Своя (только мои цвета)</option>
            </select>
          </Row>

          <Row label="Шрифт">
            <select
              value={settings.fontFamily}
              onChange={(e) => set({ fontFamily: e.target.value })}
              className={INPUT}
            >
              {FONT_FAMILIES.map((f) => (
                <option key={f} value={f}>
                  {f.split(",")[0]}
                </option>
              ))}
            </select>
          </Row>

          <NumberField
            label="Размер текста"
            value={settings.fontSize}
            min={10}
            max={24}
            suffix=" px"
            onChange={(v) => set({ fontSize: v })}
          />

          <label className="flex items-center gap-2 text-sm text-ink-200">
            <input
              type="checkbox"
              checked={settings.htmlLabels}
              onChange={(e) => set({ htmlLabels: e.target.checked })}
              className="accent-dragon-600"
            />
            HTML-подписи узлов
            <span className="text-xs text-ink-400">(при масштабировании дают «квадратики»)</span>
          </label>
        </div>

        <div className="flex flex-col gap-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Линии</h4>

          <ColorField
            label="Цвет линий"
            value={settings.lineColor}
            onChange={(v) => set({ lineColor: v })}
          />

          <NumberField
            label="Толщина линий"
            value={settings.lineWidth}
            min={0.5}
            max={6}
            step={0.5}
            suffix=" px"
            onChange={(v) => set({ lineWidth: v })}
          />

          <Row label="Форма линий">
            <select
              value={settings.curve}
              onChange={(e) => set({ curve: e.target.value as CurveType })}
              className={INPUT}
            >
              <option value="basis">Плавные (basis)</option>
              <option value="cardinal">Мягкие (cardinal)</option>
              <option value="monotoneX">Монотонные (monotone)</option>
              <option value="step">Ступенчатые (step)</option>
              <option value="linear">Прямые углы (linear)</option>
            </select>
          </Row>

          <p className="text-xs text-ink-400">
            «Плавные» убирают резкие углы у линий связей.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Узлы</h4>

          <ColorField
            label="Заливка узлов"
            value={settings.nodeColor}
            onChange={(v) => set({ nodeColor: v })}
          />
          <ColorField
            label="Рамка узлов"
            value={settings.nodeBorder}
            onChange={(v) => set({ nodeBorder: v })}
          />
          <ColorField
            label="Текст"
            value={settings.textColor}
            onChange={(v) => set({ textColor: v })}
          />

          <NumberField
            label="Толщина рамки"
            value={settings.nodeBorderWidth}
            min={0}
            max={5}
            step={0.5}
            suffix=" px"
            onChange={(v) => set({ nodeBorderWidth: v })}
          />
          <NumberField
            label="Скругление углов"
            value={settings.cornerRadius}
            min={0}
            max={24}
            suffix=" px"
            onChange={(v) => set({ cornerRadius: v })}
          />
          <NumberField
            label="Отступ в узле"
            value={settings.padding}
            min={4}
            max={40}
            suffix=" px"
            onChange={(v) => set({ padding: v })}
          />
          <NumberField
            label="Расстояние между узлами"
            value={settings.nodeSpacing}
            min={20}
            max={160}
            suffix=" px"
            onChange={(v) => set({ nodeSpacing: v })}
          />
          <NumberField
            label="Расстояние между уровнями"
            value={settings.rankSpacing}
            min={20}
            max={160}
            suffix=" px"
            onChange={(v) => set({ rankSpacing: v })}
          />
        </div>
      </div>
    </div>
  );
}
