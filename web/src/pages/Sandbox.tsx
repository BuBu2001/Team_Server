import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Bot,
  FileText,
  FolderGit2,
  List,
  MessageSquarePlus,
  RefreshCw,
  Rocket,
  Save,
  Search,
  Send,
  Square,
  Wrench,
} from "lucide-react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import {
  api,
  ApiError,
  type SandboxAgentStep,
  type SandboxFileEdit,
  type SandboxStatus,
} from "../api";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
} from "../components/ui";
import { FilesBrowser } from "../components/FilesBrowser";

/** Промпт-подсказки для пустого чата. */
const PRESETS = [
  "Проанализируй все файлы проекта и объясни, что делает каждая часть.",
  "Найди в проекте ошибки и точки роста. Отвечай кратко и по делу.",
  "Как запустить этот проект локально? Что нужно установить?",
];

/** Иконки инструментов режима агента (шаги под ответом). */
const TOOL_ICONS: Record<string, ReactNode> = {
  список: <List size={13} />,
  чтение: <FileText size={13} />,
  поиск: <Search size={13} />,
  запись: <Save size={13} />,
  создать: <FolderGit2 size={13} />,
  конец: <Square size={13} />,
};

/** Одно сообщение чата. История живёт в браузере и переживает F5. */
interface ChatMessage {
  id: number;
  role: "user" | "ai";
  text: string;
  /** Проект, прикреплённый к моменту отправки (имя — в подписи сообщения). */
  project?: string;
  /** Его id — правки применяются к тому же проекту, что и вопрос. */
  pid?: number;
  /** Правки из ответа ИИ (живут только в сессии — в localStorage не пишем). */
  edits?: SandboxFileEdit[];
  applied?: string[];
  applyErr?: string;
  /** Шаги режима агента: там правки уже применены, остаётся только журнал. */
  agentLog?: SandboxAgentStep[];
  /** Ответ на запрос в режиме агента (для подписи «Агент работает…»). */
  agent?: boolean;
  error?: boolean;
  pending?: boolean;
}

const CHAT_KEY = "sandbox-chat-v1";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** Читает сохранённую историю чата (без правок и незавершённых ответов). */
function loadChat(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(CHAT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatMessage[];
    return Array.isArray(parsed) ? parsed.filter((m) => !m.pending) : [];
  } catch {
    return [];
  }
}

/**
 * «Песочница»: чат с ИИ (chat qwen.ai), прикреплённый к выбранному проекту.
 *
 * ИИ работает через настоящий браузер на сервере (app_platform/qwen.py):
 * окно chat.qwen.ai открывается по кнопке в шапке, туда один раз входят в
 * аккаунт. Каждое сообщение уходит с контекстом файлов выбранного проекта,
 * ответ появляется здесь, в чате. Правки из ответа (блоки «ФАЙЛ: …»)
 * применяются кнопкой — файлы проекта внизу обновляются сразу. Без проекта
 * можно попросить создать репозиторий: блок «ИНСТРУМЕНТ: создать»
 * выполняется на сервере, новый проект появляется в списке и подключается.
 */
