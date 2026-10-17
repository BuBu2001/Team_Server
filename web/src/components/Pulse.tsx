import { useEffect, useState } from "react";
import { Activity, Clock, FileText, Folder, HardDrive, Users } from "lucide-react";
import { api, ApiError, type PulseData } from "../api";
import { Card, ErrorNote, Spinner, formatBytes, formatDate } from "./ui";

const ACTION_LABELS: Record<string, string> = {
  upload: "загрузка проекта",
  analyze: "анализ (gitingest)",
  delete: "удаление",
  png: "PNG структуры",
  download: "скачивание",
  diagram: "диаграмма",
  api_export: "выгрузка JSON",
  ingest: "разбор репозитория",
  create_repo: "создан репозиторий",
  load_data: "загрузка данных",
};

function label(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** Вкладка «Пульс»: активность проекта из журнала + статистика файлов. */
export function Pulse({ projectId }: { projectId: number }) {
  const [data, setData] = useState<PulseData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api
      .pulse(projectId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (loading) return <Spinner label="Считаем активность…" />;
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!data) return null;

  // «Топ авторов» — по числу действий (аналог Top committers на GitHub Pulse).
  const byUser = new Map<string, number>();
  for (const a of data.actions) byUser.set(a.user, (byUser.get(a.user) ?? 0) + 1);
  const top = [...byUser.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = top.length ? top[0][1] : 1;

  const stats = [
    { label: "Файлов", value: String(data.files), icon: FileText },
    { label: "Папок", value: String(data.dirs), icon: Folder },
    { label: "Размер", value: formatBytes(data.bytes), icon: HardDrive },
    { label: "Действий", value: String(data.actions.length), icon: Activity },
  ];

  return (
    <div className="animate-fade-up flex flex-col gap-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map(({ label: l, value, icon: Icon }) => (
          <Card key={l} className="flex items-center gap-3 p-4">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-dragon-900/30 text-dragon-400">
              <Icon size={18} />
            </span>
            <div>
              <div className="text-lg font-bold text-white">{value}</div>
              <div className="text-xs text-ink-400">{l}</div>
            </div>
          </Card>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
            <Users size={15} /> Кто работал с проектом
          </h3>
          {top.length === 0 ? (
            <p className="py-6 text-center text-sm text-ink-400">Пока нет действий.</p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {top.map(([user, count]) => (
                <li key={user} className="flex items-center gap-3">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-dragon-900/50 text-xs font-bold text-dragon-400">
                    {user[0]?.toUpperCase()}
                  </span>
                  <span className="w-28 truncate text-sm text-ink-200">{user}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded-full bg-ink-800">
                    <span
                      className="block h-full rounded-full bg-dragon-600"
                      style={{ width: `${(count / max) * 100}%` }}
                    />
                  </span>
                  <span className="w-8 text-right text-xs text-ink-400">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="p-4">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
            <Clock size={15} /> Последние действия
          </h3>
          {data.actions.length === 0 ? (
            <p className="py-6 text-center text-sm text-ink-400">Журнал пуст.</p>
          ) : (
            <ul className="flex max-h-80 flex-col gap-2 overflow-auto pr-1">
              {data.actions.slice(0, 40).map((a, i) => (
                <li key={`${a.ts}-${i}`} className="flex flex-col border-b border-ink-800 pb-2 text-sm">
                  <span className="text-ink-200">{label(a.action)}</span>
                  <span className="text-xs text-ink-400">
                    {a.user} · {formatDate(a.ts)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
