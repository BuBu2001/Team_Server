import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  FolderGit2,
  Search,
  ScrollText,
  Users,
  Package,
  Settings,
  LogOut,
  Menu,
  X,
  ExternalLink,
  BookOpen,
  ChevronDown,
} from "lucide-react";
import { useAuth } from "../auth";
import { Badge } from "./ui";
import { DragonIcon } from "./DragonIcon";

const NAV = [
  { to: "/", label: "Обзор", icon: LayoutDashboard, end: true },
  { to: "/projects", label: "Проекты", icon: FolderGit2 },
  { to: "/analyze", label: "Анализ репозитория", icon: Search },
  { to: "/journal", label: "Журнал", icon: ScrollText },
  { to: "/team", label: "Команда", icon: Users },
  { to: "/cryless", label: "Cryless", icon: Package },
  { to: "/settings", label: "Настройки", icon: Settings },
];

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? "bg-dragon-900/30 text-white border border-dragon-900"
                : "text-ink-300 hover:bg-ink-800 hover:text-white border border-transparent"
            }`
          }
        >
          <Icon size={18} />
          {label}
        </NavLink>
      ))}

      <div className="my-2 border-t border-ink-700" />
      <a
        href="/site"
        className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-300 hover:bg-ink-800 hover:text-white"
      >
        <BookOpen size={18} />
        Сайт gitingest
        <ExternalLink size={14} className="ml-auto opacity-60" />
      </a>
      <a
        href="/site/docs"
        className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-300 hover:bg-ink-800 hover:text-white"
      >
        <ExternalLink size={18} />
        API-документация
        <ExternalLink size={14} className="ml-auto opacity-60" />
      </a>
    </nav>
  );
}

export function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState(false);

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="min-h-screen bg-ink-950">
      {/* Верхняя панель */}
      <header className="sticky top-0 z-30 border-b border-ink-800 bg-ink-900/90 backdrop-blur">
        <div className="flex h-14 items-center gap-3 px-4">
          <button
            className="rounded-lg p-2 text-ink-300 hover:bg-ink-800 lg:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label="Меню"
          >
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>

          <NavLink to="/" className="flex items-center gap-2">
            <DragonIcon size={26} className="text-dragon-600" />
            <span className="text-base font-bold tracking-tight text-white">
              Team<span className="text-dragon-500">Server</span>
            </span>
          </NavLink>

          <div className="ml-auto flex items-center gap-2">
            <div className="relative">
              <button
                onClick={() => setMenu((v) => !v)}
                className="flex items-center gap-2 rounded-lg border border-ink-700 bg-ink-800 px-2.5 py-1.5 text-sm hover:border-dragon-700"
              >
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-dragon-900/50 text-xs font-bold text-dragon-400">
                  {user?.username?.[0]?.toUpperCase() ?? "?"}
                </span>
                <span className="hidden max-w-[10rem] truncate text-ink-200 sm:inline">
                  {user?.username}
                </span>
                <Badge tone={user?.role === "host" ? "red" : "blue"}>
                  {user?.role === "host" ? "Host" : "Pi"}
                </Badge>
                <ChevronDown size={14} className="text-ink-400" />
              </button>

              {menu && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenu(false)} />
                  <div className="absolute right-0 z-20 mt-2 w-48 overflow-hidden rounded-lg border border-ink-700 bg-ink-850 py-1 shadow-xl">
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-sm text-ink-200 hover:bg-ink-800"
                      onClick={() => {
                        setMenu(false);
                        navigate("/settings");
                      }}
                    >
                      <Settings size={15} /> Настройки
                    </button>
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-sm text-dragon-400 hover:bg-ink-800"
                      onClick={handleLogout}
                    >
                      <LogOut size={15} /> Выйти
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1400px]">
        {/* Боковое меню (desktop) */}
        <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] w-64 shrink-0 flex-col gap-4 overflow-y-auto border-r border-ink-800 p-4 lg:flex">
          <NavItems />
        </aside>

        {/* Боковое меню (mobile) */}
        {open && (
          <div className="fixed inset-x-0 top-14 bottom-0 z-20 overflow-y-auto border-b border-ink-800 bg-ink-900 p-4 lg:hidden">
            <NavItems onNavigate={() => setOpen(false)} />
          </div>
        )}

        <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
