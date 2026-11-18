"""Small helpers shared by the bot and the Mini App API."""
from __future__ import annotations

TIERS = (
    (10000, "♛ Legend"),
    (2500, "✪ Whale"),
    (1000, "❖ Shark"),
    (250, "◈ Dolphin"),
    (50, "◇ Fish"),
    (1, "· Shrimp"),
)


def title_for(total: int) -> str:
    for threshold, name in TIERS:
        if total >= threshold:
            return name
    return "Newcomer"
