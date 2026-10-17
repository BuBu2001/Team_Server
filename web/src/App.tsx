import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { DragonIcon } from "./components/DragonIcon";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Projects } from "./pages/Projects";
import { ProjectDetail } from "./pages/ProjectDetail";
import { Analyze } from "./pages/Analyze";
import { Journal } from "./pages/Journal";
import { Team } from "./pages/Team";
import { Cryless } from "./pages/Cryless";
import { Settings } from "./pages/Settings";

function Splash() {
  return (
    <div className="bg-dragon-glow flex min-h-screen flex-col items-center justify-center gap-4">
      <DragonIcon size={56} className="animate-pulse text-dragon-600" />
      <span className="text-sm text-ink-300">Загружаем Team Server…</span>
    </div>
  );
}

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <DragonIcon size={48} className="text-dragon-700" />
      <h1 className="text-2xl font-bold text-white">Страница не найдена</h1>
      <p className="text-sm text-ink-300">Такого раздела на сайте нет.</p>
      <a href="/" className="text-sm text-dragon-500 hover:text-dragon-400">
        ← на главную
      </a>
    </div>
  );
}

function AppRoutes() {
  const { user, loading } = useAuth();

  if (loading) return <Splash />;

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        <Route path="/analyze" element={<Analyze />} />
        <Route path="/journal" element={<Journal />} />
        <Route path="/team" element={<Team />} />
        <Route path="/cryless" element={<Cryless />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
