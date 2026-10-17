// Тонкий клиент JSON-API Team Server. Все запросы — same-origin, куку сессии
// («nick») браузер отправляет сам.

export type Role = "host" | "pi";

export interface User {
  id: number;
  username: string;
  role: Role;
  created_at?: string;
  project_count?: number;
}

export interface Project {
  id: number;
  name: string;
  owner: string;
  owner_id: number;
  added_at: string;
  mine: boolean;
  access: boolean;
  analyzed: boolean;
  has_png: boolean;
  has_digest: boolean;
  has_diagram: boolean;
}

export interface Action {
  ts: string;
  user: string;
  action: string;
  details: string;
  project: string;
}

export interface Stats {
  projects: number;
  my_projects: number;
  users: number;
  actions: number;
  actions_today: number;
  role: Role;
}

export interface Analysis {
  summary: string;
  tree: string;
  diagram_code: string | null;
  diagram_kind: "code" | "tree" | null;
  diagram_note: string;
  png_name: string | null;
}

export interface Diagram {
  code: string;
  kind: "code" | "tree";
  note: string;
}

export interface IngestResult {
  url: string;
  summary: string;
  tree: string;
  digest: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    ...options,
  });
  if (!resp.ok) {
    let detail = `Ошибка ${resp.status}`;
    try {
      const data = await resp.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* тело не JSON — оставляем общий текст */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  const type = resp.headers.get("content-type") || "";
  if (type.includes("application/json")) return (await resp.json()) as T;
  return (await resp.text()) as unknown as T;
}

function jsonBody(data: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

export const api = {
  // --- сессия ---
  me: () => request<{ user: User }>("/api/auth/me"),
  login: (nick: string) => request<{ user: User }>("/api/auth/login", jsonBody({ nick })),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  setRole: (role: Role) => request<{ user: User }>("/api/auth/role", jsonBody({ role })),

  // --- справочники ---
  users: () => request<{ users: User[] }>("/api/users"),
  stats: () => request<Stats>("/api/stats"),
  journal: (limit = 200) => request<{ rows: Action[] }>(`/api/journal?limit=${limit}`),

  // --- проекты ---
  projects: () => request<{ projects: Project[] }>("/api/projects"),
  project: (id: number) =>
    request<{ project: Project; analysis: Analysis | null }>(`/api/projects/${id}`),
  uploadProject: (form: FormData) =>
    request<{ project: Project }>("/api/projects", { method: "POST", body: form }),
  analyze: (id: number) =>
    request<Analysis>(`/api/projects/${id}/analyze`, { method: "POST" }),
  diagram: (id: number) => request<Diagram>(`/api/projects/${id}/diagram`),
  digest: (id: number) => request<string>(`/api/projects/${id}/digest`),
  removeProject: (id: number) =>
    request<{ ok: boolean }>(`/api/projects/${id}`, { method: "DELETE" }),

  // --- произвольный репозиторий (gitingest) ---
  ingest: (url: string, maxSize = 100, token = "") =>
    request<IngestResult>("/api/ingest", jsonBody({ url, max_size: maxSize, token })),
};

export const pngUrl = (id: number) => `/api/projects/${id}/png`;
export const digestUrl = (id: number) => `/api/projects/${id}/digest`;
export const exportUrl = (id: number) => `/api/projects/${id}/export`;
