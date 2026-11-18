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
