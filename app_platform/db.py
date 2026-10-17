"""База данных платформы (SQLite): пользователи, проекты и журнал работы.

Это ЕДИНАЯ БД всей системы: её пишет и веб-панель (app_platform), и консоль
Host ⇄ Pi (getting_started/core.py через ``log_action``), поэтому журнал
работы и проекты видны в обоих интерфейсах.

Путь можно переопределить переменной окружения ``TEAM_SERVER_DB`` — это нужно
тестам (и e2e-сценарию), чтобы не писать в рабочую базу data/team_server.db.
"""
import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get("TEAM_SERVER_DB")
    or Path(__file__).resolve().parent.parent / "data" / "team_server.db"
)


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'pi',      -- 'host' или 'pi'
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                source TEXT NOT NULL,                  -- путь к папке проекта на сервере
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                user TEXT NOT NULL,                    -- 'host' / имя пользователя Pi
                action TEXT NOT NULL,                  -- upload/download/analyze/...
                details TEXT NOT NULL DEFAULT '',
                project TEXT NOT NULL DEFAULT ''
            );
            -- Токены репозиториев cryless (CLI-библиотеки команд).
            -- Хранится только SHA-256 от токена: сам токен выдаётся один раз
            -- при `cryless create repository` и лежит в .cryless/config.json.
            CREATE TABLE IF NOT EXISTS cryless_tokens (
                token_hash TEXT PRIMARY KEY,
                repo_id INTEGER NOT NULL,              -- id из projects
                username TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )


# ---------- Пользователи ----------

def create_user(username: str, role: str = "pi") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, role) VALUES (?, ?)", (username, role)
        )
        uid = cur.lastrowid
    return {"id": uid, "username": username, "role": role}


def get_user_by_name(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.*, COUNT(p.id) AS project_count "
            "FROM users u LEFT JOIN projects p ON p.owner_id = u.id "
            "GROUP BY u.id ORDER BY u.id"
        ).fetchall()
    return [dict(r) for r in rows]


def set_role(user_id: int, role: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


# ---------- Проекты ----------

def add_project(owner_id: int, name: str, source: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (owner_id, name, source) VALUES (?, ?, ?)",
            (owner_id, name, source),
        )
        pid = cur.lastrowid
    return {"id": pid, "owner_id": owner_id, "name": name, "source": source}


def list_projects(owner_id: int | None = None) -> list[dict]:
    q = (
        "SELECT p.*, u.username AS owner FROM projects p "
        "JOIN users u ON u.id = p.owner_id "
    )
    args: tuple = ()
    if owner_id is not None:
        q += "WHERE p.owner_id = ? "
        args = (owner_id,)
    q += "ORDER BY p.id DESC"
    with get_conn() as conn:
        rows = conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.*, u.username AS owner FROM projects p "
            "JOIN users u ON u.id = p.owner_id WHERE p.id = ?",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_project(project_id: int) -> None:
    """Удаляет проект и связанные с ним токены cryless (данные на диске — вызывающий)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM cryless_tokens WHERE repo_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------- Журнал работы (получает данные из консоли GettingStarted) ----------
def register_project(username: str, name: str, source: str) -> dict:
    """Добавляет проект; пользователь создаётся автоматически, если новый."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO users (username, role) VALUES (?, 'pi')", (username,)
            )
            uid = cur.lastrowid
        else:
            uid = row["id"]
        existing = conn.execute(
            "SELECT * FROM projects WHERE owner_id = ? AND name = ?", (uid, name)
        ).fetchone()
        if existing:
            conn.execute("UPDATE projects SET source = ? WHERE id = ?",
                         (source, existing["id"]))
            return dict(existing) | {"source": source}
        cur = conn.execute(
            "INSERT INTO projects (owner_id, name, source) VALUES (?, ?, ?)",
            (uid, name, source),
        )
        return {"id": cur.lastrowid, "owner_id": uid, "name": name, "source": source}


def log_action(user: str, action: str, details: str = "", project: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO actions (user, action, details, project) VALUES (?, ?, ?, ?)",
            (user, action, details, project),
        )


def list_actions(user: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT ts, user, action, details, project FROM actions "
    args: tuple = ()
    if user is not None:
        q += "WHERE user = ? "
        args = (user,)
    q += "ORDER BY id DESC LIMIT ?"
    args += (limit,)
    with get_conn() as conn:
        rows = conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def count_actions(user: str | None = None, today: bool = False) -> int:
    """Число действий (для статистики): всего или только за сегодня."""
    q = "SELECT COUNT(*) AS c FROM actions"
    clauses: list[str] = []
    args: tuple = ()
    if user is not None:
        clauses.append("user = ?")
        args += (user,)
    if today:
        clauses.append("date(ts) = date('now')")
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    with get_conn() as conn:
        row = conn.execute(q, args).fetchone()
    return int(row["c"])


# ---------- Репозитории cryless (CLI-библиотека команд) ----------

def find_project(owner_id: int, name: str) -> dict | None:
    """Проект конкретного владельца по имени (у разных Pi могут совпадать)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE owner_id = ? AND name = ?",
            (owner_id, name),
        ).fetchone()
    return dict(row) if row else None


def issue_repo_token(repo_id: int, username: str) -> str:
    """Выдаёт новый токен доступа к репозиторию (старые аннулируются).

    В базе лежит только SHA-256: утечка БД не даёт доступа к репозиториям.
    """
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_conn() as conn:
        conn.execute("DELETE FROM cryless_tokens WHERE repo_id = ?", (repo_id,))
        conn.execute(
            "INSERT INTO cryless_tokens (token_hash, repo_id, username) VALUES (?, ?, ?)",
            (digest, repo_id, username),
        )
    return token


def resolve_repo_token(token: str) -> dict | None:
    """Токен -> {repo_id, username} или None, если токен неизвестен."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT repo_id, username FROM cryless_tokens WHERE token_hash = ?",
            (digest,),
        ).fetchone()
    return dict(row) if row else None

