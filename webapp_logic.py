"""Pure data shaping for the Mini App API (no aiohttp / aiogram imports).

Privacy rule: other people's user_ids are never sent, and anonymous donors
are masked for everyone except themselves.
"""
from __future__ import annotations

from typing import Any, Optional

from common import title_for
from regions import is_valid, regions_of

PLACE_ROWS_LIMIT = 100
PEOPLE_ROWS_LIMIT = 50
METRICS = ('stars', 'friends')


def _display(row: dict[str, Any]) -> str:
    if row.get("username"):
        return "@" + row["username"]
    return (row.get("full_name") or "").strip() or "Donor"


def public_person(row: dict[str, Any], viewer_id: int) -> dict[str, Any]:
    is_me = row["user_id"] == viewer_id
    anon = bool(row.get("is_anonymous"))
    return {
        "pos": int(row["pos"]),
        "name": "Anonymous" if (anon and not is_me) else _display(row),
        "anon": anon,
        "is_me": is_me,
        "code": row.get("country_code") or None,
        "region": row.get("region_code") or None,
        "value": int(row["value"]),
    }


def me_payload(db: Any, user_id: int, full_name: str, bot_username: str) -> dict[str, Any]:
    s = db.user_stats(user_id)
    return {
        "name": full_name,
        "title": title_for(s["total"]),
        "total": s["total"],
        "locked": s["total"] > 0,
        "donor_no": s["donor_no"],
        "anon": s["is_anonymous"],
        "country": s["country"],
        "region": db.get_region(user_id),
        "pos": s["pos"],
        "week_stars": s["week_stars"],
        "week_pos": s["week_pos"],
        "invited": s["invited"],
        "ranks": db.ranks(user_id),
        "link": f"https://t.me/{bot_username}?start=ref_{user_id}",
    }


def regions_map() -> dict[str, list[dict[str, str]]]:
    from regions import REGIONS
    return {cc: [{"key": k, "name": n} for k, n in items] for cc, items in REGIONS.items()}


def board_payload(
    db: Any, user_id: int, kind: str, metric: str, period: str,
    scope: str, country: Optional[str] = None,
) -> dict[str, Any]:
    """kind "people": scope world|country|region (the viewer's own country/region).
    kind "places": scope countries|regions (regions of `country`, default own)."""
    metric = metric if metric in METRICS else "stars"
    period = "week" if period == "week" else "all"
    my_country = db.get_country(user_id)
    my_region = db.get_region(user_id)
    base = {"kind": kind, "metric": metric, "period": period, "scope": scope}

    if kind == "people":
        if scope not in ("world", "country", "region"):
            scope = "world"
        geo = {"world": None, "country": my_country, "region": my_region}[scope]
        base["scope"] = scope
        if scope != "world" and not geo:
            return {**base, "geo": None, "rows": [], "mine": None}
        rows, me = db.board_people(metric, period, scope, user_id, geo, PEOPLE_ROWS_LIMIT)
        return {
            **base,
            "geo": geo,
            "rows": [public_person(r, user_id) for r in rows],
            "mine": {"pos": int(me["pos"]), "value": int(me["value"])} if me else None,
        }

    level = "regions" if scope == "regions" else "countries"
    base["scope"] = level
    if level == "regions":
        country = (country or my_country or "").upper()
        if not regions_of(country):
            return {**base, "country": country or None, "rows": [], "mine": None}
        my_key = my_region if my_country == country else None
        rows, mine = db.board_places(metric, period, level, country, my_key, PLACE_ROWS_LIMIT)
    else:
        country = None
        rows, mine = db.board_places(metric, period, level, None, my_country, PLACE_ROWS_LIMIT)
    return {
        **base,
        "country": country,
        "rows": [
            {"pos": int(r["pos"]), "key": r["place"], "value": int(r["value"]),
             "members": int(r["members"])}
            for r in rows
        ],
        "mine": (
            {"key": mine["place"], "pos": int(mine["pos"]), "value": int(mine["value"])}
            if mine else None
        ),
    }


def pulse_payload(db: Any, user_id: int) -> dict[str, Any]:
    """Weekly battle + live feed. Real events only; anonymous names masked."""
    b = db.battle()
    pick = lambda rows: [{"key": r["place"], "value": int(r["value"]), "members": int(r["members"])} for r in rows]
    feed = []
    for r in db.feed(15):
        me = r["user_id"] == user_id
        anon = bool(r.get("is_anonymous")) and not me
        feed.append({
            "name": "Anonymous" if anon else _display(r),
            "is_me": me,
            "amount": int(r["amount"]),
            "code": r.get("country_code"),
            "region": r.get("region_code"),
            "ago": int(r["ago"]),
        })
    return {
        "battle": {
            "now": pick(b["now"]), "last": pick(b["last"]),
            "last_regions": pick(b["last_regions"]), "resets_in": b["resets_in"],
        },
        "feed": feed,
    }
