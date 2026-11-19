# database.py
import os
import random
import sqlite3
from datetime import datetime

DB_PATH = "monitoring.db"
PHOTOS_DIR = "photos"

sqlite3.register_converter("timestamp", lambda s: datetime.fromisoformat(s.decode()))


def now_seconds() -> datetime:
    return datetime.now().replace(microsecond=0)


def get_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL,
                last_seen TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                started TIMESTAMP NOT NULL,
                stopped TIMESTAMP,
                owner_id INTEGER NOT NULL,
                code TEXT NOT NULL UNIQUE,
                FOREIGN KEY (owner_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS session_access (
                session_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'participant',
                added_at TIMESTAMP NOT NULL,
                PRIMARY KEY (session_id, user_id),
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS join_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                number INTEGER,
                time TIMESTAMP NOT NULL,
                reason TEXT,
                text TEXT,
                file_path TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)


# ---------- Код доступа ----------

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_code() -> str:
    part1 = "".join(random.choices(CODE_ALPHABET, k=4))
    part2 = "".join(random.choices(CODE_ALPHABET, k=4))
    return f"{part1}-{part2}"


def generate_unique_code() -> str:
    with get_connection() as conn:
        while True:
            code = generate_code()
            row = conn.execute("SELECT 1 FROM sessions WHERE code = ?", (code,)).fetchone()
            if row is None:
                return code


# ---------- Пользователи ----------

def register_user(telegram_id: int, name: str, is_admin: bool = False) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row is not None:
            return dict(row)
        
        role = "admin" if is_admin else "user"
        status = "approved" if is_admin else "pending"
        now = now_seconds()
        
        conn.execute(
            "INSERT INTO users (telegram_id, name, role, status, created_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, name, role, status, now, now)
        )
        return {"telegram_id": telegram_id, "name": name, "role": role, "status": status}


def get_user(telegram_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return dict(row) if row else None


def is_admin(telegram_id: int) -> bool:
    user = get_user(telegram_id)
    return bool(user and user["role"] == "admin")


def update_last_seen(telegram_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_seen = ? WHERE telegram_id = ?",
            (now_seconds(), telegram_id)
        )


def get_all_users_sorted() -> list[dict]:
    """Все пользователи, отсортированные по последней активности (свежие сверху)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM users
            ORDER BY 
                CASE WHEN last_seen IS NULL THEN 1 ELSE 0 END, 
                last_seen DESC,
                created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def set_user_status(telegram_id: int, status: str) -> None:
    """Меняет статус пользователя ('pending', 'approved', 'blocked')."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET status = ? WHERE telegram_id = ?",
            (status, telegram_id)
        )


def get_admins() -> list[int]:
    """Возвращает список telegram_id всех администраторов."""
    with get_connection() as conn:
        rows = conn.execute("SELECT telegram_id FROM users WHERE role = 'admin'").fetchall()
        return [r["telegram_id"] for r in rows]


# ---------- Сессии ----------

def create_session(name: str, owner_id: int) -> dict:
    now = now_seconds()
    code = generate_unique_code()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (name, started, owner_id, code) VALUES (?, ?, ?, ?)",
            (name, now, owner_id, code)
        )
        session_id = cur.lastrowid
        conn.execute(
            "INSERT INTO session_access (session_id, user_id, role, added_at) VALUES (?, ?, 'owner', ?)",
            (session_id, owner_id, now)
        )
    return get_session(session_id)


def get_session(session_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def get_session_by_code(code: str) -> dict | None:
    code = code.strip().upper()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None


def finish_session(session_id: int) -> datetime:
    now = now_seconds()
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET stopped = ? WHERE id = ? AND stopped IS NULL",
            (now, session_id)
        )
    return now


def get_active_session_for_user(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT s.* FROM sessions s
            JOIN session_access a ON a.session_id = s.id
            WHERE a.user_id = ? AND s.stopped IS NULL
            ORDER BY s.id DESC LIMIT 1
        """, (user_id,)).fetchone()
        return dict(row) if row else None


def get_finished_sessions_for_user(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT s.* FROM sessions s
            JOIN session_access a ON a.session_id = s.id
            WHERE a.user_id = ? AND s.stopped IS NOT NULL
            ORDER BY s.id DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_all_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ---------- Доступ и участники ----------

def has_access(session_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM session_access WHERE session_id = ? AND user_id = ?",
            (session_id, user_id)
        ).fetchone()
        return row is not None


def get_session_participants(session_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT u.telegram_id, u.name, a.role
            FROM session_access a
            JOIN users u ON u.telegram_id = a.user_id
            WHERE a.session_id = ?
            ORDER BY a.role DESC, a.added_at
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]


def add_participant(session_id: int, user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO session_access (session_id, user_id, role, added_at)
            VALUES (?, ?, 'participant', ?)
        """, (session_id, user_id, now_seconds()))