export function Sandbox() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [statusErr, setStatusErr] = useState("");
  const [opening, setOpening] = useState(false);

  const [projects, setProjects] = useState<{ id: number; name: string; access: boolean }[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [projectsErr, setProjectsErr] = useState("");

  const [messages, setMessages] = useState<ChatMessage[]>(loadChat);
  const [prompt, setPrompt] = useState("");
  const [asking, setAsking] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  // режим агента: ИИ сам читает и правит файлы проекта (без «Применить»)
  const [agentMode, setAgentMode] = useState(false);
  // пока переключаем родный режим на сайте Qwen (клик по меню режимов)
  const [modeSwitching, setModeSwitching] = useState(false);

  // правка ИИ → файлы проекта; refreshKey заставляет FilesBrowser перечитать
  const [applying, setApplying] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const idRef = useRef(Date.now());
  const chatBoxRef = useRef<HTMLDivElement | null>(null);
  const filesRef = useRef<HTMLDivElement | null>(null);

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

  // ---- список проектов (повторно вызывается, когда ИИ создаёт репозиторий) ----
  const loadProjects = useCallback(
    () =>
      api
        .projects()
        .then((res) =>
          setProjects(
            res.projects
              .filter((p) => p.access)
              .map((p) => ({ id: p.id, name: p.name, access: p.access })),
          ),
        )
        .catch((e: unknown) => setProjectsErr(errText(e))),
    [],
  );

  useEffect(() => {
    refreshStatus();
    loadProjects();
  }, [refreshStatus, loadProjects]);

  // пока браузер открыт — раз в 5 с подтягиваем статус (вошли ли в аккаунт)
  useEffect(() => {
    if (!status?.open) return;
    const t = window.setInterval(refreshStatus, 5000);
    return () => window.clearInterval(t);
  }, [status?.open, refreshStatus]);

  // сохраняем историю чата (текст и подписи — правки слишком тяжёлые)
  useEffect(() => {
    try {
      const stored = messages
        .filter((m) => !m.pending)
        .map(({ id, role, text, project, pid, error }) => ({
          id,
          role,
          text,
          project,
          pid,
          error,
        }));
      localStorage.setItem(CHAT_KEY, JSON.stringify(stored));
    } catch {
      /* хранилище переполнено — история просто не сохранится */
    }
  }, [messages]);

  // таймер «ИИ отвечает… N с»
  useEffect(() => {
    if (!asking) return;
    const t = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [asking]);

  // держим чат прокрученным к последнему сообщению
  useEffect(() => {
    const el = chatBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, elapsed]);

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

  // ---- новый диалог: сбрасывает страницу Qwen и локальную историю ----
  const newChat = () => {
    if (asking) return;
    if (messages.length > 0 && !window.confirm("Начать новый диалог? История чата очистится."))
      return;
    setMessages([]);
    setStatusErr("");
    api
      .sandboxReset()
      .then((s) => setStatus(s))
      .catch((e: unknown) => setStatusErr(errText(e)));
  };

  // ---- тумблер «Режим агента»: включает наш протокол И режим на сайте Qwen ----
  const toggleAgent = () => {
    if (asking || modeSwitching) return;
    const next = !agentMode;
    setModeSwitching(true);
    setStatusErr("");
    api
      .sandboxMode(next)
      .then((res) => {
        setAgentMode(res.agent);
        api.sandboxStatus().then(setStatus).catch(() => undefined);
      })
      // не включилось — показываем причину (нет окна/входа, пункт недоступен)
      .catch((e: unknown) => setStatusErr(errText(e)))
      .finally(() => setModeSwitching(false));
  };

  // ---- отправка сообщения ----
  const ask = (override?: string) => {
    const text = (override ?? prompt).trim();
    // агент правит выбранный проект, а без него — создаёт репозиторий по просьбе
    const useAgent = agentMode;
    if (!text || asking) return;
    const proj = projects.find((p) => p.id === projectId);
    const userId = (idRef.current += 1);
    const aiId = (idRef.current += 1);
    setMessages((prev) => [
      ...prev,
      { id: userId, role: "user", text, project: proj?.name, pid: projectId ?? undefined },
      {
        id: aiId,
        role: "ai",
        text: "",
        project: proj?.name,
        pid: projectId ?? undefined,
        agent: useAgent,
        pending: true,
      },
    ]);
    setPrompt("");
    setAsking(true);
    setElapsed(0);
    api
      .sandboxAsk(text, projectId, useAgent)
      .then((res) => {
        // агент уже записал файлы (и создал репозиторий) — список перечитываем сразу
        const wrote = !!res.agent_log?.some(
          (s) =>
            (s.tool === "запись" || s.tool === "создать") && !s.note.startsWith("ошибка"),
        );
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiId
              ? {
                  ...m,
                  pending: false,
                  text: res.answer,
                  edits: res.edits,
                  agentLog: res.agent_log,
                  project: res.project || res.created?.name || m.project,
                }
              : m,
          ),
        );
        // новый репозиторий от ИИ: перечитываем список и сразу подключаем его к чату
        const created = res.created;
        if (created) {
          void loadProjects().then(() => {
            setProjectId(created.id);
            setRefreshKey((n) => n + 1);
          });
        }
        if (wrote) {
          setRefreshKey((n) => n + 1);
          filesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      })
      .catch((e: unknown) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiId ? { ...m, pending: false, error: true, text: errText(e) } : m,
          ),
        );
      })
      .finally(() => setAsking(false));
  };

  // ---- применить правку из ответа к файлу проекта ----
  const applyEdit = (msg: ChatMessage, edit: SandboxFileEdit) => {
    if (!msg.pid || applying) return;
    const key = `${msg.id}:${edit.path}`;
    setApplying(key);
    setMessages((prev) => prev.map((m) => (m.id === msg.id ? { ...m, applyErr: undefined } : m)));
    api
      .sandboxApply(msg.pid, edit.path, edit.content)
      .then((res) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msg.id
              ? {
                  ...m,
                  applied: [...(m.applied ?? []).filter((p) => p !== res.path), res.path],
                }
              : m,
          ),
        );
        // файлы проекта перечитываются — правка сразу видна в списке
        setRefreshKey((n) => n + 1);
        filesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch((e: unknown) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === msg.id ? { ...m, applyErr: errText(e) } : m)),
        ),
      )
      .finally(() => setApplying(null));
  };

  // ---- подсказка про браузер/вход (над сообщениями) ----
  const notice: { text: string; amber: boolean; action: ReactNode } | null = !status?.open
    ? {
        text: "Браузер ИИ закрыт — откройте окно на сервере и войдите в аккаунт Qwen (один раз).",
        amber: true,
        action: (
          <Button size="sm" variant="outline" loading={opening} onClick={openBrowser}>
            Открыть браузер
          </Button>
        ),
      }
    : status.logged_in === false
      ? {
          text: "Аккаунт Qwen не подключён — нажмите «Войти» в окне браузера на сервере («Войти с паролем» или GitHub).",
          amber: true,
          action: (
            <Button size="sm" variant="outline" onClick={refreshStatus}>
              Проверить снова
            </Button>
          ),
        }
      : status.logged_in === null
        ? {
            text: "Окно браузера открыто — если там страница входа Qwen, войдите в аккаунт.",
            amber: false,
            action: null,
          }
        : null;

  const attachedName =
    projectId != null
      ? (projects.find((p) => p.id === projectId)?.name ?? `проект #${projectId}`)
      : null;

  const renderMsg = (m: ChatMessage) =>
    m.role === "user" ? (
      <div key={m.id} className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-dragon-800 bg-dragon-900/40 px-4 py-2.5">
          <div className="mb-1 text-[11px] text-dragon-300">
            Вы{m.project ? ` · ${m.project}` : ""}
          </div>
          <div className="whitespace-pre-wrap break-words text-sm text-white">{m.text}</div>
        </div>
      </div>
    ) : (
      <div key={m.id} className="flex justify-start">
        <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-ink-700 bg-ink-900 px-4 py-2.5">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] text-ink-400">
            <Bot size={12} /> ИИ
            {m.project && <Badge tone="blue">{m.project}</Badge>}
          </div>

          {m.pending ? (
            <div className="text-sm text-ink-300">
              <Spinner
                label={m.agent ? `Агент выполняет задачу… ${elapsed} с` : `ИИ отвечает… ${elapsed} с`}
              />
            </div>
          ) : (
            <>
              <div
                className={`chat-md max-w-none text-sm ${
                  m.error ? "text-dragon-400" : "text-ink-200"
                }`}
                dangerouslySetInnerHTML={{
                  __html: DOMPurify.sanitize(marked.parse(m.text || "") as string),
                }}
              />

              {!!m.edits?.length && (
                <div className="mt-3 border-t border-ink-700 pt-2.5">
                  <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-amber-400">
                    <Wrench size={13} /> Правки — запишутся в файлы проекта
                  </div>
                  <div className="flex flex-col gap-2">
                    {m.edits.map((edit, i) => {
                      const done = !!m.applied?.includes(edit.path);
                      return (
                        <div
                          key={`${m.id}-${i}-${edit.path}`}
                          className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2"
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
                          {done ? (
                            <span className="text-xs font-medium text-emerald-400">
                              ✓ применён
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              loading={applying === `${m.id}:${edit.path}`}
                              disabled={applying !== null}
                              onClick={() => applyEdit(m, edit)}
                            >
                              Применить
                            </Button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {m.applyErr && <p className="mt-2 text-xs text-dragon-400">{m.applyErr}</p>}
                </div>
              )}

              {!!m.agentLog?.length && (
                <div className="mt-3 border-t border-ink-700 pt-2.5">
                  <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-emerald-400">
                    <Wrench size={13} /> Действия агента — «запись» и «создать» применяются сразу
                  </div>
                  <div className="flex flex-col gap-1">
                    {m.agentLog.map((step, i) => {
                      const err = step.note.startsWith("ошибка");
                      const wrote =
                        (step.tool === "запись" || step.tool === "создать") && !err;
                      const tone = err
                        ? "text-dragon-400"
                        : wrote
                          ? "text-emerald-400"
                          : "text-ink-400";
                      return (
                        <div
                          key={`${m.id}-step-${i}`}
                          className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-1.5 text-xs"
                        >
                          <span className={`shrink-0 ${tone}`}>
                            {TOOL_ICONS[step.tool] ?? <Wrench size={13} />}
                          </span>
                          <span className="shrink-0 font-medium text-white">{step.tool}</span>
                          {step.path && (
                            <span className="min-w-0 flex-1 truncate font-mono text-ink-300">
                              {step.path}
                            </span>
                          )}
                          <span className={tone}>{step.note}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    );

  return (
    <div className="animate-fade-up">
      <PageHeader
        title="Песочница"
        subtitle="chat qwen.ai: прикрепляется к выбранному проекту — видит его файлы и правит их; без проекта может создать репозиторий по вашей просьбе."
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

        {/* chat qwen.ai — на месте прежней плашки «Браузер ИИ открыт» */}
        <div className="card flex flex-col overflow-hidden">
          {/* шапка чата */}
          <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 bg-ink-800/60 px-4 py-3">
            <Bot size={18} className="text-dragon-500" />
            <span className="text-sm font-semibold text-white">chat qwen.ai</span>
            <Badge tone={attachedName ? "blue" : "neutral"}>
              {attachedName ? `прикреплён: ${attachedName}` : "без проекта"}
            </Badge>
            <span className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={toggleAgent}
                disabled={asking || modeSwitching}
                title="Включает режим агента: наш протокол инструментов + родный Agent Mode на сайте Qwen (окно браузера на сервере)"
                className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-60 ${
                  agentMode
                    ? "border-dragon-700 bg-dragon-900/60 text-white"
                    : "border-ink-600 text-ink-300 hover:border-ink-500 hover:text-white"
                }`}
              >
                <Wrench size={13} className={agentMode ? "text-emerald-400" : ""} />
                Режим агента
                <span
                  className={`ml-0.5 h-2 w-2 rounded-full ${
                    modeSwitching
                      ? "animate-pulse bg-amber-400"
                      : agentMode
                        ? "bg-emerald-400"
                        : "bg-ink-500"
                  }`}
                />
              </button>
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
              <Button
                size="sm"
                variant="outline"
                icon={<MessageSquarePlus size={14} />}
                onClick={newChat}
                disabled={asking}
              >
                Новый диалог
              </Button>
            </span>
          </div>

          {/* браузер закрыт / нет входа — короткая подсказка */}
          {notice && (
            <div
              className={`flex flex-wrap items-center gap-2 border-b border-ink-700 px-4 py-2 text-xs ${
                notice.amber ? "bg-amber-500/10 text-amber-300" : "bg-ink-800/60 text-ink-300"
              }`}
            >
              <span>{notice.text}</span>
              {notice.action && <span className="ml-auto">{notice.action}</span>}
            </div>
          )}

          {/* сообщения */}
          <div
            ref={chatBoxRef}
            className="flex h-[55vh] min-h-[300px] flex-col gap-3 overflow-y-auto p-4"
          >
            {messages.length === 0 && (
              <div className="m-auto max-w-lg text-center">
                <Bot size={30} className="mx-auto mb-2 text-dragon-500" />
                <p className="mb-1 text-sm text-ink-200">
                  {attachedName
                    ? `Чат прикреплён к проекту «${attachedName}» — его файлы уходят ИИ с каждым сообщением.`
                    : "Выберите проект слева — ИИ получит его файлы; либо просто попросите «создай репозиторий» — ИИ создаст новый проект."}
                </p>
                <p className="text-xs text-ink-400">
                  ИИ может править файлы: кнопка «Применить» под ответом записывает изменения,
                  и они сразу видны в списке файлов ниже. Либо включите «Режим агента» в шапке —
                  тогда ИИ сам читает файлы, правит их и создаёт репозитории без подтверждения.
                </p>
                <div className="mt-4 flex flex-col gap-2">
                  {PRESETS.map((p) => (
                    <button
                      key={p}
                      onClick={() => setPrompt(p)}
                      className="rounded-lg border border-ink-700 px-3 py-2 text-left text-xs text-ink-300 transition-colors hover:border-dragon-800 hover:text-white"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map(renderMsg)}
          </div>

          {/* ввод */}
          <div className="border-t border-ink-700 p-4">
            <div className="flex items-end gap-2">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    ask();
                  }
                }}
                rows={2}
                disabled={asking}
                placeholder={
                  attachedName
                    ? "Напишите сообщение… Enter — отправить, Shift+Enter — новая строка"
                    : "Сообщение ИИ (лучше выберите проект слева)…"
                }
                className="flex-1 resize-none rounded-lg border border-ink-600 bg-ink-900 p-3 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none disabled:opacity-60"
              />
              <Button
                icon={<Send size={16} />}
                loading={asking}
                disabled={!prompt.trim()}
                onClick={() => ask()}
              >
                {asking ? (agentMode ? "Агент работает…" : "ИИ отвечает…") : "Отправить"}
              </Button>
            </div>
            {agentMode && projectId == null && (
              <p className="mt-2 text-xs text-ink-400">
                Режим агента без проекта: попросите ИИ «создай репозиторий» — он создаст новый
                проект, он появится слева и подключится к чату.
              </p>
            )}
            {asking && (
              <p className="mt-2 text-xs text-ink-400">
                {agentMode
                  ? "Агент выполняет задачу инструментами сервера (читает, пишет, создаёт репозитории) — ответ с журналом шагов придёт сюда, подождите."
                  : "ИИ печатает ответ в окне браузера на сервере — ответ придёт сюда, подождите."}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Файлы выбранного проекта — обновляются после применения правок */}
      {projectId && (
        <div ref={filesRef} className="mt-8 scroll-mt-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <Wrench size={16} /> Файлы проекта
          </h2>
          <FilesBrowser projectId={projectId} refreshToken={refreshKey} />
        </div>
      )}

      {!projectId && projects.length > 0 && !projectsErr && (
        <EmptyState
          icon={<FolderGit2 size={26} />}
          title="Выберите проект"
          description="Нажмите на проект слева — откроются его файлы, а сообщения ИИ будут с контекстом проекта."
        />
      )}
    </div>
  );
}
