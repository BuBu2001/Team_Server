import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  FileText,
  FolderGit2,
  MessageSquare,
  RefreshCw,
  Rocket,
  Wrench,
} from "lucide-react";
import {
  api,
  ApiError,
  type SandboxAnswer,
  type SandboxFileEdit,
  type SandboxStatus,
} from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
} from "../components/ui";
import { FilesBrowser } from "../components/FilesBrowser";

/** Промпт-подсказка, если пользователь не знает, с чего начать. */
const PRESETS = [
  "Проанализируй все файлы проекта и объясни, что делает каждая часть.",
  "Найди в проекте ошибки и точки роста. Отвечай кратко и по делу.",
  "Как запустить этот проект локально? Что нужно установить?",
];

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/**
 * «Песочница»: пользователь выбирает любой проект, смотрит его файлы и
 * разговаривает с ИИ (Qwen) прямо о коде.
 *
 * ИИ работает через настоящий браузер на сервере (app_platform/qwen.py):
 * при нажатии «Открыть браузер» на сервере появляется окно chat.qwen.ai, куда
 * пользователь входит в аккаунт один раз. Вопрос уходит с контекстом файлов
 * выбранного проекта — ИИ видит код и отвечает о нём.
 */
export function Sandbox() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [statusErr, setStatusErr] = useState("");
  const [opening, setOpening] = useState(false);

  const [projects, setProjects] = useState<{ id: number; name: string; access: boolean }[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [projectsErr, setProjectsErr] = useState("");

  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  const [askErr, setAskErr] = useState("");
  const [answer, setAnswer] = useState<SandboxAnswer | null>(null);
  const [log, setLog] = useState<{ prompt: string; answer: string; project: string }[]>([]);

  // применение правки ИИ к файлу проекта на сервере
  const [applying, setApplying] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const timer = useRef<number | undefined>(undefined);

  // ---- статус браузера + список проектов ----
  const refreshStatus = useCallback(() => {
    api
      .sandboxStatus()
      .then((s) => {
        setStatus(s);
        setStatusErr("");
      })
      .catch((e: unknown) => setStatusErr(errText(e)));
  }, []);

  useEffect(() => {
    refreshStatus();
    api
      .projects()
      .then((res) =>
        setProjects(
          res.projects
            .filter((p) => p.access)
            .map((p) => ({ id: p.id, name: p.name, access: p.access })),
        ),
      )
      .catch((e: unknown) => setProjectsErr(errText(e)));
    return () => {
      if (timer.current !== undefined) window.clearTimeout(timer.current);
    };
  }, [refreshStatus]);

  // пока браузер открыт — раз в 5 с подтягиваем статус (вошли ли в аккаунт)
  useEffect(() => {
    if (!status?.open) return;
    timer.current = window.setInterval(refreshStatus, 5000);
    return () => {
      if (timer.current !== undefined) window.clearInterval(timer.current);
    };
  }, [status?.open, refreshStatus]);

  // ---- открыть браузер ----
  const openBrowser = () => {
    setOpening(true);
    setStatusErr("");
    api
      .sandboxBrowser()
      .then((s) => setStatus(s))
      .catch((e: unknown) => setStatusErr(errText(e)))
      .finally(() => setOpening(false));
  };

  // ---- задать вопрос ----
  const ask = () => {
    const text = prompt.trim();
    if (!text || asking) return;
    setAsking(true);
    setAskErr("");
    api
      .sandboxAsk(text, projectId)
      .then((res) => {
        setAnswer(res);
        setApplyMsg(null);
        setLog((prev) => [
          { prompt: text, answer: res.answer, project: res.project },
          ...prev,
        ]);
        setPrompt("");
      })
      .catch((e: unknown) => setAskErr(errText(e)))
      .finally(() => setAsking(false));
  };

  // ---- применить правку из ответа к файлу проекта ----
  const applyEdit = (edit: SandboxFileEdit) => {
    if (!projectId || applying) return;
    setApplying(edit.path);
    setApplyMsg(null);
    api
      .sandboxApply(projectId, edit.path, edit.content)
      .then((res) =>
        setApplyMsg({
          ok: true,
          text: `Файл «${res.path}» обновлён на сервере (${res.bytes} байт).`,
        }),
      )
      .catch((e: unknown) => setApplyMsg({ ok: false, text: errText(e) }))
      .finally(() => setApplying(null));
  };

  const busy = asking;

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Песочница"
        subtitle="Выберите проект, посмотрите файлы и спросите ИИ о коде."
        actions={
          <Button
            icon={<Rocket size={16} />}
            variant={status?.open ? "outline" : "primary"}
            loading={opening}
            onClick={openBrowser}
          >
            {status?.open ? "Браузер ИИ открыт" : "Открыть браузер ИИ"}
          </Button>
        }
      />

      <ErrorNote>{statusErr}</ErrorNote>

      {/* Статус браузера: вошли ли в аккаунт Qwen */}
      <Card className="mb-6 flex flex-wrap items-center gap-3 p-4 text-sm">
        <Bot size={18} className="text-dragon-500" />
        {!status?.open ? (
          <span className="text-ink-300">
            Браузер с ИИ закрыт. Нажмите «Открыть браузер ИИ» — окно появится на
            сервере, там войдите в аккаунт Qwen один раз.
          </span>
        ) : status.logged_in === false ? (
          <span className="text-ink-300">
            Окно Qwen открыто, но аккаунт не подключён —{" "}
            <span className="text-amber-400">
              войдите в нём (лучше «Войти с паролем» или GitHub; вход через
              Google может отклоняться самим Google в системном Chromium)
            </span>
            , затем задайте вопрос.
          </span>
        ) : status.logged_in === null ? (
          <span className="text-ink-300">
            Окно браузера открыто (адрес в статусе). Если видите страницу входа
            Qwen — войдите в аккаунт, и статус станет «готов».
          </span>
        ) : (
          <span className="text-ink-300">
            Окно Qwen открыто и аккаунт подключён — можно задавать вопросы.
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          <Badge
            tone={
              !status?.open
                ? "amber"
                : status.logged_in === true
                  ? "green"
                  : status.logged_in === false
                    ? "amber"
                    : "neutral"
            }
          >
            {!status?.open
              ? "закрыт"
              : status.logged_in === true
                ? "готов"
                : status.logged_in === false
                  ? "нет входа"
                  : "открыт"}
          </Badge>
          <Button size="sm" variant="ghost" onClick={refreshStatus} title="Обновить статус">
            <RefreshCw size={14} />
          </Button>
        </span>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Выбор проекта */}
        <div>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <FolderGit2 size={16} /> Проект
          </h2>
          {projectsErr ? (
            <ErrorNote>{projectsErr}</ErrorNote>
          ) : (
            <div className="flex max-h-[70vh] flex-col gap-1 overflow-y-auto">
              {projects.length === 0 && (
                <p className="text-sm text-ink-400">Доступных проектов нет.</p>
              )}
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setProjectId(p.id === projectId ? null : p.id)}
                  className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                    p.id === projectId
                      ? "border-dragon-900 bg-dragon-900/30 text-white"
                      : "border-transparent text-ink-300 hover:bg-ink-800 hover:text-white"
                  }`}
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Диалог с ИИ */}
        <div>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <MessageSquare size={16} /> Разговор с ИИ
          </h2>

          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") ask();
            }}
            rows={4}
            disabled={busy}
            placeholder={
              projectId
                ? "Спросите ИИ о коде выбранного проекта… (Ctrl+Enter — отправить)"
                : "Выберите проект слева, затем спросите ИИ…"
            }
            className="w-full resize-y rounded-lg border border-ink-600 bg-ink-900 p-3 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none disabled:opacity-60"
          />

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button icon={<Bot size={16} />} loading={busy} disabled={!prompt.trim()} onClick={ask}>
              {busy ? "ИИ отвечает…" : "Спросить ИИ"}
            </Button>
            {projectId && (
              <Badge tone="blue">контекст: файлы проекта #{projectId}</Badge>
            )}
            {!projectId && (
              <span className="text-xs text-ink-400">Без проекта — общий вопрос</span>
            )}
          </div>

          {/* Быстрые подсказки */}
          {!busy && !answer && (
            <div className="mt-4 flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p}
                  onClick={() => setPrompt(p)}
                  className="rounded-full border border-ink-700 px-3 py-1.5 text-xs text-ink-300 transition-colors hover:border-dragon-800 hover:text-white"
                >
                  {p}
                </button>
              ))}
            </div>
          )}

          <ErrorNote>{askErr}</ErrorNote>

          {busy && (
            <div className="mt-4 flex items-center gap-2 text-sm text-ink-300">
              <Spinner label="ИИ думает над ответом — смотрите окно браузера на сервере…" />
            </div>
          )}

          {/* Ответ ИИ */}
          {answer && !busy && (
            <Card className="mt-4 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs text-ink-400">
                <Bot size={14} /> Ответ ИИ
                {answer.project && <Badge tone="blue">{answer.project}</Badge>}
              </div>
              <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-200">
                {answer.answer}
              </div>

              {/* Правки, которые ИИ предлагает применить к файлам проекта */}
              {answer.edits.length > 0 && (
                <div className="mt-4 border-t border-ink-700 pt-3">
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-amber-400">
                    <Wrench size={13} /> Предложенные правки — запишутся в файлы проекта на
                    сервере
                  </div>
                  <div className="flex flex-col gap-2">
                    {answer.edits.map((edit) => (
                      <div
                        key={edit.path}
                        className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2"
                      >
                        <FileText size={14} className="shrink-0 text-ink-400" />
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-white">
                          {edit.path}
                        </span>
                        <span className="text-xs text-ink-400">{edit.content.length} симв.</span>
                        <details className="text-ink-400">
                          <summary className="cursor-pointer select-none text-xs hover:text-white">
                            смотреть
                          </summary>
                          <pre className="mt-2 max-h-60 overflow-auto rounded bg-ink-950 p-2 text-xs text-ink-300">
                            {edit.content}
                          </pre>
                        </details>
                        <Button
                          size="sm"
                          variant="outline"
                          loading={applying === edit.path}
                          disabled={applying !== null}
                          onClick={() => applyEdit(edit)}
                        >
                          Применить
                        </Button>
                      </div>
                    ))}
                  </div>
                  {applyMsg && (
                    <p
                      className={`mt-2 text-xs ${applyMsg.ok ? "text-emerald-400" : "text-dragon-400"}`}
                    >
                      {applyMsg.text}
                    </p>
                  )}
                </div>
              )}
            </Card>
          )}

          {/* История вопросов за сессию */}
          {log.length > 1 && (
            <div className="mt-6">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-400">
                История ({log.length})
              </h3>
              <div className="flex flex-col gap-3">
                {log.slice(1).map((item, i) => (
                  <Card key={i} className="p-3">
                    <div className="mb-1 text-xs text-ink-400">Вы: {item.prompt}</div>
                    <div className="whitespace-pre-wrap break-words text-sm text-ink-200">
                      {item.answer}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Файлы выбранного проекта */}
      {projectId && (
        <div className="mt-8">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <Wrench size={16} /> Файлы проекта
          </h2>
          <FilesBrowser projectId={projectId} />
        </div>
      )}

      {!projectId && projects.length > 0 && !projectsErr && (
        <EmptyState
          icon={<FolderGit2 size={26} />}
          title="Выберите проект"
          description="Нажмите на проект слева — откроются его файлы, а вопросы ИИ будут с кодом проекта."
        />
      )}
    </div>
  );
}
