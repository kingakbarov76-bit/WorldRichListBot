"""SQL fragments for the leaderboards (no driver imports; pure strings).

All tables live in the dedicated `worldrichlist` schema.
Ranking tie-break: whoever reached the amount first ranks higher.
"""
from __future__ import annotations

S = "worldrichlist"

WEEK = "interval '7 days'"


def user_cte(period: str) -> str:
    """CTE `r(user_id, username, full_name, is_anonymous, country_code, stars, pos)`."""
    if period == "week":
        return f"""
WITH s AS (
    SELECT user_id, SUM(amount)::bigint AS stars, MAX(ts) AS last_ts
    FROM {S}.payments
    WHERE ts >= now() - {WEEK}
    GROUP BY user_id
),
r AS (
    SELECT u.user_id, u.username, u.full_name, u.is_anonymous, u.country_code,
           s.stars,
           ROW_NUMBER() OVER (ORDER BY s.stars DESC, s.last_ts ASC, s.user_id ASC) AS pos
    FROM s JOIN {S}.users u ON u.user_id = s.user_id
)
"""
    return f"""
WITH r AS (
    SELECT user_id, username, full_name, is_anonymous, country_code,
           total_stars AS stars,
           ROW_NUMBER() OVER (ORDER BY total_stars DESC, updated_at ASC, user_id ASC) AS pos
    FROM {S}.users
    WHERE total_stars > 0
)
"""


def country_cte(period: str) -> str:
    """CTE `r(country_code, stars, donors, pos)`."""
    cond = f"AND ts >= now() - {WEEK}" if period == "week" else ""
    return f"""
WITH c AS (
    SELECT country_code, SUM(amount)::bigint AS stars,
           COUNT(DISTINCT user_id)::bigint AS donors, MAX(ts) AS last_ts
    FROM {S}.payments
    WHERE country_code IS NOT NULL {cond}
    GROUP BY country_code
),
r AS (
    SELECT country_code, stars, donors,
           ROW_NUMBER() OVER (ORDER BY stars DESC, last_ts ASC, country_code ASC) AS pos
    FROM c
)
"""


def country_donors_cte() -> str:
    """CTE `r(user_id, username, full_name, is_anonymous, stars, pos)` for ONE
    country (param %s = country code), all-time."""
    return f"""
WITH r AS (
    SELECT user_id, username, full_name, is_anonymous, country_code,
           total_stars AS stars,
           ROW_NUMBER() OVER (ORDER BY total_stars DESC, updated_at ASC, user_id ASC) AS pos
    FROM {S}.users
    WHERE total_stars > 0 AND country_code = %s
)
"""


# ======================================================================
# Multi-level boards (Stars / Friends  x  World / Country / Region)
# ======================================================================
def _since(period: str, col: str) -> str:
    return f"AND {col} >= now() - {WEEK}" if period == "week" else ""


_PEOPLE_COLS = "u.user_id, u.username, u.full_name, u.is_anonymous, u.country_code, u.region_code"
_GEO = {
    "world": "",
    "country": "AND u.country_code = %s",
    "region": "AND u.region_code = %s",
}


def people_ranked(metric: str, period: str, scope: str) -> str:
    """CTE `ranked(user_id, username, full_name, is_anonymous, country_code,
    region_code, value, pos)`.

    metric: "stars" | "friends"   (friends = invited people who picked a country)
    period: "all" | "week"
    scope : "world" | "country" | "region" -> ONE %s param (country code / region key)
            for country/region, none for world.
    """
    geo = _GEO[scope]
    if metric == "friends":
        return f"""
WITH f AS (
    SELECT referrer_id AS user_id, COUNT(*)::bigint AS value, MAX(activated_at) AS last_ts
    FROM {S}.users
    WHERE referrer_id IS NOT NULL AND activated_at IS NOT NULL {_since(period, "activated_at")}
    GROUP BY referrer_id
),
ranked AS (
    SELECT {_PEOPLE_COLS}, f.value,
           ROW_NUMBER() OVER (ORDER BY f.value DESC, f.last_ts ASC, u.user_id ASC) AS pos
    FROM f JOIN {S}.users u ON u.user_id = f.user_id
    WHERE TRUE {geo}
)
"""
    if period == "week":
        return f"""
WITH s AS (
    SELECT user_id, SUM(amount)::bigint AS value, MAX(ts) AS last_ts
    FROM {S}.payments
    WHERE ts >= now() - {WEEK}
    GROUP BY user_id
),
ranked AS (
    SELECT {_PEOPLE_COLS}, s.value,
           ROW_NUMBER() OVER (ORDER BY s.value DESC, s.last_ts ASC, u.user_id ASC) AS pos
    FROM s JOIN {S}.users u ON u.user_id = s.user_id
    WHERE TRUE {geo}
)
"""
    return f"""
WITH ranked AS (
    SELECT {_PEOPLE_COLS}, u.total_stars AS value,
           ROW_NUMBER() OVER (ORDER BY u.total_stars DESC, u.updated_at ASC, u.user_id ASC) AS pos
    FROM {S}.users u
    WHERE u.total_stars > 0 {geo}
)
"""


def places_ranked(metric: str, period: str, level: str) -> str:
    """CTE `ranked(place, value, members, pos)`.

    level: "countries" | "regions" -> "regions" takes ONE %s param (country code).
    For metric "friends", a place's value is the number of friends invited by
    people who live there.
    """
    col = "country_code" if level == "countries" else "region_code"
    in_country_p = "AND p.country_code = %s" if level == "regions" else ""
    in_country_r = "AND rr.country_code = %s" if level == "regions" else ""
    if metric == "friends":
        return f"""
WITH c AS (
    SELECT rr.{col} AS place, COUNT(*)::bigint AS value,
           COUNT(DISTINCT u.referrer_id)::bigint AS members, MAX(u.activated_at) AS last_ts
    FROM {S}.users u JOIN {S}.users rr ON rr.user_id = u.referrer_id
    WHERE u.activated_at IS NOT NULL AND rr.{col} IS NOT NULL {in_country_r}
          {_since(period, "u.activated_at")}
    GROUP BY rr.{col}
),
ranked AS (
    SELECT place, value, members,
           ROW_NUMBER() OVER (ORDER BY value DESC, last_ts ASC, place ASC) AS pos
    FROM c
)
"""
    return f"""
WITH c AS (
    SELECT p.{col} AS place, SUM(p.amount)::bigint AS value,
           COUNT(DISTINCT p.user_id)::bigint AS members, MAX(p.ts) AS last_ts
    FROM {S}.payments p
    WHERE p.{col} IS NOT NULL {in_country_p} {_since(period, "p.ts")}
    GROUP BY p.{col}
),
ranked AS (
    SELECT place, value, members,
           ROW_NUMBER() OVER (ORDER BY value DESC, last_ts ASC, place ASC) AS pos
    FROM c
)
"""
