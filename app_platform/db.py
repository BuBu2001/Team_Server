"""База данных платформы (SQLite): пользователи и проекты."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "team_server.db"


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