# ---------- Запросы на вступление ----------

def create_join_request(session_id: int, user_id: int) -> int | None:
    with get_connection() as conn:
        existing = conn.execute("""
            SELECT id FROM join_requests
            WHERE session_id = ? AND user_id = ? AND status = 'pending'
        """, (session_id, user_id)).fetchone()
        if existing is not None:
            return None
        cur = conn.execute("""
            INSERT INTO join_requests (session_id, user_id, status, created_at)
            VALUES (?, ?, 'pending', ?)
        """, (session_id, user_id, now_seconds()))
        return cur.lastrowid


def get_pending_requests(session_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT r.id, r.user_id, u.name, r.created_at
            FROM join_requests r
            JOIN users u ON u.telegram_id = r.user_id
            WHERE r.session_id = ? AND r.status = 'pending'
            ORDER BY r.id
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]


def get_request(request_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None


def approve_request(request_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        req = dict(row)
        conn.execute("UPDATE join_requests SET status = 'approved' WHERE id = ?", (request_id,))
        conn.execute("""
            INSERT OR IGNORE INTO session_access (session_id, user_id, role, added_at)
            VALUES (?, ?, 'participant', ?)
        """, (req["session_id"], req["user_id"], now_seconds()))
        return req


def reject_request(request_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        req = dict(row)
        conn.execute("UPDATE join_requests SET status = 'rejected' WHERE id = ?", (request_id,))
        return req


def cancel_pending_requests_for_session(session_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE join_requests SET status = 'rejected' WHERE session_id = ? AND status = 'pending'",
            (session_id,)
        )


# ---------- События ----------

def last_event(session_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM events WHERE session_id = ?
            ORDER BY time DESC, id DESC LIMIT 1
        """, (session_id,)).fetchone()
        return dict(row) if row else None


def add_event(session_id: int, event_type: str, *, number: int = None,
              reason: str = None, text: str = None, file_path: str = None,
              time: datetime | None = None) -> int | None:
    if event_type == "start":
        last = last_event(session_id)
        if last is not None and last["event_type"] == "start":
            return None

    event_time = time if time is not None else now_seconds()
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO events (session_id, event_type, number, time, reason, text, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, event_type, number, event_time, reason, text, file_path))
        return cur.lastrowid


def next_stop_number(session_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT MAX(number) AS mx FROM events
            WHERE session_id = ? AND event_type = 'stop'
        """, (session_id,)).fetchone()
        return (row["mx"] + 1) if row["mx"] else 1


def find_awaiting_stop(session_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM events
            WHERE session_id = ? AND event_type = 'stop' AND reason IS NULL
            ORDER BY time DESC, id DESC LIMIT 1
        """, (session_id,)).fetchone()
        return dict(row) if row else None


def set_reason(event_id: int, reason: str | None) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE events SET reason = ? WHERE id = ?", (reason, event_id))


def set_event_text(event_id: int, text: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE events SET text = ? WHERE id = ?", (text, event_id))


def get_session_events(session_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM events WHERE session_id = ? ORDER BY time, id
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]


def count_photos(session_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS c FROM events
            WHERE session_id = ? AND event_type = 'photo'
        """, (session_id,)).fetchone()
        return row["c"]


# ---------- Вычисления «на лету» ----------

def compute_intervals(events: list[dict], session_end: datetime | None = None) -> list[dict]:
    result = [dict(e) for e in events]
    last_start_time = None

    for ev in result:
        if ev["event_type"] == "start":
            last_start_time = ev["time"]
            ev["work_before"] = None
        elif ev["event_type"] == "stop":
            ev["work_before"] = (
                int((ev["time"] - last_start_time).total_seconds())
                if last_start_time is not None else 0
            )
            last_start_time = None
        else:
            ev["work_before"] = None

    for i, ev in enumerate(result):
        if ev["event_type"] != "stop":
            continue
        next_ev = None
        for j in range(i + 1, len(result)):
            if result[j]["event_type"] in ("start", "stop"):
                next_ev = result[j]
                break
        if next_ev is not None:
            ev["stop_duration"] = int((next_ev["time"] - ev["time"]).total_seconds())
        elif session_end is not None:
            ev["stop_duration"] = int((session_end - ev["time"]).total_seconds())
        else:
            ev["stop_duration"] = None

    return result


def total_work_time(events: list[dict], session_start: datetime,
                    session_end: datetime | None = None) -> int:
    intervals = compute_intervals(events, session_end)
    total = sum(
        e["work_before"] for e in intervals
        if e["event_type"] == "stop" and e["work_before"] is not None
    )
    if session_end is not None and intervals:
        last = intervals[-1]
        if last["event_type"] == "start":
            total += int((session_end - last["time"]).total_seconds())
    return total