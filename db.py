"""Postgres (Supabase) storage for WorldRichListBot.

Sync psycopg3 + connection pool; the bot calls these methods through
asyncio.to_thread. Use the Supabase *Session pooler* connection string
(IPv4-compatible) as DATABASE_URL.
"""
from __future__ import annotations

from typing import Any, Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from regions import is_valid
from sql_queries import (
    S, country_cte, country_donors_cte, people_ranked, places_ranked, user_cte,
)


class Database:
    def __init__(self, dsn: str) -> None:
        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=5,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": None,  # safe behind poolers
                "connect_timeout": 15,
            },
            open=False,
        )
        self.pool.open(wait=True, timeout=30)

    # ------------------------------------------------------------ low level
    def _one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall()

    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).rowcount

    # ---------------------------------------------------------------- users
    def upsert_user(
        self, user_id: int, username: Optional[str], full_name: str
    ) -> bool:
        """Create/refresh a user. Returns True if the user is new."""
        row = self._one(
            f"""
            INSERT INTO {S}.users (user_id, username, full_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
               SET username = EXCLUDED.username, full_name = EXCLUDED.full_name
            RETURNING (xmax = 0) AS inserted
            """,
            (user_id, username, full_name or ""),
        )
        return bool(row and row["inserted"])

    def user_exists(self, user_id: int) -> bool:
        return self._one(
            f"SELECT 1 AS x FROM {S}.users WHERE user_id = %s", (user_id,)
        ) is not None

    def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        if user_id == referrer_id:
            return False
        return (
            self._exec(
                f"""
                UPDATE {S}.users SET referrer_id = %s
                WHERE user_id = %s AND referrer_id IS NULL AND activated_at IS NULL
                  AND EXISTS (SELECT 1 FROM {S}.users WHERE user_id = %s)
                """,
                (referrer_id, user_id, referrer_id),
            )
            == 1
        )

    def toggle_anonymous(self, user_id: int) -> bool:
        row = self._one(
            f"UPDATE {S}.users SET is_anonymous = NOT is_anonymous "
            f"WHERE user_id = %s RETURNING is_anonymous",
            (user_id,),
        )
        return bool(row["is_anonymous"]) if row else False

    def is_anonymous(self, user_id: int) -> bool:
        row = self._one(
            f"SELECT is_anonymous FROM {S}.users WHERE user_id = %s", (user_id,)
        )
        return bool(row["is_anonymous"]) if row else False

    # -------------------------------------------------------------- country
    def get_country(self, user_id: int) -> Optional[str]:
        row = self._one(
            f"SELECT country_code FROM {S}.users WHERE user_id = %s", (user_id,)
        )
        return row["country_code"] if row and row["country_code"] else None

    def has_donated(self, user_id: int) -> bool:
        row = self._one(
            f"SELECT total_stars FROM {S}.users WHERE user_id = %s", (user_id,)
        )
        return bool(row and row["total_stars"] > 0)

    def set_country(self, user_id: int, code: str) -> bool:
        """Choose a country. Locked after the first donation.

        Changing the country clears the region; the first pick "activates" the
        user (this is what makes an invite count as a referral).
        """
        return (
            self._exec(
                f"""
                UPDATE {S}.users
                SET region_code = CASE WHEN country_code IS DISTINCT FROM %s
                                       THEN NULL ELSE region_code END,
                    country_code = %s,
                    activated_at = COALESCE(activated_at, now())
                WHERE user_id = %s AND (total_stars = 0 OR country_code IS NULL)
                """,
                (code.upper(), code.upper(), user_id),
            )
            == 1
        )

    def get_region(self, user_id: int) -> Optional[str]:
        row = self._one(
            f"SELECT region_code FROM {S}.users WHERE user_id = %s", (user_id,)
        )
        return row["region_code"] if row and row["region_code"] else None

    def set_region(self, user_id: int, key: Optional[str]) -> bool:
        """Set/clear the region. Allowed any time while it is still empty;
        once set it is locked after the first donation. Past payments are
        back-filled so regional boards stay consistent."""
        country = self.get_country(user_id)
        if not country or (key is not None and not is_valid(country, key)):
            return False
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {S}.users SET region_code = %s
                    WHERE user_id = %s
                      AND (total_stars = 0 OR region_code IS NULL)
                    """,
                    (key, user_id),
                )
                if cur.rowcount != 1:
                    return False
                cur.execute(
                    f"UPDATE {S}.payments SET region_code = %s "
                    f"WHERE user_id = %s AND region_code IS NULL",
                    (key, user_id),
                )
        return True

    # ------------------------------------------------------------- payments
    def _user_pos(self, cur: Any, user_id: int) -> Optional[int]:
        cur.execute(user_cte("all") + "SELECT pos FROM r WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return int(row["pos"]) if row else None

    def _country_pos(self, cur: Any, code: str) -> Optional[int]:
        cur.execute(
            country_cte("all") + "SELECT pos FROM r WHERE country_code = %s", (code,)
        )
        row = cur.fetchone()
        return int(row["pos"]) if row else None

    def record_payment(
        self, user_id: int, amount: int, charge_id: str
    ) -> Optional[dict[str, Any]]:
        """Credit a successful payment exactly once (None = duplicate)."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {S}.users (user_id) VALUES (%s) "
                    f"ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                )
                # Row lock serialises concurrent payments of the same user.
                cur.execute(
                    f"SELECT total_stars, donor_no, country_code, region_code FROM {S}.users "
                    f"WHERE user_id = %s FOR UPDATE",
                    (user_id,),
                )
                u = cur.fetchone()
                cur.execute(
                    f"SELECT 1 AS x FROM {S}.payments WHERE charge_id = %s",
                    (charge_id,),
                )
                if cur.fetchone():
                    return None

                country = u["country_code"]
                old_total = int(u["total_stars"])
                old_pos = self._user_pos(cur, user_id)
                old_cpos = self._country_pos(cur, country) if country else None

                cur.execute(
                    f"INSERT INTO {S}.payments "
                    f"(charge_id, user_id, amount, country_code, region_code) "
                    f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (charge_id) DO NOTHING",
                    (charge_id, user_id, amount, country, u["region_code"]),
                )
                if cur.rowcount == 0:
                    return None

                donor_no = u["donor_no"]
                if donor_no is None:
                    cur.execute("SELECT nextval('worldrichlist.donor_seq') AS n")
                    donor_no = int(cur.fetchone()["n"])
                cur.execute(
                    f"UPDATE {S}.users SET total_stars = total_stars + %s, "
                    f"donor_no = %s, updated_at = now() WHERE user_id = %s",
                    (amount, donor_no, user_id),
                )

                new_pos = self._user_pos(cur, user_id)
                new_cpos = self._country_pos(cur, country) if country else None

                passed: list[str] = []
                if (
                    country
                    and old_cpos is not None
                    and new_cpos is not None
                    and new_cpos < old_cpos
                ):
                    cur.execute(
                        country_cte("all")
                        + "SELECT country_code FROM r WHERE pos > %s AND pos <= %s "
                        "ORDER BY pos",
                        (new_cpos, old_cpos),
                    )
                    passed = [r["country_code"] for r in cur.fetchall()]

        return {
            "old_pos": old_pos,
            "new_pos": new_pos,
            "old_total": old_total,
            "new_total": old_total + amount,
            "donor_no": int(donor_no),
            "is_first_donation": old_total == 0,
            "country": country,
            "old_country_pos": old_cpos,
            "new_country_pos": new_cpos,
            "passed_countries": passed,
        }

    # ---------------------------------------------------------- leaderboards
    def leaderboard_users(
        self, period: str, limit: int, user_id: int
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        cte = user_cte(period)
        rows = self._all(cte + "SELECT * FROM r WHERE pos <= %s ORDER BY pos", (limit,))
        me = self._one(cte + "SELECT pos, stars FROM r WHERE user_id = %s", (user_id,))
        return rows, me

    def leaderboard_countries(
        self, period: str, limit: int, country_code: Optional[str]
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        cte = country_cte(period)
        rows = self._all(cte + "SELECT * FROM r WHERE pos <= %s ORDER BY pos", (limit,))
        mine = None
        if country_code:
            mine = self._one(
                cte + "SELECT country_code, pos, stars, donors FROM r "
                "WHERE country_code = %s",
                (country_code,),
            )
        return rows, mine

    def country_top_donors(self, country_code: str, limit: int) -> list[dict[str, Any]]:
        return self._all(
            country_donors_cte() + "SELECT * FROM r WHERE pos <= %s ORDER BY pos",
            (country_code, limit),
        )

    def donors_of_countries(
        self, codes: list[str], exclude_user: int, limit: int
    ) -> list[dict[str, Any]]:
        """Top donors of the given countries (to send 'you were overtaken')."""
        if not codes:
            return []
        return self._all(
            f"""
            SELECT user_id, country_code FROM {S}.users
            WHERE country_code = ANY(%s) AND total_stars > 0 AND user_id <> %s AND notify
            ORDER BY total_stars DESC
            LIMIT %s
            """,
            (codes, exclude_user, limit),
        )

    # ------------------------------------------------- multi-level boards
    def board_people(
        self, metric: str, period: str, scope: str, user_id: int,
        geo_key: Optional[str], limit: int,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        cte = people_ranked(metric, period, scope)
        gp: tuple = (geo_key,) if scope != "world" else ()
        rows = self._all(cte + "SELECT * FROM ranked WHERE pos <= %s ORDER BY pos", gp + (limit,))
        me = self._one(cte + "SELECT pos, value FROM ranked WHERE user_id = %s", gp + (user_id,))
        return rows, me

    def board_places(
        self, metric: str, period: str, level: str, country: Optional[str],
        my_key: Optional[str], limit: int,
    ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
        cte = places_ranked(metric, period, level)
        gp: tuple = (country,) if level == "regions" else ()
        rows = self._all(cte + "SELECT * FROM ranked WHERE pos <= %s ORDER BY pos", gp + (limit,))
        mine = None
        if my_key:
            mine = self._one(
                cte + "SELECT place, pos, value, members FROM ranked WHERE place = %s",
                gp + (my_key,),
            )
        return rows, mine

    def ranks(self, user_id: int) -> dict[str, Any]:
        """Position of the user on every board (all-time), or None if absent."""
        u = self._one(
            f"SELECT country_code, region_code FROM {S}.users WHERE user_id = %s",
            (user_id,),
        ) or {}
        cc, rc = u.get("country_code"), u.get("region_code")
        out: dict[str, Any] = {"country": cc, "region": rc}
        for metric in ("stars", "friends"):
            for scope, key in (("world", None), ("country", cc), ("region", rc)):
                if scope != "world" and not key:
                    out[f"{metric}_{scope}"] = None
                    continue
                _, me = self.board_people(metric, "all", scope, user_id, key, 0)
                out[f"{metric}_{scope}"] = (
                    {"pos": int(me["pos"]), "value": int(me["value"])} if me else None
                )
        return out

    # ------------------------------------------------------ notifications/meta
    def get_notify(self, user_id: int) -> bool:
        row = self._one(f"SELECT notify FROM {S}.users WHERE user_id = %s", (user_id,))
        return bool(row["notify"]) if row else True

    def toggle_notify(self, user_id: int) -> bool:
        row = self._one(
            f"UPDATE {S}.users SET notify = NOT notify WHERE user_id = %s RETURNING notify",
            (user_id,),
        )
        return bool(row["notify"]) if row else True

    def current_week_key(self) -> str:
        row = self._one(
            "SELECT to_char(date_trunc('week', now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS k"
        )
        return row["k"]

    def get_meta(self, key: str) -> Optional[str]:
        row = self._one(f"SELECT value FROM {S}.meta WHERE key = %s", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._exec(
            f"INSERT INTO {S}.meta (key, value) VALUES (%s, %s) "
            f"ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )

    def audience_of_country(self, code: str, limit: int) -> list[int]:
        rows = self._all(
            f"SELECT user_id FROM {S}.users WHERE country_code = %s AND notify "
            f"ORDER BY total_stars DESC LIMIT %s",
            (code, limit),
        )
        return [int(r["user_id"]) for r in rows]

    # ------------------------------------------------ weekly battle & feed
    def battle(self) -> dict[str, Any]:
        """Calendar-week (Mon 00:00 UTC) country race: this week's top 3,
        last week's winner and seconds until reset."""
        def top(col: str, lo: str, hi: str) -> list[dict[str, Any]]:
            return self._all(
                f"""
                SELECT {col} AS place, SUM(amount)::bigint AS value,
                       COUNT(DISTINCT user_id)::bigint AS members
                FROM {S}.payments
                WHERE {col} IS NOT NULL
                  AND ts >= {lo} AND ts < {hi}
                GROUP BY {col}
                ORDER BY value DESC, MAX(ts) ASC, {col} ASC
                LIMIT 3
                """
            )
        wk = "date_trunc('week', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
        now_hi = "now() + interval '1 day'"
        last_lo = f"({wk} - interval '7 days')"
        reset = self._one(
            f"SELECT EXTRACT(EPOCH FROM (({wk} + interval '7 days') - now()))::bigint AS s"
        )
        return {
            "now": top("country_code", wk, now_hi),
            "last": top("country_code", last_lo, wk),
            "last_regions": top("region_code", last_lo, wk),
            "resets_in": int(reset["s"]) if reset else 0,
        }

    def feed(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            f"""
            SELECT p.user_id, p.amount, p.country_code, p.region_code,
                   EXTRACT(EPOCH FROM (now() - p.ts))::bigint AS ago,
                   u.is_anonymous, u.username, u.full_name
            FROM {S}.payments p JOIN {S}.users u ON u.user_id = p.user_id
            ORDER BY p.ts DESC LIMIT %s
            """,
            (limit,),
        )

    # ------------------------------------------------------------------ stats
    def user_stats(self, user_id: int) -> dict[str, Any]:
        _, all_me = self.leaderboard_users("all", 0, user_id)
        _, week_me = self.leaderboard_users("week", 0, user_id)
        u = self._one(
            f"SELECT total_stars, donor_no, is_anonymous, country_code "
            f"FROM {S}.users WHERE user_id = %s",
            (user_id,),
        )
        invited = self._one(
            f"SELECT COUNT(*)::bigint AS n FROM {S}.users WHERE referrer_id = %s",
            (user_id,),
        )
        country = u["country_code"] if u else None
        _, c_all = self.leaderboard_countries("all", 0, country)
        return {
            "total": int(u["total_stars"]) if u else 0,
            "donor_no": u["donor_no"] if u else None,
            "is_anonymous": bool(u["is_anonymous"]) if u else False,
            "country": country,
            "pos": int(all_me["pos"]) if all_me else None,
            "week_stars": int(week_me["stars"]) if week_me else 0,
            "week_pos": int(week_me["pos"]) if week_me else None,
            "invited": int(invited["n"]) if invited else 0,
            "country_pos": int(c_all["pos"]) if c_all else None,
            "country_stars": int(c_all["stars"]) if c_all else 0,
        }

    def close(self) -> None:
        self.pool.close()
