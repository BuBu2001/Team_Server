import { useEffect, useMemo, useState } from "react";
import { Download, FileText, GitBranch, Image as ImageIcon, Play } from "lucide-react";
import { api, ApiError, pngUrl, type Analysis } from "../api";
import { Button, Card, ErrorNote, Spinner } from "./ui";

interface Block {
  path: string;
  content: string;
}

/** Разбирает digest gitingest на блоки «файл → содержимое». */
function parseDigest(digest: string): Block[] {
  const out: Block[] = [];
  let path: string | null = null;
  let buf: string[] = [];
  const flush = () => {
    if (path !== null) out.push({ path, content: buf.join("\n").replace(/^\n+|\n+$/g, "") });
  };
  for (const raw of digest.split("\n")) {
    const m = /^FILE:\s*(.+?)\s*$/.exec(raw);
    if (m) {
      flush();
      path = m[1];
      buf = [];
      continue;
    }
    // строки-разделители «====» вокруг заголовка файла не показываем
    if (path !== null && /^=+\s*$/.test(raw)) continue;
    if (path !== null) buf.push(raw);
  }
  flush();
  return out;
}

const MAX_BLOCKS = 250;

/**
 * Вкладка gitingest: картинка со структурой, скачивание в PNG и кликабельная
 * структура — щелчок по файлу перемещает к его блоку с кодом (как по якорю).
 */
export function GitingestView({
  projectId,
  analysis,
  analyzed,
  busy,
  canAccess,
  onAnalyze,
}: {
  projectId: number;
  analysis: Analysis | null;
  analyzed: boolean;
  busy: boolean;
  canAccess: boolean;
  onAnalyze: () => void;
}) {
  const [digest, setDigest] = useState<string | null>(null);
  const [digestError, setDigestError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!analyzed) {
      setDigest(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setDigestError("");
    api
      .digest(projectId)
      .then((text) => {
        if (!cancelled) setDigest(text);
      })
      .catch((e: unknown) => {
        if (!cancelled) setDigestError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, analyzed]);

  const blocks = useMemo(() => (digest ? parseDigest(digest) : []), [digest]);
  const shown = blocks.slice(0, MAX_BLOCKS);

  function jump(index: number) {
    document
      .getElementById(`gitingest-file-${index}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // --- ещё не анализировали: предлагаем запустить gitingest ---
  if (!analyzed) {
    return (
      <Card className="flex flex-col items-center gap-4 p-10 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full border border-dragon-900 bg-dragon-900/20 text-dragon-500">
          <GitBranch size={24} />
        </div>
        <div>
          <h3 className="text-lg font-semibold text-white">Структура кода ещё не построена</h3>
          <p className="mx-auto mt-1 max-w-lg text-sm text-ink-300">
            Нажмите «Получить gitingest» — сервер разберёт файлы проекта, покажет
            картинку со структурой и откроет код по файлам. Результат можно
            сохранить в PNG.
          </p>
        </div>
        {canAccess ? (
          <Button onClick={onAnalyze} loading={busy} icon={<Play size={16} />}>
            Получить gitingest
          </Button>
        ) : (
          <p className="text-xs text-ink-400">Запустить анализ может владелец проекта или Host.</p>
        )}
      </Card>
    );
  }

  return (
    <div className="animate-fade-up">
      {/* Картинка со структурой + скачать в PNG */}
      <Card className="p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
            <ImageIcon size={15} /> Структура проекта
          </h3>
          <a href={pngUrl(projectId)} download={`${projectId}_structure.png`}>
            <Button variant="outline" size="sm" icon={<Download size={15} />}>
              Скачать в PNG
            </Button>
          </a>
        </div>
        <img
          src={pngUrl(projectId)}
          alt="Структура проекта"
          className="mx-auto max-w-full rounded-lg border border-ink-700 bg-ink-950"
        />
      </Card>

      {analysis?.summary && (
        <Card className="mt-4 p-4">
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-ink-400">
            Сводка
          </h3>
          <pre className="pre-dump">{analysis.summary}</pre>
        </Card>
      )}

      {/* Кликабельная структура + блоки кода */}
      {loading && <Spinner label="Готовим структуру и код…" />}
      {digestError && <ErrorNote>{digestError}</ErrorNote>}

      {!loading && blocks.length > 0 && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[18rem_1fr]">
          <aside className="lg:sticky lg:top-16 lg:self-start">
            <Card className="max-h-[32rem] overflow-auto p-2">
              <h3 className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400">
                Файлы ({blocks.length})
              </h3>
              <ul className="flex flex-col">
                {shown.map((b, i) => (
                  <li key={b.path}>
                    <button
                      onClick={() => jump(i)}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-ink-200 hover:bg-ink-800 hover:text-white"
                      title={b.path}
                    >
                      <FileText size={13} className="shrink-0 text-dragon-500" />
                      <span className="truncate font-mono">{b.path}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          </aside>

          <div className="flex min-w-0 flex-col gap-4">
            {shown.map((b, i) => (
              <Card key={b.path} className="overflow-hidden">
                <div
                  id={`gitingest-file-${i}`}
                  className="flex scroll-mt-20 items-center gap-2 border-b border-ink-700 bg-ink-800 px-4 py-2"
                >
                  <FileText size={14} className="text-dragon-500" />
                  <span className="truncate font-mono text-xs text-white">{b.path}</span>
                </div>
                <pre className="max-h-[34rem] overflow-auto p-4 font-mono text-[0.78rem] leading-relaxed text-ink-200">
                  {b.content || "— пусто —"}
                </pre>
              </Card>
            ))}
            {blocks.length > MAX_BLOCKS && (
              <p className="text-center text-xs text-ink-400">
                Показаны первые {MAX_BLOCKS} файлов из {blocks.length}. Полный список — в
                «Digest».
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
