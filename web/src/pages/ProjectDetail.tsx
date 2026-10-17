import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Play,
  Download,
  FileText,
  Braces,
  Image as ImageIcon,
  GitBranch,
  Trash2,
  Lock,
  User,
  Calendar,
} from "lucide-react";
import { api, exportUrl, digestUrl, pngUrl, ApiError, type Analysis, type Diagram } from "../api";
import { useAuth } from "../auth";
import { useAsync } from "../hooks";
import { Badge, Button, Card, ErrorNote, Spinner, formatDate } from "../components/ui";
import { Mermaid } from "../components/Mermaid";

type Tab = "overview" | "diagram" | "png" | "digest";

const TABS: { id: Tab; label: string; icon: typeof FileText }[] = [
  { id: "overview", label: "Обзор и дерево", icon: FileText },
  { id: "diagram", label: "Диаграмма", icon: GitBranch },
  { id: "png", label: "PNG", icon: ImageIcon },
  { id: "digest", label: "Digest", icon: Braces },
];

export function ProjectDetail() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const navigate = useNavigate();
  const { user } = useAuth();

  const detail = useAsync(() => api.project(id), [id]);

  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [diagram, setDiagram] = useState<Diagram | null>(null);
  const [digest, setDigest] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [digestError, setDigestError] = useState("");

  const project = detail.data?.project ?? null;

  // Первичная гидратация: анализ уже сохранён на сервере.
  useEffect(() => {
    if (detail.data) setAnalysis(detail.data.analysis);
  }, [detail.data]);

  useEffect(() => {
    if (project?.has_diagram) {
      api
        .diagram(id)
        .then(setDiagram)
        .catch(() => setDiagram(null));
    }
  }, [project?.has_diagram, id]);

  async function runAnalyze() {
    setBusy(true);
    setError("");
    try {
      const result = await api.analyze(id);
      setAnalysis(result);
      if (result.diagram_code) {
        setDiagram({
          code: result.diagram_code,
          kind: result.diagram_kind ?? "tree",
          note: result.diagram_note,
        });
      }
      setDigest(null);
      detail.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openDigest() {
    setTab("digest");
    if (digest !== null || digestError) return;
    try {
      setDigest(await api.digest(id));
    } catch (e) {
      setDigestError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function removeProject() {
    if (!confirm(`Удалить проект «${project?.name}»? Действие необратимо.`)) return;
    try {
      await api.removeProject(id);
      navigate("/projects");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  if (detail.loading) return <Spinner label="Загружаем проект…" />;
  if (detail.error)
    return (
      <div>
        <Link to="/projects" className="mb-4 inline-flex items-center gap-1 text-sm text-ink-300 hover:text-white">
          <ArrowLeft size={14} /> к проектам
        </Link>
        <ErrorNote>{detail.error}</ErrorNote>
      </div>
    );
  if (!project) return null;

  return (
    <div className="animate-fade-up">
      <Link
        to="/projects"
        className="mb-4 inline-flex items-center gap-1 text-sm text-ink-300 hover:text-white"
      >
        <ArrowLeft size={14} /> к проектам
      </Link>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-white">{project.name}</h1>
            {project.analyzed ? <Badge tone="green">проанализирован</Badge> : <Badge tone="amber">черновик</Badge>}
            {!project.access && (
              <Badge tone="neutral">
                <Lock size={11} /> нет доступа к материалам
              </Badge>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-4 text-sm text-ink-300">
            <span className="flex items-center gap-1.5">
              <User size={14} /> {project.owner}
            </span>
            <span className="flex items-center gap-1.5">
              <Calendar size={14} /> {formatDate(project.added_at)}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {project.access && (
            <Button onClick={runAnalyze} loading={busy} icon={<Play size={16} />}>
              {project.analyzed ? "Переанализировать" : "Проанализировать"}
            </Button>
          )}
          {project.access && (
            <>
              <a href={pngUrl(id)} download>
                <Button variant="outline" icon={<ImageIcon size={16} />} disabled={!project.has_png}>
                  PNG
                </Button>
              </a>
              <a href={digestUrl(id)} download>
                <Button variant="outline" icon={<Download size={16} />} disabled={!project.has_digest}>
                  Digest
                </Button>
              </a>
              <a href={exportUrl(id)} target="_blank" rel="noreferrer">
                <Button variant="outline" icon={<Braces size={16} />}>
                  JSON
                </Button>
              </a>
            </>
          )}
          {(project.mine || user?.role === "host") && (
            <Button variant="danger" icon={<Trash2 size={16} />} onClick={removeProject}>
              Удалить
            </Button>
          )}
        </div>
      </div>

      <ErrorNote>{error}</ErrorNote>

      {!analysis && !busy && (
        <Card className="mt-4 flex flex-col items-center gap-3 p-10 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full border border-dragon-900 bg-dragon-900/20 text-dragon-500">
            <Play size={24} />
          </div>
          <p className="text-sm text-ink-300">
            Проект ещё не проанализирован. Запустите анализ, чтобы получить структуру,
            digest, диаграмму кода и PNG.
          </p>
          {project.access && (
            <Button onClick={runAnalyze} loading={busy} icon={<Play size={16} />}>
              Проанализировать
            </Button>
          )}
        </Card>
      )}

      {analysis && (
        <>
          {/* Табы */}
          <div className="mb-4 flex flex-wrap gap-1 border-b border-ink-800">
            {TABS.map(({ id: tid, label, icon: Icon }) => (
              <button
                key={tid}
                onClick={() => (tid === "digest" ? openDigest() : setTab(tid))}
                className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  tab === tid
                    ? "border-dragon-600 text-white"
                    : "border-transparent text-ink-300 hover:text-white"
                }`}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </div>

          {tab === "overview" && (
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
                  Сводка
                </h3>
                <pre className="pre-dump">{analysis.summary || "—"}</pre>
              </div>
              <div>
                <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
                  Структура файлов
                </h3>
                <pre className="pre-dump">{analysis.tree || "—"}</pre>
              </div>
            </div>
          )}

          {tab === "diagram" && (
            <Card className="p-4">
              {analysis.diagram_kind && (
                <div className="mb-3 flex items-center gap-2">
                  <Badge tone="red">
                    {analysis.diagram_kind === "code" ? "архитектуры кода" : "по дереву файлов"}
                  </Badge>
                  {analysis.diagram_note && (
                    <span className="text-xs text-ink-400">{analysis.diagram_note}</span>
                  )}
                </div>
              )}
              {diagram?.code ? (
                <>
                  <Mermaid code={diagram.code} />
                  <details className="mt-4">
                    <summary className="cursor-pointer text-xs text-ink-400">исходный mermaid-код</summary>
                    <pre className="pre-dump mt-2">{diagram.code}</pre>
                  </details>
                </>
              ) : (
                <p className="py-10 text-center text-sm text-ink-400">Диаграмма недоступна</p>
              )}
            </Card>
          )}

          {tab === "png" && (
            <Card className="p-4">
              {project.has_png ? (
                <img
                  src={pngUrl(id)}
                  alt={`Структура проекта ${project.name}`}
                  className="mx-auto max-w-full rounded-lg border border-ink-700 bg-ink-950"
                />
              ) : (
                <p className="py-10 text-center text-sm text-ink-400">PNG ещё не создан</p>
              )}
            </Card>
          )}

          {tab === "digest" && (
            <Card className="p-4">
              {digestError ? (
                <ErrorNote>{digestError}</ErrorNote>
              ) : digest === null ? (
                <Spinner label="Загружаем digest…" />
              ) : (
                <pre className="pre-dump">{digest}</pre>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  );
}
