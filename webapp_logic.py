"""Pure data shaping for the Mini App API (no aiohttp / aiogram imports).

Privacy rule: other people's user_ids are never sent, and anonymous donors
are masked for everyone except themselves.
"""
from __future__ import annotations

from typing import Any, Optional

from common import title_for

COUNTRY_ROWS_LIMIT = 100
DONOR_ROWS_LIMIT = 50


def _display(row: dict[str, Any]) -> str:
    if row.get("username"):
        return "@" + row["username"]
    return (row.get("full_name") or "").strip() or "Donor"


def public_donor(row: dict[str, Any], viewer_id: int) -> dict[str, Any]:
    is_me = row["user_id"] == viewer_id
    anon = bool(row.get("is_anonymous"))
    return {
        "pos": int(row["pos"]),
        "name": "Anonymous" if (anon and not is_me) else _display(row),
        "anon": anon,
        "is_me": is_me,
        "code": (row.get("country_code") or None),
        "stars": int(row["stars"]),
    }


def me_payload(
    db: Any, user_id: int, full_name: str, bot_username: str
) -> dict[str, Any]:
    s = db.user_stats(user_id)
    return {
        "name": full_name,
        "title": title_for(s["total"]),
        "total": s["total"],
        "locked": s["total"] > 0,
        "donor_no": s["donor_no"],
        "anon": s["is_anonymous"],
        "country": s["country"],
        "country_pos": s["country_pos"],
        "country_stars": s["country_stars"],
        "pos": s["pos"],
        "week_stars": s["week_stars"],
        "week_pos": s["week_pos"],
        "invited": s["invited"],
        "link": f"https://t.me/{bot_username}?start=ref_{user_id}",
    }


def board_payload(db: Any, view: str, period: str, user_id: int) -> dict[str, Any]:
    period = "week" if period == "week" else "all"

    if view == "d":
        rows, me = db.leaderboard_users(period, DONOR_ROWS_LIMIT, user_id)
        return {
            "view": "d",
            "period": period,
            "rows": [public_donor(r, user_id) for r in rows],
            "mine": {"pos": int(me["pos"]), "stars": int(me["stars"])} if me else None,
        }

    if view == "m":
        code = db.get_country(user_id)
        if not code:
            return {"view": "m", "period": "all", "country": None, "rows": [], "mine": None}
        rows = db.country_top_donors(code, DONOR_ROWS_LIMIT)
        return {
            "view": "m",
            "period": "all",
            "country": code,
            "rows": [public_donor(r, user_id) for r in rows],
            "mine": None,
        }

    my_country: Optional[str] = db.get_country(user_id)
    rows, mine = db.leaderboard_countries(period, COUNTRY_ROWS_LIMIT, my_country)
    return {
        "view": "c",
        "period": period,
        "rows": [
            {
                "pos": int(r["pos"]),
                "code": r["country_code"],
                "stars": int(r["stars"]),
                "donors": int(r["donors"]),
            }
            for r in rows
        ],
        "mine": (
            {"code": my_country, "pos": int(mine["pos"]), "stars": int(mine["stars"])}
            if mine
            else None
        ),
    }
