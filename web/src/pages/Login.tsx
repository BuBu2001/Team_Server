import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { LogIn, ShieldCheck, GitBranch, Users } from "lucide-react";
import { useAuth } from "../auth";
import { ApiError } from "../api";
import { Button, ErrorNote } from "../components/ui";
import { DragonIcon } from "../components/DragonIcon";

const FEATURES = [
  { icon: GitBranch, text: "Анализ структуры и кода проектов" },
  { icon: Users, text: "Host и Pi в одной локальной сети" },
  { icon: ShieldCheck, text: "Журнал действий и роли пользователей" },
];

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [nick, setNick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!nick.trim()) {
      setError("Введите ник");
      return;
    }
    setBusy(true);
    try {
      await login(nick.trim());
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="bg-dragon-glow flex min-h-screen items-center justify-center p-4">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-2xl border border-ink-700 bg-ink-900 shadow-2xl md:grid-cols-2">
        {/* Левая часть — бренд */}
        <div className="relative hidden flex-col justify-between border-r border-ink-800 bg-ink-950 p-8 md:flex">
          <div className="flex items-center gap-3">
            <DragonIcon size={40} className="text-dragon-600" />
            <span className="text-xl font-bold text-white">
              Team<span className="text-dragon-500">Server</span>
            </span>
          </div>
          <div>
            <h1 className="text-2xl font-bold leading-tight text-white">
              Платформа для разработчиков
            </h1>
            <p className="mt-2 text-sm text-ink-300">
              Локальный сервер: Host выполняет работу, Pi отправляет проекты и
              получает результаты анализа.
            </p>
            <ul className="mt-6 flex flex-col gap-3">
              {FEATURES.map(({ icon: Icon, text }) => (
                <li key={text} className="flex items-center gap-3 text-sm text-ink-200">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-dragon-900 bg-dragon-900/20 text-dragon-500">
                    <Icon size={16} />
                  </span>
                  {text}
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-ink-400">Вход без пароля — только по нику.</p>
        </div>

        {/* Правая часть — форма */}
        <div className="flex flex-col justify-center p-8">
          <div className="mb-6 flex items-center gap-3 md:hidden">
            <DragonIcon size={34} className="text-dragon-600" />
            <span className="text-lg font-bold text-white">
              Team<span className="text-dragon-500">Server</span>
            </span>
          </div>
          <h2 className="text-xl font-bold text-white">Вход / регистрация</h2>
          <p className="mt-1 text-sm text-ink-300">
            Если ника ещё нет — аккаунт создастся автоматически.
          </p>

          <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-200">Ник</span>
              <input
                value={nick}
                onChange={(e) => setNick(e.target.value)}
                placeholder="vanya"
                autoFocus
                className="rounded-lg border border-ink-600 bg-ink-950 px-3 py-2.5 text-sm text-white placeholder:text-ink-400 focus:border-dragon-600 focus:outline-none"
              />
              <span className="text-xs text-ink-400">
                2–32 символа: латиница, цифры, <code>_ . -</code>
              </span>
            </label>

            <ErrorNote>{error}</ErrorNote>

            <Button type="submit" size="lg" loading={busy} icon={<LogIn size={18} />}>
              Войти
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
