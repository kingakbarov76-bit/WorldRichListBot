"""SQLite storage for WorldRichListBot (stdlib only, thread-safe)."""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Optional

WEEK_SECONDS = 7 * 24 * 3600

_ALL_TIME_CTE = """
WITH r AS (
    SELECT user_id, username, full_name, is_anonymous,
           total_stars AS stars,
           ROW_NUMBER() OVER (
               ORDER BY total_stars DESC, updated_at ASC, user_id ASC
           ) AS pos
    FROM users
    WHERE total_stars > 0
)
"""

_WEEKLY_CTE = """
WITH s AS (
    SELECT user_id, SUM(amount) AS stars, MAX(ts) AS last_ts
    FROM payments
    WHERE ts >= :since
    GROUP BY user_id
),
r AS (
    SELECT u.user_id, u.username, u.full_name, u.is_anonymous,
           s.stars AS stars,
           ROW_NUMBER() OVER (
               ORDER BY s.stars DESC, s.last_ts ASC, s.user_id ASC
           ) AS pos
    FROM s JOIN users u ON u.user_id = s.user_id
)
"""


class Database:
    def __init__(self, path: str = "worldrichlist.db") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id      INTEGER PRIMARY KEY,
                    username     TEXT,
                    full_name    TEXT NOT NULL DEFAULT '',
                    total_stars  INTEGER NOT NULL DEFAULT 0,
                    is_anonymous INTEGER NOT NULL DEFAULT 0,
                    donor_no     INTEGER,
                    referrer_id  INTEGER,
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments (
                    charge_id TEXT PRIMARY KEY,
                    user_id   INTEGER NOT NULL,
                    amount    INTEGER NOT NULL,
                    ts        REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_payments_ts ON payments(ts);
                CREATE INDEX IF NOT EXISTS idx_users_total ON users(total_stars);
                CREATE INDEX IF NOT EXISTS idx_users_referrer ON users(referrer_id);
                """
            )

    # ------------------------------------------------------------------- users
    def upsert_user(
        self, user_id: int, username: Optional[str], full_name: str
    ) -> bool:
        """Create or refresh a user. Returns True if the user is new."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO users"
                "(user_id, username, full_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, full_name or "", now, now),
            )
            if cur.rowcount == 1:
                return True
            # Refresh the name only; updated_at is the leaderboard tie-break
            # (time of last donation) and must not change here.
            self._conn.execute(
                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                (username, full_name or "", user_id),
            )
            return False

    def user_exists(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row is not None

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        if user_id == referrer_id:
            return False
        with self._lock:
            ref = self._conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (referrer_id,)
            ).fetchone()
            if ref is None:
                return False
            cur = self._conn.execute(
                "UPDATE users SET referrer_id = ? "
                "WHERE user_id = ? AND referrer_id IS NULL",
                (referrer_id, user_id),
            )
            return cur.rowcount == 1

    def toggle_anonymous(self, user_id: int) -> bool:
        """Flip anonymous mode, return the new value."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET is_anonymous = 1 - is_anonymous WHERE user_id = ?",
                (user_id,),
            )
            row = self._conn.execute(
                "SELECT is_anonymous FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return bool(row["is_anonymous"]) if row else False

    def is_anonymous(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT is_anonymous FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return bool(row["is_anonymous"]) if row else False

    # ---------------------------------------------------------------- payments
    def _pos_all_time(self, user_id: int) -> Optional[int]:
        row = self._conn.execute(
            _ALL_TIME_CTE + "SELECT pos FROM r WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["pos"]) if row else None

    def record_payment(
        self, user_id: int, amount: int, charge_id: str
    ) -> Optional[dict[str, Any]]:
        """Credit a successful payment exactly once.

        Returns None if this charge_id was already processed (duplicate),
        otherwise a dict with old/new rank and totals.
        """
        now = time.time()
        with self._lock:
            c = self._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "INSERT OR IGNORE INTO users"
                    "(user_id, full_name, created_at, updated_at) "
                    "VALUES (?, '', ?, ?)",
                    (user_id, now, now),
                )
                cur = c.execute(
                    "INSERT OR IGNORE INTO payments(charge_id, user_id, amount, ts) "
                    "VALUES (?, ?, ?, ?)",
                    (charge_id, user_id, amount, now),
                )
                if cur.rowcount == 0:
                    c.execute("COMMIT")
                    return None

                old_pos = self._pos_all_time(user_id)
                row = c.execute(
                    "SELECT total_stars, donor_no FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                old_total = int(row["total_stars"])
                donor_no = row["donor_no"]
                if donor_no is None:
                    donor_no = c.execute(
                        "SELECT COALESCE(MAX(donor_no), 0) + 1 FROM users"
                    ).fetchone()[0]
                c.execute(
                    "UPDATE users SET total_stars = total_stars + ?, "
                    "donor_no = ?, updated_at = ? WHERE user_id = ?",
                    (amount, donor_no, now, user_id),
                )
                new_pos = self._pos_all_time(user_id)
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        return {
            "old_pos": old_pos,
            "new_pos": new_pos,
            "old_total": old_total,
            "new_total": old_total + amount,
            "donor_no": int(donor_no),
            "is_first_donation": old_total == 0,
        }

    # ------------------------------------------------------------- leaderboard
    def leaderboard(
        self, period: str, limit: int, user_id: int
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        """Return (top rows, caller's own {pos, stars} or None).

        period: "all" (all-time) or "week" (rolling 7 days).
        """
        if period == "week":
            cte = _WEEKLY_CTE
            params: dict[str, Any] = {"since": time.time() - WEEK_SECONDS}
        else:
            cte = _ALL_TIME_CTE
            params = {}
        with self._lock:
            rows = self._conn.execute(
                cte + "SELECT * FROM r WHERE pos <= :limit ORDER BY pos",
                {**params, "limit": limit},
            ).fetchall()
            me = self._conn.execute(
                cte + "SELECT pos, stars FROM r WHERE user_id = :uid",
                {**params, "uid": user_id},
            ).fetchone()
        return [dict(r) for r in rows], (dict(me) if me else None)

    # ------------------------------------------------------------------- stats
    def user_stats(self, user_id: int) -> dict[str, Any]:
        _, all_me = self.leaderboard("all", 0, user_id)
        _, week_me = self.leaderboard("week", 0, user_id)
        with self._lock:
            u = self._conn.execute(
                "SELECT total_stars, donor_no, is_anonymous FROM users "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            invited = self._conn.execute(
                "SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)
            ).fetchone()[0]
        return {
            "total": int(u["total_stars"]) if u else 0,
            "donor_no": u["donor_no"] if u else None,
            "is_anonymous": bool(u["is_anonymous"]) if u else False,
            "pos": all_me["pos"] if all_me else None,
            "week_stars": week_me["stars"] if week_me else 0,
            "week_pos": week_me["pos"] if week_me else None,
            "invited": int(invited),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
