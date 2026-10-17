import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Server, Laptop, LogOut, Check, ExternalLink, User as UserIcon } from "lucide-react";
import { useAuth } from "../auth";
import { Button, Card, ErrorNote, PageHeader, formatDate } from "../components/ui";
import type { Role } from "../api";

const ROLES: { id: Role; title: string; desc: string; icon: typeof Server }[] = [
  {
    id: "host",
    title: "Host",
    desc: "Вы сервер этой сети: доступны все проекты, журнал команды и чужие материалы.",
    icon: Server,
  },
  {
    id: "pi",
    title: "Pi",
    desc: "Обычный участник: отправляете свои проекты и видите свои результаты.",
    icon: Laptop,
  },
];

export function Settings() {
  const { user, setRole, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<Role | null>(null);
  const [error, setError] = useState("");

  async function changeRole(role: Role) {
    if (role === user?.role) return;
    setBusy(role);
    setError("");
    try {
      await setRole(role);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="animate-fade-up">
      <PageHeader title="Настройки" subtitle="Профиль, режим работы и выход." />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold text-white">
            <UserIcon size={18} className="text-dragon-500" /> Профиль
          </h2>
          <div className="flex items-center gap-4">
            <span className="flex h-14 w-14 items-center justify-center rounded-full border border-dragon-900 bg-dragon-900/20 text-xl font-bold text-dragon-500">
              {user?.username?.[0]?.toUpperCase()}
            </span>
            <div>
              <div className="text-lg font-semibold text-white">{user?.username}</div>
              <div className="text-sm text-ink-300">
                {user?.role === "host" ? "Host — сервер сети" : "Pi — участник"}
              </div>
              {user?.created_at && (
                <div className="mt-0.5 text-xs text-ink-400">
                  в системе с {formatDate(user.created_at)}
                </div>
              )}
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <a href="/site/docs" target="_blank" rel="noreferrer">
              <Button variant="outline" icon={<ExternalLink size={16} />}>
                API-документация
              </Button>
            </a>
            <Button
              variant="danger"
              icon={<LogOut size={16} />}
              onClick={async () => {
                await logout();
                navigate("/login");
              }}
            >
              Выйти
            </Button>
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="mb-4 text-lg font-semibold text-white">Режим работы</h2>
          <div className="flex flex-col gap-3">
            {ROLES.map(({ id, title, desc, icon: Icon }) => {
              const active = user?.role === id;
              return (
                <button
                  key={id}
                  onClick={() => changeRole(id)}
                  disabled={busy !== null}
                  className={`flex items-start gap-4 rounded-xl border p-4 text-left transition-colors disabled:opacity-60 ${
                    active
                      ? "border-dragon-600 bg-dragon-900/20"
                      : "border-ink-700 hover:border-ink-500"
                  }`}
                >
                  <span
                    className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
                      active ? "bg-dragon-600 text-white" : "bg-ink-800 text-ink-300"
                    }`}
                  >
                    <Icon size={18} />
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-white">{title}</span>
                      {active && (
                        <span className="flex items-center gap-1 text-xs text-dragon-400">
                          <Check size={13} /> текущий
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-ink-300">{desc}</p>
                  </div>
                </button>
              );
            })}
          </div>
          <ErrorNote>{error}</ErrorNote>
          <p className="mt-4 text-xs text-ink-400">
            Подсказка для администратора: клиенты cryless обращаются к{" "}
            <code>/api/cryless</code>; полный статус сервера — в разделе «Cryless».
          </p>
        </Card>
      </div>
    </div>
  );
}
