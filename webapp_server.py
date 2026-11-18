"""aiohttp server: serves the Mini App and its JSON API.

Runs inside the same process as the bot. Every /api/* call must carry the
Telegram `initData` string in the X-Tg-Init-Data header; it is verified with
the bot token, so a caller can only ever act as themselves.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web
from aiogram.types import LabeledPrice

from countries import COUNTRIES
from tg_auth import validate_init_data
from regions import is_valid
from webapp_logic import board_payload, me_payload, regions_map

log = logging.getLogger("worldrichlist.web")

WEBAPP_DIR = Path(__file__).parent / "webapp"
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def create_app(bot: Any, db: Any, cfg: dict[str, Any]) -> web.Application:
    """cfg: token, bot_username, presets, min_stars, max_stars,
    show_sample, sample_stars."""

    @web.middleware
    async def errors(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            log.exception("API error on %s", request.path)
            return _error(500, "Something went wrong. Please try again.")

    async def authenticate(request: web.Request) -> dict[str, Any]:
        user = validate_init_data(
            request.headers.get("X-Tg-Init-Data", ""), cfg["token"]
        )
        if user is None:
            raise web.HTTPUnauthorized(
                text='{"error": "Please open this app from Telegram."}',
                content_type="application/json",
            )
        full_name = " ".join(
            p for p in (user.get("first_name"), user.get("last_name")) if p
        ).strip()
        await asyncio.to_thread(
            db.upsert_user, user["id"], user.get("username"), full_name
        )
        user["_full_name"] = full_name or user.get("username") or "Friend"
        return user

    async def me_for(user: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            me_payload, db, user["id"], user["_full_name"], cfg["bot_username"]
        )

    # ------------------------------------------------------------------ pages
    async def index(_: web.Request) -> web.StreamResponse:
        return web.FileResponse(WEBAPP_DIR / "index.html", headers=NO_CACHE)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    # -------------------------------------------------------------------- API
    async def bootstrap(request: web.Request) -> web.Response:
        user = await authenticate(request)
        return web.json_response(
            {
                "me": await me_for(user),
                "countries": [{"code": c, "name": n} for c, n in COUNTRIES.items()],
                "regions": regions_map(),
                "config": {
                    "presets": cfg["presets"],
                    "min": cfg["min_stars"],
                    "max": cfg["max_stars"],
                    "sample": {"show": cfg["show_sample"], "stars": cfg["sample_stars"]},
                },
            }
        )

    async def board(request: web.Request) -> web.Response:
        user = await authenticate(request)
        q = request.query
        kind = q.get("kind", "places")
        if kind not in ("people", "places"):
            return _error(400, "Unknown board.")
        data = await asyncio.to_thread(
            board_payload, db, user["id"], kind, q.get("metric", "stars"),
            q.get("period", "all"), q.get("scope", "world" if kind == "people" else "countries"),
            q.get("country"),
        )
        return web.json_response(data)

    async def set_region(request: web.Request) -> web.Response:
        user = await authenticate(request)
        body = await request.json()
        key = body.get("key") or None
        country = await asyncio.to_thread(db.get_country, user["id"])
        if key is not None and not is_valid(country, str(key)):
            return _error(400, "Unknown region.")
        if not await asyncio.to_thread(db.set_region, user["id"], key):
            return _error(409, "Your region is locked after your first donation.")
        return web.json_response({"me": await me_for(user)})

    async def set_country(request: web.Request) -> web.Response:
        user = await authenticate(request)
        body = await request.json()
        code = str(body.get("code", "")).upper()
        if code not in COUNTRIES:
            return _error(400, "Unknown country.")
        if not await asyncio.to_thread(db.set_country, user["id"], code):
            return _error(409, "Your country is locked after your first donation.")
        return web.json_response({"me": await me_for(user)})

    async def toggle_anon(request: web.Request) -> web.Response:
        user = await authenticate(request)
        await asyncio.to_thread(db.toggle_anonymous, user["id"])
        return web.json_response({"me": await me_for(user)})

    async def invoice(request: web.Request) -> web.Response:
        user = await authenticate(request)
        body = await request.json()
        try:
            amount = int(body.get("amount"))
        except (TypeError, ValueError):
            return _error(400, "Enter a whole number of Stars.")
        if not (cfg["min_stars"] <= amount <= cfg["max_stars"]):
            return _error(
                400, f"Amount must be between {cfg['min_stars']} and {cfg['max_stars']} Stars."
            )
        country = await asyncio.to_thread(db.get_country, user["id"])
        if not country:
            return _error(400, "Choose your country first.")
        link = await bot.create_invoice_link(
            title=f"Donate {amount} ⭐",
            description=f"{amount} Telegram Stars for {COUNTRIES[country]} on the Global Leaderboard!",
            payload=f"donate:{user['id']}:{amount}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
            provider_token="",
        )
        return web.json_response({"link": link})

    app = web.Application(middlewares=[errors])
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/app", index)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/board", board)
    app.router.add_post("/api/country", set_country)
    app.router.add_post("/api/region", set_region)
    app.router.add_post("/api/anon", toggle_anon)
    app.router.add_post("/api/invoice", invoice)
    return app
