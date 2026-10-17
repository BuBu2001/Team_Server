import { useState } from "react";
import { Plus, Search, FolderGit2 } from "lucide-react";
import { api } from "../api";
import { useAsync } from "../hooks";
import { Button, EmptyState, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { ProjectCard } from "../components/ProjectCard";
import { UploadDialog } from "../components/UploadDialog";

export function Projects() {
  const projects = useAsync(() => api.projects(), []);
  const [query, setQuery] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);

  const all = projects.data?.projects ?? [];
  const filtered = all.filter(
    (p) =>
      p.name.toLowerCase().includes(query.toLowerCase()) ||
      p.owner.toLowerCase().includes(query.toLowerCase()),
  );
  const mine = filtered.filter((p) => p.mine);
  const others = filtered.filter((p) => !p.mine);

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Проекты"
        subtitle={`Всего на сервере: ${all.length}`}
        actions={
          <Button icon={<Plus size={16} />} onClick={() => setUploadOpen(true)}>
            Загрузить проект
          </Button>
        }
      />

      <div className="relative mb-6 max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по названию или автору…"
          className="w-full rounded-lg border border-ink-600 bg-ink-900 py-2 pl-9 pr-3 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
        />
      </div>

      {projects.loading ? (
        <Spinner label="Загружаем проекты…" />
      ) : projects.error ? (
        <ErrorNote>{projects.error}</ErrorNote>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<FolderGit2 size={26} />}
          title={query ? "Ничего не найдено" : "Проектов пока нет"}
          description={
            query
              ? "Попробуйте изменить запрос."
              : "Загрузите zip-архив проекта или укажите ссылку на git-репозиторий."
          }
          action={
            !query && (
              <Button icon={<Plus size={16} />} onClick={() => setUploadOpen(true)}>
                Загрузить проект
              </Button>
            )
          }
        />
      ) : (
        <div className="flex flex-col gap-8">
          {mine.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
                Мои проекты
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {mine.map((p) => (
                  <ProjectCard key={p.id} project={p} />
                ))}
              </div>
            </section>
          )}
          {others.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
                Проекты команды
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {others.map((p) => (
                  <ProjectCard key={p.id} project={p} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onDone={projects.reload}
      />
    </div>
  );
}
