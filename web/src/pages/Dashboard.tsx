import { Link } from "react-router-dom";
import {
  FolderGit2,
  Users,
  Activity,
  CalendarClock,
  Plus,
  ArrowRight,
  ScrollText,
} from "lucide-react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useAsync } from "../hooks";
import { Badge, Button, Card, ErrorNote, Spinner, formatDate, PageHeader } from "../components/ui";
import { ProjectCard } from "../components/ProjectCard";
import { UploadDialog } from "../components/UploadDialog";
import { useState } from "react";

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof FolderGit2;
  label: string;
  value: number | string;
}) {
  return (
    <Card className="flex items-center gap-4 p-4">
      <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-dragon-900 bg-dragon-900/20 text-dragon-500">
        <Icon size={20} />
      </span>
      <div>
        <div className="text-2xl font-bold text-white">{value}</div>
        <div className="text-xs text-ink-300">{label}</div>
      </div>
    </Card>
  );
}

export function Dashboard() {
  const { user } = useAuth();
  const [uploadOpen, setUploadOpen] = useState(false);

  const stats = useAsync(() => api.stats(), []);
  const projects = useAsync(() => api.projects(), []);
  const journal = useAsync(() => api.journal(8), []);

  const all = projects.data?.projects ?? [];
  const mine = all.filter((p) => p.mine);

  function reloadAll() {
    stats.reload();
    projects.reload();
    journal.reload();
  }

  return (
    <div className="animate-fade-up">
      <PageHeader
        title={`Привет, ${user?.username}!`}
        subtitle={
          user?.role === "host"
            ? "Режим Host: вы сервер этой сети — доступны все проекты и журнал команды."
            : "Режим Pi: отправляйте проекты и просматривайте свои результаты."
        }
        actions={
          <Button icon={<Plus size={16} />} onClick={() => setUploadOpen(true)}>
            Загрузить проект
          </Button>
        }
      />

      {/* Статистика */}
      <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={FolderGit2} label="Моих проектов" value={stats.data?.my_projects ?? mine.length} />
        <StatCard icon={Activity} label="Всего проектов" value={stats.data?.projects ?? all.length} />
        <StatCard icon={Users} label="Участников" value={stats.data?.users ?? "—"} />
        <StatCard
          icon={CalendarClock}
          label="Действий за сегодня"
          value={stats.data?.actions_today ?? "—"}
        />
      </div>

      {/* Мои проекты */}
      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Мои проекты</h2>
          <Link
            to="/projects"
            className="flex items-center gap-1 text-sm text-dragon-500 hover:text-dragon-400"
          >
            все проекты <ArrowRight size={14} />
          </Link>
        </div>
        {projects.loading ? (
          <Spinner />
        ) : projects.error ? (
          <ErrorNote>{projects.error}</ErrorNote>
        ) : mine.length === 0 ? (
          <Card className="flex flex-col items-center gap-3 p-8 text-center">
            <FolderGit2 size={28} className="text-ink-400" />
            <p className="text-sm text-ink-300">
              У вас пока нет проектов. Загрузите первый — анализ появится здесь.
            </p>
            <Button icon={<Plus size={16} />} onClick={() => setUploadOpen(true)}>
              Загрузить проект
            </Button>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {mine.map((p) => (
              <ProjectCard key={p.id} project={p} />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Все проекты */}
        <section>
          <h2 className="mb-3 text-lg font-semibold text-white">Проекты команды</h2>
          <Card className="divide-y divide-ink-800">
            {all.slice(0, 8).map((p) => (
              <Link
                key={p.id}
                to={`/projects/${p.id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-ink-800"
              >
                <FolderGit2 size={16} className="text-dragon-500" />
                <span className="truncate text-sm font-medium text-white">{p.name}</span>
                <span className="truncate text-xs text-ink-400">{p.owner}</span>
                <span className="ml-auto">
                  {p.analyzed ? <Badge tone="green">готов</Badge> : <Badge tone="amber">черновик</Badge>}
                </span>
              </Link>
            ))}
            {all.length === 0 && (
              <p className="px-4 py-6 text-center text-sm text-ink-400">Проектов пока нет</p>
            )}
          </Card>
        </section>

        {/* Журнал */}
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Последние действия</h2>
            <Link
              to="/journal"
              className="flex items-center gap-1 text-sm text-dragon-500 hover:text-dragon-400"
            >
              весь журнал <ScrollText size={14} />
            </Link>
          </div>
          <Card className="divide-y divide-ink-800">
            {(journal.data?.rows ?? []).map((a, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                <Badge tone={a.user === user?.username ? "red" : "neutral"}>{a.user}</Badge>
                <span className="truncate text-sm text-ink-200">{a.action}</span>
                <span className="truncate text-xs text-ink-400">{a.details}</span>
                <span className="ml-auto shrink-0 text-xs text-ink-400">{formatDate(a.ts)}</span>
              </div>
            ))}
            {(journal.data?.rows ?? []).length === 0 && (
              <p className="px-4 py-6 text-center text-sm text-ink-400">Действий пока нет</p>
            )}
          </Card>
        </section>
      </div>

      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onDone={reloadAll} />
    </div>
  );
}
