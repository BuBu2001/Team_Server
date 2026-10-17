import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import mermaid from "mermaid";
import { Maximize2, Minus, Plus, SlidersHorizontal } from "lucide-react";
import {
  DEFAULT_DIAGRAM_SETTINGS,
  DiagramSettingsPanel,
  type DiagramSettings,
} from "./DiagramSettings";

let counter = 0;

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 6;

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

/** Собирает конфиг mermaid из пользовательских настроек диаграммы. */
function buildConfig(s: DiagramSettings) {
  const dark = s.theme === "dark" || s.theme === "base";
  return {
    startOnLoad: false,
    theme: s.theme,
    securityLevel: "strict" as const,
    fontFamily: s.fontFamily,
    flowchart: {
      // false: подписи рисуются векторным SVG-текстом, а не HTML в foreignObject —
      // иначе при масштабировании они превращаются в «квадратики».
      htmlLabels: s.htmlLabels,
      curve: s.curve,
      nodeSpacing: s.nodeSpacing,
      rankSpacing: s.rankSpacing,
      padding: s.padding,
      useMaxWidth: false,
      diagramPadding: 8,
    },
    themeVariables: {
      darkMode: dark,
      background: "#0e0e11",
      primaryColor: s.nodeColor,
      primaryTextColor: s.textColor,
      primaryBorderColor: s.nodeBorder,
      lineColor: s.lineColor,
      edgeColor: s.lineColor,
      secondaryColor: s.nodeColor,
      tertiaryColor: "#131317",
      clusterBkg: "#131317",
      clusterBorder: "#2b2b34",
      noteBkgColor: s.nodeColor,
      noteTextColor: s.textColor,
      noteBorderColor: s.nodeBorder,
      fontFamily: s.fontFamily,
      fontSize: `${s.fontSize}px`,
    },
    // Точные CSS-override: mermaid иногда игнорирует themeVariables для толщины линий.
    themeCSS: `
      svg { shape-rendering: geometricPrecision; text-rendering: geometricPrecision; }
      .edgePath .path { stroke: ${s.lineColor}; stroke-width: ${s.lineWidth}px; }
      .flowchart-link { stroke: ${s.lineColor}; stroke-width: ${s.lineWidth}px; }
      .marker { fill: ${s.lineColor}; }
      .edgeLabel { background-color: ${s.nodeColor}; color: ${s.textColor}; font-family: ${s.fontFamily}; }
      .nodeLabel { color: ${s.textColor}; font-family: ${s.fontFamily}; font-size: ${s.fontSize}px; }
      .node rect, .node circle, .node polygon, .node ellipse {
        fill: ${s.nodeColor};
        stroke: ${s.nodeBorder};
        stroke-width: ${s.nodeBorderWidth}px;
        rx: ${s.cornerRadius}px;
        ry: ${s.cornerRadius}px;
      }
      .cluster rect { fill: ${s.nodeColor}; stroke: ${s.nodeBorder}; rx: ${s.cornerRadius}px; }
    `,
  };
}

/** Натуральный размер диаграммы из viewBox (без учёта масштаба). */
function parseSize(svg: string): { w: number; h: number } {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const el = doc.querySelector("svg");
  if (!el) return { w: 800, h: 500 };
  const vb = el.getAttribute("viewBox")?.trim().split(/[\s,]+/).map(Number);
  if (vb && vb.length === 4 && vb[2] > 0 && vb[3] > 0) return { w: vb[2], h: vb[3] };
  const w = Number(el.getAttribute("width"));
  const h = Number(el.getAttribute("height"));
  return { w: w > 0 && w !== 1 ? w : 800, h: h > 0 && h !== 1 ? h : 500 };
}

/**
 * Диаграмма mermaid с масштабированием и перемещением.
 *
 * Масштаб меняет физический размер контейнера, а НЕ CSS-трансформ scale:
 * браузер при этом заново растрирует вектор — линии остаются чёткими на любом проценте.
 *
 * Настройки (тема, цвета, линии, узлы, шрифт) приходят снаружи; кнопка-шестерёнка
 * в панели управления открывает панель настроек.
 */
