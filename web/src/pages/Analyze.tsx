import { useState, type FormEvent } from "react";
import { Search, GitBranch, ExternalLink, FileText, Braces } from "lucide-react";
import { api, ApiError, type IngestResult } from "../api";
import { Button, Card, ErrorNote, PageHeader } from "../components/ui";

type Tab = "summary" | "tree" | "digest";

export function Analyze() {
  const [url, setUrl] = useState("");
  const [maxSize, setMaxSize] = useState(100);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [tab, setTab] = useState<Tab>("summary");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!url.trim()) {
      setError("Укажите ссылку на репозиторий");
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      setResult(await api.ingest(url.trim(), maxSize, token.trim()));
      setTab("summary");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Анализ репозитория"
        subtitle="Разберите любой публичный git-репозиторий по ссылке — без сохранения в проекты."
        actions={
          <a href="/site" target="_blank" rel="noreferrer">
            <Button variant="outline" icon={<ExternalLink size={16} />}>
              Сайт gitingest
            </Button>
          </a>
        }
      />

      <Card className="mb-6 p-5">
        <form onSubmit={submit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-200">Ссылка на репозиторий</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/user/repo  или  git@github.com:user/repo.git"
              className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-200">Макс. размер файла, КБ</span>
              <input
                type="number"
                min={1}
                value={maxSize}
                onChange={(e) => setMaxSize(Number(e.target.value) || 100)}
                className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-white focus:border-dragon-600 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-200">
                Токен доступа <span className="text-ink-400">(для приватных репозиториев)</span>
              </span>
              <input
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="ghp_…"
                className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
              />
            </label>
          </div>

          <ErrorNote>{error}</ErrorNote>

          <div className="flex items-center gap-3">
            <Button type="submit" loading={busy} icon={<Search size={16} />}>
              Анализировать
            </Button>
            <span className="flex items-center gap-1 text-xs text-ink-400">
              <GitBranch size={13} /> клонируется во временную папку и удаляется
            </span>
          </div>
        </form>
      </Card>

      {result && (
        <Card className="p-5">
          <div className="mb-4 flex flex-wrap gap-1 border-b border-ink-800">
            {(
              [
                { id: "summary", label: "Сводка", icon: FileText },
                { id: "tree", label: "Структура", icon: GitBranch },
                { id: "digest", label: "Digest", icon: Braces },
              ] as { id: Tab; label: string; icon: typeof FileText }[]
            ).map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium ${
                  tab === id
                    ? "border-dragon-600 text-white"
                    : "border-transparent text-ink-300 hover:text-white"
                }`}
              >
                <Icon size={15} /> {label}
              </button>
            ))}
          </div>
          <pre className="pre-dump">
            {tab === "summary" ? result.summary : tab === "tree" ? result.tree : result.digest}
          </pre>
        </Card>
      )}
    </div>
  );
}
