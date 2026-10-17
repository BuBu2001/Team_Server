import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import mermaid from "mermaid";
import { Maximize2, Minus, Plus } from "lucide-react";

let initialized = false;
function ensureInit() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "strict",
    fontFamily: "var(--font-sans)",
    themeVariables: {
      darkMode: true,
      background: "#0e0e11",
      primaryColor: "#1b1b21",
      primaryTextColor: "#e5e5ea",
      primaryBorderColor: "#dc2626",
      lineColor: "#b91c1c",
      fontFamily: "Inter, system-ui, sans-serif",
    },
  });
  initialized = true;
}

let counter = 0;

const MIN_ZOOM = 0.2;
const MAX_ZOOM = 6;

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

/**
 * Диаграмма mermaid с масштабированием и перемещением:
 * колесо мыши — приблизить/отдалить, перетаскивание — сдвинуть,
 * кнопки в углу — шаговое масштабирование и сброс вида.
 */
export function Mermaid({ code, className = "" }: { code: string; className?: string }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [grabbing, setGrabbing] = useState(false);

  const idRef = useRef(`mmd-${++counter}`);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

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

  useEffect(() => {
    let cancelled = false;
    ensureInit();
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
  }, [code]);

  // Новая диаграмма — вид по умолчанию.
  useEffect(() => {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  }, [code]);

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

  function resetView() {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  }

  if (error) {
    return (
      <div className="pre-dump text-dragon-400">
        Не удалось отрисовать диаграмму: {error}
        {"\n\n"}
        {code}
      </div>
    );
  }
  if (!svg) {
    return <div className="py-10 text-center text-sm text-ink-300">Рисуем диаграмму…</div>;
  }

  const ctl =
    "flex h-7 w-7 items-center justify-center rounded-md text-ink-300 transition-colors hover:bg-ink-800 hover:text-white";

  return (
    <div className={className}>
      <div className="relative">
        {/* Панель управления видом */}
        <div className="absolute right-2 top-2 z-10 flex items-center gap-1 rounded-lg border border-ink-700 bg-ink-900/90 p-1 backdrop-blur">
          <button className={ctl} onClick={() => zoomBy(1 / 1.25)} title="Отдалить (колесо вниз)">
            <Minus size={15} />
          </button>
          <span className="w-11 text-center text-xs tabular-nums text-ink-300">
            {Math.round(zoom * 100)}%
          </span>
          <button className={ctl} onClick={() => zoomBy(1.25)} title="Приблизить (колесо вверх)">
            <Plus size={15} />
          </button>
          <button className={ctl} onClick={resetView} title="Сбросить вид (100%)">
            <Maximize2 size={14} />
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
          <div
            style={{
              transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
              transformOrigin: "0 0",
              display: "inline-block",
            }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      </div>

      <p className="mt-2 text-center text-xs text-ink-400">
        Колесо мыши — приблизить/отдалить, перетаскивание — сдвинуть, кнопка в углу — сброс.
      </p>
    </div>
  );
}
