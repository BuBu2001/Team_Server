import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

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

export function Mermaid({ code, className = "" }: { code: string; className?: string }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const idRef = useRef(`mmd-${++counter}`);

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
  return (
    <div
      className={`flex justify-center overflow-auto ${className}`}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
