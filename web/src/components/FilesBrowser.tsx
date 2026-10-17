import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ChevronRight,
  FileText,
  Folder,
  Home,
} from "lucide-react";
import { api, ApiError, type FileContent, type FileEntry } from "../api";
import { ErrorNote, Spinner, formatBytes } from "./ui";

const MAX_RENDER_LINES = 5000;

/** Хлебные крошки пути: [корень, сегмент, сегмент, …] */
function crumbs(path: string): { label: string; to: string }[] {
  const parts = path ? path.split("/") : [];
  const out = [{ label: "корень", to: "" }];
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    out.push({ label: part, to: acc });
  }
  return out;
}

/**
 * Браузер кода проекта: список файлов/папок + просмотр файла.
 * Работает даже до анализа — по реальным файлам в хранилище.
 */
export function FilesBrowser({ projectId }: { projectId: number }) {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<FileEntry[] | null>(null);
  const [listError, setListError] = useState("");
  const [listing, setListing] = useState(false);

  const [openPath, setOpenPath] = useState<string | null>(null);
  const [file, setFile] = useState<FileContent | null>(null);
  const [fileError, setFileError] = useState("");
  const [loadingFile, setLoadingFile] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setListing(true);
    setListError("");
    api
      .files(projectId, path)
      .then((res) => {
        if (!cancelled) setEntries(res.entries);
      })
      .catch((e: unknown) => {
        if (!cancelled) setListError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setListing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, path]);

  async function openFile(p: string) {
    setLoadingFile(true);
    setFileError("");
    setFile(null);
    try {
      const res = await api.file(projectId, p);
      setFile(res);
      setOpenPath(p);
    } catch (e) {
      setFileError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoadingFile(false);
    }
  }

  function closeFile() {
    setFile(null);
    setFileError("");
    setOpenPath(null);
  }

  // API возвращает только прямых потомков запрошенной папки.
  const rows = openPath || fileError ? null : (entries ?? []);

  const lineCount = file && !file.binary ? file.content.split("\n").length : 0;
  const gutter =
    file && !file.binary
      ? Array.from(
          { length: Math.min(lineCount, MAX_RENDER_LINES) },
          (_, i) => String(i + 1),
        ).join("\n")
      : "";

  return (
    <div>
      {listError && <ErrorNote>{listError}</ErrorNote>}

      {fileError && (
        <div className="mb-4">
          <ErrorNote>{fileError}</ErrorNote>
        </div>
      )}

      {loadingFile && <Spinner label="Открываем файл…" />}

      {file && !loadingFile && (
        <div className="animate-fade-up">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <button
                onClick={closeFile}
                className="mb-1.5 inline-flex items-center gap-1.5 text-sm text-ink-300 hover:text-white"
              >
                <ArrowLeft size={14} /> к списку файлов
              </button>
              <div className="flex flex-wrap items-center gap-2">
                <FileText size={15} className="text-dragon-500" />
                <span className="break-all font-mono text-sm text-white">{file.path}</span>
                <span className="text-xs text-ink-400">{formatBytes(file.size)}</span>
              </div>
            </div>
            {file.truncated && (
              <span className="text-xs text-amber-400">
                показаны первые {formatBytes(512 * 1024)}
              </span>
            )}
          </div>

          {file.binary ? (
            <div className="code-view py-10 text-center text-sm text-ink-400">
              Двоичный файл — содержимое не отображается.
            </div>
          ) : (
            <div className="code-view flex">
              <pre className="gutter">{gutter}</pre>
              <pre className="code">
                {lineCount > MAX_RENDER_LINES
                  ? file.content.split("\n").slice(0, MAX_RENDER_LINES).join("\n")
                  : file.content}
              </pre>
            </div>
          )}
          {lineCount > MAX_RENDER_LINES && (
            <p className="mt-2 text-xs text-ink-400">
              Файл длинный — показано первых {MAX_RENDER_LINES} строк из {lineCount}.
            </p>
          )}
        </div>
      )}

      {!openPath && !fileError && (
        <>
          {/* Крошки */}
          <div className="mb-3 flex flex-wrap items-center gap-1 text-sm">
            <button
              onClick={() => setPath("")}
              className="inline-flex items-center gap-1.5 text-ink-200 hover:text-white"
              title="В корень"
            >
              <Home size={14} /> корень
            </button>
            {crumbs(path)
              .slice(1)
              .map((c, i, arr) => (
                <span key={c.to} className="flex items-center gap-1">
                  <ChevronRight size={13} className="text-ink-500" />
                  <button
                    onClick={() => setPath(c.to)}
                    className={`hover:text-white ${
                      i === arr.length - 1 ? "text-white" : "text-ink-300"
                    }`}
                  >
                    {c.label}
                  </button>
                </span>
              ))}
          </div>

          <div className="card overflow-hidden">
            <div className="grid grid-cols-[1fr_7rem] gap-2 border-b border-ink-700 bg-ink-800 px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-400">
              <span>Имя</span>
              <span className="text-right">Размер</span>
            </div>

            {listing && !entries && <Spinner label="Читаем файлы…" />}

            {!listing && rows && rows.length === 0 && (
              <div className="px-4 py-10 text-center text-sm text-ink-400">
                Папка пуста.
              </div>
            )}

            {rows && rows.length > 0 && (
              <ul className="divide-y divide-ink-800">
                {rows.map((entry) => (
                  <li key={entry.path}>
                    <button
                      onClick={() =>
                        entry.type === "dir" ? setPath(entry.path) : void openFile(entry.path)
                      }
                      className="grid w-full grid-cols-[1fr_7rem] items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-ink-800"
                    >
                      <span className="flex min-w-0 items-center gap-2.5">
                        {entry.type === "dir" ? (
                          <Folder size={16} className="shrink-0 text-dragon-500" />
                        ) : (
                          <FileText size={16} className="shrink-0 text-ink-400" />
                        )}
                        <span
                          className={`truncate ${
                            entry.type === "dir" ? "text-white" : "text-ink-200"
                          }`}
                        >
                          {entry.name}
                        </span>
                      </span>
                      <span className="text-right text-xs text-ink-400">
                        {entry.type === "dir" ? "—" : formatBytes(entry.size)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
