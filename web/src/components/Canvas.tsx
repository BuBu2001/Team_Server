import { useCallback, useEffect, useRef, useState } from "react";
import type { ComponentProps } from "react";
import { Excalidraw, serializeAsJSON } from "@excalidraw/excalidraw";
import { Loader2, Save } from "lucide-react";
import { api, ApiError, type CanvasData } from "../api";
import { Button, ErrorNote, formatDate } from "./ui";

/** Автосохранение через эту паузу после последнего движения мыши. */
const AUTOSAVE_MS = 1200;

/**
 * Пропсы Excalidraw с постоянной ссылкой: компонент обёрнут в React.memo,
 * и каждая новая ссылка (на `onChange`, `UIOptions` и т.п.) заставляла бы его
 * перерисовываться при любом обновлении статуса — Excalidraw отвечал бы на это
 * своим onChange, и флаг «не сохранено» зацикливался бы.
 */
const UI_OPTS: ComponentProps<typeof Excalidraw>["UIOptions"] = {
  canvasActions: { toggleTheme: false, loadScene: false },
};

/** Типы выводятся из компонента: пакет не экспортирует их наружу. */
type InitialData = Exclude<
  ComponentProps<typeof Excalidraw>["initialData"],
  null | undefined | Promise<unknown>
>;
type ChangeArgs = Parameters<NonNullable<ComponentProps<typeof Excalidraw>["onChange"]>>;

type SaveStatus = "loading" | "ready" | "saving" | "error";

/** Ломаная/чужая сцена не должна ронять вкладку — открываем пустой холст. */
function parseScene(scene: string): InitialData {
  try {
    const data: unknown = JSON.parse(scene);
    if (data && typeof data === "object") return data as InitialData;
  } catch {
    // повреждённый JSON — ниже откроем пустой холст
  }
  return { elements: [] };
}

