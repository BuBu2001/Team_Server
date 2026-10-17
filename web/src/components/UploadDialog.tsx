import { useState, type FormEvent } from "react";
import { Upload, Link2, FileArchive } from "lucide-react";
import { api, ApiError } from "../api";
import { Button, ErrorNote } from "./ui";
import { Modal } from "./Modal";

type Mode = "archive" | "url";

export function UploadDialog({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>("archive");
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function reset() {
    setName("");
    setSource("");
    setFile(null);
    setError("");
    setBusy(false);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!name.trim()) {
      setError("Укажите название проекта");
      return;
    }
    if (mode === "archive" && !file) {
      setError("Выберите zip-архив проекта");
      return;
    }
    if (mode === "url" && !source.trim()) {
      setError("Укажите ссылку на git-репозиторий");
      return;
    }
    const form = new FormData();
    form.append("name", name.trim());
    if (mode === "archive" && file) {
      form.append("archive", file);
      form.append("source", "");
    } else {
      form.append("source", source.trim());
    }
    setBusy(true);
    try {
      await api.uploadProject(form);
      reset();
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Загрузить проект">
      <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg border border-ink-700 bg-ink-900 p-1">
        <button
          type="button"
          onClick={() => setMode("archive")}
          className={`flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
            mode === "archive" ? "bg-dragon-600 text-white" : "text-ink-300 hover:text-white"
          }`}
        >
          <FileArchive size={16} /> Zip-архив
        </button>
        <button
          type="button"
          onClick={() => setMode("url")}
          className={`flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium ${
            mode === "url" ? "bg-dragon-600 text-white" : "text-ink-300 hover:text-white"
          }`}
        >
          <Link2 size={16} /> Ссылка на репозиторий
        </button>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink-200">Название проекта</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my-app"
            className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
          />
        </label>

        {mode === "archive" ? (
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-200">Zip-архив</span>
            <input
              type="file"
              accept=".zip,application/zip"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full cursor-pointer rounded-lg border border-dashed border-ink-600 bg-ink-900 px-3 py-2 text-sm text-ink-300 file:mr-3 file:rounded-md file:border-0 file:bg-dragon-600 file:px-3 file:py-1.5 file:text-white hover:border-dragon-700"
            />
            <span className="text-xs text-ink-400">
              Архив распакуется на сервере; имя файла внутри не важно.
            </span>
          </label>
        ) : (
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-200">Ссылка на git-репозиторий</span>
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="https://github.com/user/repo"
              className="rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
            />
            <span className="text-xs text-ink-400">
              Репозиторий будет клонирован при первом анализе.
            </span>
          </label>
        )}

        <ErrorNote>{error}</ErrorNote>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" icon={<Upload size={16} />} loading={busy}>
            Загрузить
          </Button>
        </div>
      </form>
    </Modal>
  );
}
