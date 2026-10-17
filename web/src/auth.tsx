import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, type Role, type User } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (nick: string) => Promise<User>;
  logout: () => Promise<void>;
  setRole: (role: Role) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const { user } = await api.me();
      setUser(user);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        // сетевые ошибки не считаем «не авторизован», просто оставляем как есть
      }
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (nick: string) => {
    const { user } = await api.login(nick);
    setUser(user);
    return user;
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  const setRole = useCallback(async (role: Role) => {
    const { user } = await api.setRole(role);
    setUser(user);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, setRole, refresh }),
    [user, loading, login, logout, setRole, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth должен вызываться внутри AuthProvider");
  return ctx;
}