/** Пустая сцена: нет ни фигур, ни встроенных картинок. */
function isEmptyScene(scene: string): boolean {
  try {
    const data = JSON.parse(scene) as { elements?: unknown[]; files?: object };
    return !(data.elements?.length) && !Object.keys(data.files || {}).length;
  } catch {
    return false;
  }
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/**
 * Нужен ли PUT: сцена отличается от серверной. Две пустые сцены считаем
 * равными — сервер отдаёт заглушку чуть иначе, чем её сериализует Excalidraw,
 * и без этой оговорки каждое открытие вкладки плодило бы запись в журнале.
 */
function needsSave(json: string, saved: string): boolean {
  if (json === saved) return false;
  return !(isEmptyScene(json) && isEmptyScene(saved));
}

/**
 * Вкладка «Холст»: рисунок Excalidraw, привязанный к проекту.
 *
 * Холст — сущность платформы, а не файла репозитория: он хранится в БД
 * сервера и в git никогда не попадает. Изменения уходят на сервер через
 * дебаунс (AUTOSAVE_MS) плюс вручную по кнопке/Ctrl+S.
 */
export function Canvas({
  projectId,
  projectName,
  canAccess,
}: {
  projectId: number;
  projectName: string;
  canAccess: boolean;
}) {
  const [initial, setInitial] = useState<InitialData | null>(null);
  const [meta, setMeta] = useState<CanvasData | null>(null);
  const [status, setStatus] = useState<SaveStatus>("loading");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");

  const savedRef = useRef("");                       // последняя сцена на сервере
  const latestRef = useRef<ChangeArgs | null>(null); // последние элементы с холста
  const timerRef = useRef<number | undefined>(undefined);
  const inFlightRef = useRef(false);
  const queuedRef = useRef(false);

  // Сохраняет то, что сейчас на холсте (или отменяет отложенное, если уже летит).
  const persistLatest = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
    if (inFlightRef.current) {
      queuedRef.current = true; // не даём запросам обогнать друг друга
      return;
    }
    const latest = latestRef.current;
    if (!latest) return;
    let json: string;
    try {
      json = serializeAsJSON(latest[0], latest[1], latest[2], "local");
    } catch (e) {
      // падение сериализации не должно остаться незамеченным
      setError(`Не удалось подготовить сцену: ${errText(e)}`);
      setStatus("error");
      return;
    }
    if (!needsSave(json, savedRef.current)) {
      setDirty(false);
      return;
    }
    inFlightRef.current = true;
    setStatus("saving");
    setError("");
    api
      .saveCanvas(projectId, json)
      .then((d) => {
        savedRef.current = json;
        setMeta(d);
        setDirty(false);
        setStatus("ready");
      })
      .catch((e) => {
        setError(errText(e));
        setStatus("error");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (queuedRef.current) {
          queuedRef.current = false;
          window.setTimeout(() => void persistLatest(), 0);
        }
      });
  }, [projectId]);

  // Загрузка сцены проекта (и дописывание несохранённого при уходе со вкладки).
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError("");
    setDirty(false);
    setInitial(null);
    setMeta(null);
    latestRef.current = null;

    api
      .canvas(projectId)
      .then((d) => {
        if (cancelled) return;
        savedRef.current = d.scene;
        setMeta(d);
        setInitial(parseScene(d.scene));
        setStatus("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(errText(e));
        setStatus("error");
      });

    return () => {
      cancelled = true;
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
      const latest = latestRef.current;
      if (latest) {
        try {
          const json = serializeAsJSON(latest[0], latest[1], latest[2], "local");
          if (needsSave(json, savedRef.current)) {
            // вкладку закрыли/переключили — дописываем, что не успели
            void api.saveCanvas(projectId, json).catch(() => {});
          }
        } catch {
          // при размонтировании ошибку сериализации некому показать
        }
      }
    };
  }, [projectId]);

  // Ctrl/Cmd+S — сохранить холст сразу.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        persistLatest();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [persistLatest]);

  // Ссылка постоянная, пока не сменился проект — иначе Excalidraw перерисуется.
  const handleChange = useCallback(
    (...args: ChangeArgs) => {
      latestRef.current = args;
      setDirty(true);
      if (timerRef.current === undefined) {
        timerRef.current = window.setTimeout(() => void persistLatest(), AUTOSAVE_MS);
      }
    },
    [persistLatest],
  );

  if (!canAccess) {
    return (
      <div className="card p-8 text-center text-sm text-ink-300">
        Холст доступен владельцу проекта и Host.
      </div>
    );
  }

  if (status === "error" && initial === null) {
    return <ErrorNote>Не удалось открыть холст: {error}</ErrorNote>;
  }

  if (initial === null) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-ink-300">
        <Loader2 size={16} className="animate-spin" /> Открываем холст…
      </div>
    );
  }

  const statusText =
    status === "saving"
      ? "Сохранение…"
      : status === "error"
        ? "Ошибка сохранения"
        : dirty
          ? "Есть несохранённые изменения"
          : meta?.updated_at
            ? `Изменено ${formatDate(meta.updated_at)}${meta.updated_by ? ` — ${meta.updated_by}` : ""}`
            : "Холст пока пуст";

  const dotColor =
    status === "error"
      ? "bg-red-500"
      : status === "saving" || dirty
        ? "bg-amber-400"
        : "bg-emerald-500";

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-ink-400">
          <span className={`inline-block h-2 w-2 rounded-full ${dotColor}`} />
          <span>{statusText}</span>
          <span className="text-ink-500">·</span>
          <span className="text-ink-500">{projectName}</span>
        </div>
        <Button
          size="sm"
          variant={dirty ? "primary" : "outline"}
          loading={status === "saving"}
          disabled={!dirty}
          icon={<Save size={14} />}
          onClick={persistLatest}
          title="Сохранить (Ctrl+S)"
        >
          {dirty ? "Сохранить" : "Сохранено"}
        </Button>
      </div>

      <ErrorNote>{error}</ErrorNote>

      <div className="holst-box h-[calc(100vh-17rem)] min-h-[480px] overflow-hidden rounded-xl border border-ink-700 bg-ink-900">
        <Excalidraw
          initialData={initial}
          onChange={handleChange}
          theme="dark"
          name={projectName}
          UIOptions={UI_OPTS}
        />
      </div>

      <p className="mt-2 text-center text-xs text-ink-400">
        Автосохранение через 1,2 с после правки · Ctrl+S — сохранить сразу. Холст хранится
        только на сервере и в git-репозиторий не попадает.
      </p>
    </div>
  );
}