export function Mermaid({
  code,
  settings = DEFAULT_DIAGRAM_SETTINGS,
  onPatchSettings,
  onResetSettings,
  className = "",
}: {
  code: string;
  settings?: DiagramSettings;
  onPatchSettings?: (p: Partial<DiagramSettings>) => void;
  onResetSettings?: () => void;
  className?: string;
}) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [grabbing, setGrabbing] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const idRef = useRef(`mmd-${++counter}`);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  // Стабильный ключ: mermaid хранит конфиг глобально, переинициализируем при его смене.
  const settingsKey = JSON.stringify(settings);

  useEffect(() => {
    let cancelled = false;
    mermaid.initialize(buildConfig(settings));
    setError("");
    setSvg("");
    if (!code.trim()) return;
    mermaid
      .render(idRef.current, code)
      .then(({ svg }) => {
        if (!cancelled) setSvg(svg);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
      // mermaid оставляет служебный элемент; убираем, чтобы не копился
      document.getElementById(`d${idRef.current}`)?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, settingsKey]);

  useEffect(() => {
    setSize(svg ? parseSize(svg) : { w: 0, h: 0 });
  }, [svg]);

  /** Масштаб вокруг центра видимой области. */
  const zoomBy = useCallback((factor: number) => {
    const rect = boxRef.current?.getBoundingClientRect();
    const cx = rect ? rect.width / 2 : 0;
    const cy = rect ? rect.height / 2 : 0;
    setOffset((o) => ({
      x: cx - (cx - o.x) * factor,
      y: cy - (cy - o.y) * factor,
    }));
    setZoom((z) => clamp(z * factor, MIN_ZOOM, MAX_ZOOM));
  }, []);

  /** Вписывает диаграмму целиком в контейнер. */
  const fitView = useCallback(() => {
    const box = boxRef.current;
    if (!box) return;
    const bw = box.clientWidth;
    const bh = box.clientHeight;
    if (!bw || !bh || !size.w || !size.h) return;
    const scale = clamp(Math.min((bw - 24) / size.w, (bh - 24) / size.h), MIN_ZOOM, MAX_ZOOM);
    setZoom(scale);
    setOffset({ x: (bw - size.w * scale) / 2, y: (bh - size.h * scale) / 2 });
  }, [size]);

  // Вписываем при загрузке диаграммы и при смене настроек.
  useEffect(() => {
    if (svg && size.w) fitView();
  }, [svg, size, fitView]);

  // Масштаб колесом: слушатель должен быть не-пассивным, чтобы гасить скролл.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [svg, zoomBy]);

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
    setGrabbing(true);
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const d = dragRef.current;
    if (!d) return;
    setOffset({ x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) });
  }

  function onPointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    if (dragRef.current) e.currentTarget.releasePointerCapture(e.pointerId);
    dragRef.current = null;
    setGrabbing(false);
  }

  const ctl =
    "flex h-7 w-7 items-center justify-center rounded-md text-ink-300 transition-colors hover:bg-ink-800 hover:text-white";

  return (
    <div className={className}>
      {showSettings && (
        <DiagramSettingsPanel
          settings={settings}
          patch={onPatchSettings}
          reset={onResetSettings}
          onClose={() => setShowSettings(false)}
        />
      )}

      {error ? (
        <div className="pre-dump text-dragon-400">
          Не удалось отрисовать диаграмму: {error}
          {"\n\n"}
          {code}
        </div>
      ) : !svg ? (
        <div className="py-10 text-center text-sm text-ink-300">Рисуем диаграмму…</div>
      ) : (
        <>
          <div className="relative">
            {/* Панель управления видом */}
            <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg border border-ink-700 bg-ink-900/90 p-1 backdrop-blur">
              <button
                className={ctl}
                onClick={() => zoomBy(1 / 1.25)}
                title="Отдалить (колесо вниз)"
              >
                <Minus size={15} />
              </button>
              <span className="w-11 text-center text-xs tabular-nums text-ink-300">
                {Math.round(zoom * 100)}%
              </span>
              <button
                className={ctl}
                onClick={() => zoomBy(1.25)}
                title="Приблизить (колесо вверх)"
              >
                <Plus size={15} />
              </button>
              <button className={ctl} onClick={fitView} title="Вписать диаграмму в экран">
                <Maximize2 size={14} />
              </button>
              <button
                className={`${ctl} ${showSettings ? "bg-ink-800 text-white" : ""}`}
                onClick={() => setShowSettings((v) => !v)}
                title="Настройки диаграммы"
              >
                <SlidersHorizontal size={14} />
              </button>
            </div>

            <div
              ref={boxRef}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              className={`relative min-h-[26rem] select-none overflow-hidden rounded-xl border border-ink-700 bg-ink-950 ${
                grabbing ? "cursor-grabbing" : "cursor-grab"
              }`}
            >
              {/* Размер задаётся явно (а не transform: scale) — SVG перерисовывается векторно. */}
              <div
                style={{
                  transform: `translate(${offset.x}px, ${offset.y}px)`,
                  transformOrigin: "0 0",
                  width: size.w * zoom,
                  height: size.h * zoom,
                }}
                className="diagram-canvas"
                dangerouslySetInnerHTML={{ __html: svg }}
              />
            </div>
          </div>

          <p className="mt-2 text-center text-xs text-ink-400">
            Колесо мыши — приблизить/отдалить, перетаскивание — сдвинуть, шестерёнка — настройки.
          </p>
        </>
      )}
    </div>
  );
}
