import { Users, FolderGit2 } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useAsync } from "../hooks";
import { Badge, Card, ErrorNote, PageHeader, Spinner, formatDate } from "../components/ui";

export function Team() {
  const { user } = useAuth();
  const users = useAsync(() => api.users(), []);

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Команда"
        subtitle="Пользователи платформы и их роли. Host выполняет серверную работу, Pi — отправляет проекты."
      />

      {users.loading ? (
        <Spinner label="Загружаем участников…" />
      ) : users.error ? (
        <ErrorNote>{users.error}</ErrorNote>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(users.data?.users ?? []).map((u) => (
            <Card key={u.id} hover className="flex items-center gap-4 p-4">
              <span className="flex h-12 w-12 items-center justify-center rounded-full border border-dragon-900 bg-dragon-900/20 text-lg font-bold text-dragon-500">
                {u.username[0]?.toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="truncate font-semibold text-white">{u.username}</h3>
                  {u.username === user?.username && <Badge tone="red">вы</Badge>}
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs text-ink-300">
                  <Badge tone={u.role === "host" ? "red" : "blue"}>
                    {u.role === "host" ? "Host" : "Pi"}
                  </Badge>
                  <span className="flex items-center gap-1">
                    <FolderGit2 size={12} /> {u.project_count ?? 0}
                  </span>
                </div>
                {u.created_at && (
                  <p className="mt-1 text-xs text-ink-400">с {formatDate(u.created_at)}</p>
                )}
              </div>
            </Card>
          ))}
          {(users.data?.users ?? []).length === 0 && (
            <Card className="col-span-full flex items-center justify-center gap-2 p-8 text-ink-400">
              <Users size={20} /> Пользователей пока нет
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
