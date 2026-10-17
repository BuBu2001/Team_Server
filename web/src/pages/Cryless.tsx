import { useState } from "react";
import { Package, Copy, Check, Terminal, RefreshCw } from "lucide-react";
import { useAsync } from "../hooks";
import { Badge, Button, Card, ErrorNote, PageHeader, Spinner } from "../components/ui";

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard может быть недоступен */
    }
  }
  return (
    <div className="group relative">
      <pre className="pre-dump pr-12">{code}</pre>
      <button
        onClick={copy}
        className="absolute right-2 top-2 rounded-md border border-ink-600 bg-ink-900 p-1.5 text-ink-300 hover:text-white"
        aria-label="Скопировать"
      >
        {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
      </button>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-dragon-900 bg-dragon-900/20 text-sm font-bold text-dragon-500">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <h3 className="mb-2 font-semibold text-white">{title}</h3>
        <div className="flex flex-col gap-2">{children}</div>
      </div>
    </div>
  );
}

export function Cryless() {
  const ping = useAsync(() => fetch("/api/cryless/ping").then((r) => r.json()), []);

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Cryless"
        subtitle="Библиотека команд в стиле git: создание репозитория и загрузка данных проекта на сервер."
        actions={
          <Button variant="outline" icon={<RefreshCw size={16} />} onClick={ping.reload}>
            Проверить связь
          </Button>
        }
      />

      <Card className="mb-6 flex flex-wrap items-center gap-3 p-4">
        <Package size={20} className="text-dragon-500" />
        <span className="text-sm text-ink-200">Статус сервера:</span>
        {ping.loading ? (
          <Badge tone="neutral">проверяем…</Badge>
        ) : ping.error ? (
          <Badge tone="red">нет связи</Badge>
        ) : (
          <>
            <Badge tone="green">онлайн</Badge>
            <span className="text-xs text-ink-400">
              версия {ping.data?.version} · репозиториев: {ping.data?.repositories}
            </span>
          </>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-white">
            <Terminal size={18} className="text-dragon-500" /> Как пользоваться
          </h2>
          <div className="flex flex-col gap-6">
            <Step n={1} title="Проверьте соединение">
              <CodeBlock code="cryless connection check" />
            </Step>
            <Step n={2} title="Создайте репозиторий (получите токен)">
              <CodeBlock code="cryless create repository my-project" />
              <p className="text-xs text-ink-400">
                Токен выдаётся один раз и сохраняется в <code>.cryless/config.json</code>.
              </p>
            </Step>
            <Step n={3} title="Загрузите данные проекта">
              <CodeBlock code="cryless load data my-project" />
              <p className="text-xs text-ink-400">
                Текущая папка упакуется и отправится на сервер; анализ запустится автоматически.
              </p>
            </Step>
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="mb-4 text-lg font-semibold text-white">Полезно знать</h2>
          <ul className="flex flex-col gap-3 text-sm text-ink-300">
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-dragon-500" />
              Имя репозитория — только латиница, цифры, <code>. _ -</code>; становится именем
              проекта на сервере.
            </li>
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-dragon-500" />
              Повторная загрузка полностью заменяет содержимое репозитория (как <code>git push</code>).
            </li>
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-dragon-500" />
              Пересоздать репозиторий и получить новый токен:{" "}
              <code>cryless create repository имя --force</code>.
            </li>
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-dragon-500" />
              Загруженные репозитории видны в разделе «Проекты» с анализом и диаграммами.
            </li>
          </ul>
          {ping.error && <ErrorNote>{String(ping.error)}</ErrorNote>}
          {ping.loading && <Spinner label="Связываемся с сервером…" />}
        </Card>
      </div>
    </div>
  );
}
