import { useState } from "react";
import { ScrollText, Search } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useAsync } from "../hooks";
import { Badge, Card, EmptyState, ErrorNote, PageHeader, Spinner, formatDate } from "../components/ui";

export function Journal() {
  const { user } = useAuth();
  const journal = useAsync(() => api.journal(300), []);
  const [query, setQuery] = useState("");

  const rows = (journal.data?.rows ?? []).filter((r) => {
    const q = query.toLowerCase();
    return (
      !q ||
      r.user.toLowerCase().includes(q) ||
      r.action.toLowerCase().includes(q) ||
      r.details.toLowerCase().includes(q) ||
      r.project.toLowerCase().includes(q)
    );
  });

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Журнал работы"
        subtitle={
          user?.role === "host"
            ? "Все действия команды — веб-панель и консоль Host ⇄ Pi."
            : "Ваши действия (и веб-панель, и консоль)."
        }
      />

      <div className="relative mb-4 max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по пользователю, действию, проекту…"
          className="w-full rounded-lg border border-ink-600 bg-ink-900 py-2 pl-9 pr-3 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
        />
      </div>

      {journal.loading ? (
        <Spinner label="Загружаем журнал…" />
      ) : journal.error ? (
        <ErrorNote>{journal.error}</ErrorNote>
      ) : rows.length === 0 ? (
        <EmptyState icon={<ScrollText size={26} />} title="Записей нет" description={query ? "Измените запрос поиска." : "Действия появятся здесь по мере работы."} />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-800 text-left text-xs uppercase tracking-wide text-ink-400">
                  <th className="px-4 py-3">Время</th>
                  <th className="px-4 py-3">Пользователь</th>
                  <th className="px-4 py-3">Действие</th>
                  <th className="px-4 py-3">Детали</th>
                  <th className="px-4 py-3">Проект</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {rows.map((r, i) => (
                  <tr key={i} className="hover:bg-ink-800/50">
                    <td className="whitespace-nowrap px-4 py-2.5 text-ink-300">{formatDate(r.ts)}</td>
                    <td className="px-4 py-2.5">
                      <Badge tone={r.user === user?.username ? "red" : "neutral"}>{r.user}</Badge>
                    </td>
                    <td className="px-4 py-2.5 font-medium text-ink-200">{r.action}</td>
                    <td className="px-4 py-2.5 text-ink-300">{r.details}</td>
                    <td className="px-4 py-2.5 text-ink-300">{r.project || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
