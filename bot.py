"""WorldRichListBot - Country vs Country leaderboard of Telegram Stars (XTR).

Stack: aiogram 3.x + Postgres (Supabase). Payments are Telegram Stars only
(currency="XTR", provider_token="").
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from html import escape
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    MenuButtonWebApp,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    TelegramObject,
    WebAppInfo,
)

import countries
from countries import COUNTRIES, flag
from countries import label as country_label
from common import title_for
from db import Database

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

log = logging.getLogger("worldrichlist")

# --------------------------------------------------------------------- config
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
MIN_STARS = int(os.getenv("MIN_STARS", "1"))
MAX_STARS = int(os.getenv("MAX_STARS", "10000"))
TOP_N = 15
PRESETS = (10, 50, 100, 500, 1000)
FOUNDING_LIMIT = int(os.getenv("FOUNDING_LIMIT", "50"))
PAGE_SIZE = 24  # countries per picker page

# Clearly-labelled sample row shown while the board is (nearly) empty.
# It is NOT counted in ranks and is always marked "not a real ...".
SHOW_SAMPLE_ROW = os.getenv("SHOW_SAMPLE_ROW", "1") == "1"
SAMPLE_STARS = int(os.getenv("SAMPLE_STARS", "100"))

# Mini App (served by this same process). Needs a public HTTPS URL.
_ext = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip() or (f"{_ext}/app" if _ext else "")

# "Your country was overtaken" notifications
NOTIFY_COOLDOWN = 600  # seconds per (overtaken, overtaker) pair
NOTIFY_LIMIT = 40  # max recipients per overtaken country

BTN_LB = "🏆 Global Leaderboard"
BTN_DONATE = "⭐ Donate Stars"
BTN_RANK = "👤 My Rank"
BTN_APP = "🚀 Open App"

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

db: Database  # initialised in main()
router = Router()
_notify_last: dict[tuple[str, str], float] = {}
_bg_tasks: set[asyncio.Task] = set()


class DonateStates(StatesGroup):
    waiting_custom_amount = State()
    choosing_country = State()


# ------------------------------------------------------------------ helpers
def short(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _person(row: dict[str, Any]) -> str:
    if row.get("username"):
        return "@" + escape(short(row["username"]))
    name = (row.get("full_name") or "").strip() or f"User {row['user_id']}"
    return escape(short(name))


def donor_name(row: dict[str, Any], viewer_id: int) -> str:
    """Flag + name as shown in donor lists (viewer always sees their own)."""
    prefix = f"{flag(row['country_code'])} " if row.get("country_code") else ""
    if row["user_id"] == viewer_id:
        hidden = " 🕶 <i>(you, hidden from others)</i>" if row.get("is_anonymous") else " <i>(you)</i>"
        return prefix + _person(row) + hidden
    if row.get("is_anonymous"):
        return prefix + "🕶 Anonymous"
    return prefix + _person(row)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


async def safe_edit(
    message: Any, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None
) -> None:
    if not isinstance(message, Message):
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def grid(buttons: list[InlineKeyboardButton], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + cols] for i in range(0, len(buttons), cols)]


def main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_LB)],
        [KeyboardButton(text=BTN_DONATE), KeyboardButton(text=BTN_RANK)],
    ]
    if WEBAPP_URL:
        rows.insert(0, [KeyboardButton(text=BTN_APP, web_app=WebAppInfo(url=WEBAPP_URL))])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Choose an option 👇",
    )


# -------------------------------------------------------------- middleware
class UserTrackerMiddleware(BaseMiddleware):
    """Keeps user_id / username / full_name fresh on every interaction."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        data["is_new_user"] = False
        if user is not None and not user.is_bot:
            data["is_new_user"] = await asyncio.to_thread(
                db.upsert_user, user.id, user.username, user.full_name
            )
        return await handler(event, data)


# --------------------------------------------------------------- leaderboard
def leaderboard_keyboard(view: str, period: str) -> InlineKeyboardMarkup:
    def tab(text: str, v: str, p: str, active: bool) -> InlineKeyboardButton:
        return btn(("• " if active else "") + text, f"lb:{v}:{p}")

    rows = [
        [
            tab("🌍 Countries", "c", period, view == "c"),
            tab("👤 Donors", "d", period, view == "d"),
        ]
    ]
    if view in ("c", "d"):
        rows.append(
            [
                tab("All time", view, "all", period == "all"),
                tab("This week", view, "week", period == "week"),
            ]
        )
    rows.append(
        [
            tab("🏳️ My country", "m", "all", view == "m"),
            btn("⭐ Donate", "menu:donate"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sample_lines(top_stars: int, noun: str) -> list[str]:
    if SHOW_SAMPLE_ROW and top_stars < SAMPLE_STARS:
        return [
            f"🎯 <i>Sample entry (not a real {noun}):</i> 🤖 Demo — {SAMPLE_STARS} ⭐",
            "<i>Beat it to take the #1 spot!</i>",
            "",
        ]
    return []


async def build_country_board(period: str, period_name: str, viewer_id: int) -> str:
    my_country = await asyncio.to_thread(db.get_country, viewer_id)
    rows, mine = await asyncio.to_thread(
        db.leaderboard_countries, period, TOP_N, my_country
    )
    lines = [f"🌍 <b>Country Ranking</b> — {period_name}", ""]
    lines += sample_lines(rows[0]["stars"] if rows else 0, "country")
    if not rows:
        lines.append("No country on the board yet — put yours first! 👑")
    else:
        for row in rows:
            pos = row["pos"]
            medal = MEDALS.get(pos, f"<b>{pos}.</b>")
            mark = "  ⬅️ <i>yours</i>" if row["country_code"] == my_country else ""
            lines.append(
                f"{medal} {country_label(row['country_code'])} — "
                f"<b>{row['stars']}</b> ⭐ · {plural(row['donors'], 'donor')}{mark}"
            )
    lines += ["", "━━━━━━━━━━━━━━"]
    if not my_country:
        lines.append("📍 Pick your country with your first donation and fight for it! 🏳️")
    elif mine:
        lines.append(
            f"📍 Your country: {country_label(my_country)} — "
            f"<b>#{mine['pos']}</b> · <b>{mine['stars']}</b> ⭐"
        )
    else:
        lines.append(
            f"📍 {country_label(my_country)} isn't on the board yet — be the first to donate!"
        )
    return "\n".join(lines)


async def build_donor_board(period: str, period_name: str, viewer_id: int) -> str:
    rows, me = await asyncio.to_thread(db.leaderboard_users, period, TOP_N, viewer_id)
    lines = [f"👤 <b>Top Donors</b> — {period_name}", ""]
    lines += sample_lines(rows[0]["stars"] if rows else 0, "donor")
    if not rows:
        lines.append("No donors yet — be the first on the list! 👑")
    else:
        for row in rows:
            medal = MEDALS.get(row["pos"], f"<b>{row['pos']}.</b>")
            lines.append(
                f"{medal} {donor_name(row, viewer_id)} — <b>{row['stars']}</b> ⭐"
            )
    lines += ["", "━━━━━━━━━━━━━━"]
    if me:
        lines.append(
            f"📍 Your rank: <b>#{me['pos']}</b> · Balance: <b>{me['stars']}</b> ⭐"
        )
    else:
        lines.append("📍 You're not on the board yet — donate to join!")
    return "\n".join(lines)


async def build_my_country_board(viewer_id: int) -> str:
    code = await asyncio.to_thread(db.get_country, viewer_id)
    if not code:
        return (
            "🏳️ <b>My country</b>\n\nYou haven't picked a country yet.\n"
            "Tap ⭐ <b>Donate</b> — you'll choose it with your first donation."
        )
    rows = await asyncio.to_thread(db.country_top_donors, code, 10)
    lines = [f"🏳️ <b>{country_label(code)}</b> — top donors", ""]
    if not rows:
        lines.append("Nobody has donated for this country yet. Be the first! 👑")
    else:
        for row in rows:
            medal = MEDALS.get(row["pos"], f"<b>{row['pos']}.</b>")
            lines.append(
                f"{medal} {donor_name(row, viewer_id)} — <b>{row['stars']}</b> ⭐"
            )
    return "\n".join(lines)


async def build_leaderboard(view: str, period: str, viewer_id: int) -> str:
    period_name = "All time" if period == "all" else "This week (last 7 days)"
    if view == "m":
        return await build_my_country_board(viewer_id)
    if view == "d":
        return await build_donor_board(period, period_name, viewer_id)
    return await build_country_board(period, period_name, viewer_id)


# -------------------------------------------------------------------- rank
def anon_label(is_anonymous: bool) -> str:
    return "🕶 Anonymous mode: ON" if is_anonymous else "👁 Anonymous mode: OFF"


async def build_rank(user_id: int, bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    s = await asyncio.to_thread(db.user_stats, user_id)
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"
    country = s["country"]

    lines = ["👤 <b>My Rank</b>", ""]
    lines.append(f"Title: <b>{title_for(s['total'])}</b>")
    if s["donor_no"] is not None and s["donor_no"] <= FOUNDING_LIMIT:
        lines.append(f"Badge: 🏅 <b>Founding Donor #{s['donor_no']}</b>")
    lines.append("")
    if country:
        lines.append(f"🏳️ Country: <b>{country_label(country)}</b>")
        if s["country_pos"] is not None:
            lines.append(
                f"🌍 Country rank: <b>#{s['country_pos']}</b> "
                f"({s['country_stars']} ⭐ total)"
            )
        else:
            lines.append("🌍 Country rank: <i>no donations yet</i>")
    else:
        lines.append("🏳️ Country: <i>not chosen yet (pick it with your first donation)</i>")
    lines.append(f"⭐ You donated: <b>{s['total']}</b>")
    if s["pos"] is not None:
        lines.append(f"👤 Personal rank: <b>#{s['pos']}</b>")
    else:
        lines.append("👤 Personal rank: <i>not ranked yet — donate to join!</i>")
    if s["week_pos"] is not None:
        lines.append(
            f"📅 This week: <b>{s['week_stars']}</b> ⭐ (rank <b>#{s['week_pos']}</b>)"
        )
    lines.append(f"👥 Friends invited: <b>{s['invited']}</b>")
    lines += ["", f"🔗 Your invite link:\n<code>{link}</code>"]

    rows: list[list[InlineKeyboardButton]] = [[btn("⭐ Donate", "menu:donate")]]
    if country:
        share_text = f"Help {country_label(country)} reach #1 on the Country Ranking! ⭐"
        share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(share_text)}"
        rows.append([InlineKeyboardButton(text="📣 Share my country", url=share_url)])
    rows.append([btn(anon_label(s["is_anonymous"]), "anon:rank")])
    if s["total"] == 0:
        rows.append([btn("🌍 Change country" if country else "🌍 Choose country", "country:pick")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------------------------------------------------------ donate
async def build_donate(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    anon, country = await asyncio.gather(
        asyncio.to_thread(db.is_anonymous, user_id),
        asyncio.to_thread(db.get_country, user_id),
    )
    if country:
        where = f"Donating for: <b>{country_label(country)}</b>"
    else:
        where = (
            "You'll choose <b>your country</b> with your first donation "
            "(it's locked afterwards)."
        )
    text = (
        "⭐ <b>Donate Stars</b>\n\n"
        f"{where}\n\n"
        "Pick an amount and push your country up the ranking!\n"
        "Prefer privacy? Turn on <b>Anonymous mode</b> — others see 🕶 Anonymous "
        "(your country still counts)."
    )
    rows = grid([btn(f"{a} ⭐", f"don:{a}") for a in PRESETS], 2)
    rows.append([btn("✏️ Custom Amount", "don:custom")])
    rows.append([btn(anon_label(anon), "anon:donate")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def send_invoice(
    bot: Bot, chat_id: int, user_id: int, amount: int, country: str
) -> None:
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Donate {amount} ⭐",
        description=(
            f"{amount} Telegram Stars for {country_label(country)} on the "
            f"Global Leaderboard! Donations are non-refundable: they buy you a "
            f"rank and badges."
        ),
        payload=f"donate:{user_id}:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
        provider_token="",
    )


# ---------------------------------------------------------- country picker
COUNTRY_PROMPT = (
    "🌍 <b>Choose your country</b>\n\n"
    "Your donations will count for it on the Country Ranking.\n"
    "⚠️ It is <b>locked after your first donation</b>.\n\n"
    "Pick below, or just <b>type a country name</b> to search."
)


def country_kb(codes: list[str], extra: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows = grid(
        [btn(country_label(c), f"cc:{c}") for c in codes], 2
    )
    rows.extend(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def popular_kb() -> InlineKeyboardMarkup:
    return country_kb(list(countries.POPULAR), [[btn("🔤 All countries (A–Z)", "cp:0")]])


def page_kb(page: int) -> InlineKeyboardMarkup:
    pages = (len(countries.SORTED_CODES) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    codes = countries.SORTED_CODES[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    nav = [
        btn("◀️", f"cp:{page - 1}") if page > 0 else btn("·", "noop"),
        btn(f"{page + 1}/{pages}", "noop"),
        btn("▶️", f"cp:{page + 1}") if page < pages - 1 else btn("·", "noop"),
    ]
    return country_kb(codes, [nav, [btn("⭐ Popular", "cpop")]])


async def begin_donation(
    bot: Bot, chat_id: int, user_id: int, amount: int, state: FSMContext
) -> None:
    """Invoice right away if the country is known, otherwise pick it first."""
    country = await asyncio.to_thread(db.get_country, user_id)
    if country is None:
        await state.set_state(DonateStates.choosing_country)
        await state.update_data(pending_amount=amount)
        await bot.send_message(chat_id, COUNTRY_PROMPT, reply_markup=popular_kb())
        return
    await send_invoice(bot, chat_id, user_id, amount, country)


# ================================================================== handlers
@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    is_new_user: bool = False,
) -> None:
    await state.clear()
    user = message.from_user
    assert user is not None

    # Referral: /start ref_<user_id>, only credited for brand-new users.
    if is_new_user and command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args[4:])
        except ValueError:
            referrer_id = 0
        if referrer_id and await asyncio.to_thread(
            db.set_referrer, user.id, referrer_id
        ):
            try:
                await message.bot.send_message(
                    referrer_id,
                    "🎉 Someone just joined WorldRichListBot with your invite link!",
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                pass

    await message.answer(
        f"👋 Welcome, <b>{escape(user.first_name or 'friend')}</b>!\n\n"
        "🌍 <b>WorldRichListBot</b> — which country is the richest in Telegram "
        "Stars? Donate for <b>your country</b> and push it to #1!\n\n"
        "🏆 Country Ranking & Top Donors\n"
        "⭐ Donate Stars for your country\n"
        "👤 Track your rank, title and badges\n\n"
        "🚀 Tap <b>Open App</b> for the full experience, or use the buttons below 👇",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Help</b>\n\n"
        "• 🏆 <b>Global Leaderboard</b> — Country Ranking and Top Donors "
        "(all time / this week)\n"
        "• ⭐ <b>Donate Stars</b> — pick an amount or enter your own\n"
        "• 👤 <b>My Rank</b> — your stats, title, invite link\n\n"
        "Your country is chosen with your first donation and then locked.\n"
        "Payments use Telegram Stars only. Payment problems? Send /paysupport.",
        reply_markup=main_menu(),
    )


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    if not WEBAPP_URL:
        await message.answer("The app is not available right now.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Open App", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await message.answer(
        "🌍 Open the app to see the Country Ranking and donate for your country.",
        reply_markup=kb,
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(
        "💬 <b>Payment support</b>\n\n"
        "Donations are non-refundable: they buy you a rank and badges.\n\n"
        "The only exceptions are errors, for example a payment charged twice or "
        "not credited. In that case describe the problem and include the time "
        "of the payment. We'll review it as soon as possible."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.", reply_markup=main_menu())


@router.message(F.text == BTN_LB)
async def on_leaderboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = await build_leaderboard("c", "all", message.from_user.id)
    await message.answer(text, reply_markup=leaderboard_keyboard("c", "all"))


@router.message(F.text == BTN_DONATE)
async def on_donate(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await build_donate(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(F.text == BTN_RANK)
async def on_rank(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await build_rank(message.from_user.id, message.bot)
    await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------- callbacks
@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("lb:"))
async def cb_leaderboard(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    view = parts[1] if len(parts) > 1 and parts[1] in ("c", "d", "m") else "c"
    period = "week" if len(parts) > 2 and parts[2] == "week" else "all"
    text = await build_leaderboard(view, period, callback.from_user.id)
    await safe_edit(callback.message, text, leaderboard_keyboard(view, period))
    await callback.answer()


@router.callback_query(F.data == "menu:donate")
async def cb_menu_donate(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await build_donate(callback.from_user.id)
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("anon:"))
async def cb_anonymous(callback: CallbackQuery) -> None:
    new_value = await asyncio.to_thread(db.toggle_anonymous, callback.from_user.id)
    if callback.data == "anon:rank":
        text, kb = await build_rank(callback.from_user.id, callback.bot)
    else:
        text, kb = await build_donate(callback.from_user.id)
    await safe_edit(callback.message, text, kb)
    await callback.answer(
        "Anonymous mode is ON 🕶" if new_value else "Anonymous mode is OFF 👁"
    )


@router.callback_query(F.data.startswith("don:"))
async def cb_donate(callback: CallbackQuery, state: FSMContext) -> None:
    arg = (callback.data or "").split(":", 1)[1]
    if arg == "custom":
        await state.set_state(DonateStates.waiting_custom_amount)
        await callback.bot.send_message(
            callback.from_user.id,
            f"✏️ Send the amount of Stars you'd like to donate "
            f"(<b>{MIN_STARS}–{MAX_STARS}</b>).\n\nSend /cancel to go back.",
        )
        await callback.answer()
        return

    try:
        amount = int(arg)
    except ValueError:
        await callback.answer("Invalid amount.", show_alert=True)
        return
    if not (MIN_STARS <= amount <= MAX_STARS):
        await callback.answer("Invalid amount.", show_alert=True)
        return

    await state.clear()
    await begin_donation(
        callback.bot, callback.from_user.id, callback.from_user.id, amount, state
    )
    await callback.answer()


@router.callback_query(F.data == "country:pick")
async def cb_country_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if await asyncio.to_thread(db.has_donated, callback.from_user.id):
        await callback.answer(
            "Your country is locked after your first donation.", show_alert=True
        )
        return
    await state.set_state(DonateStates.choosing_country)
    await state.update_data(pending_amount=None)
    await callback.bot.send_message(
        callback.from_user.id, COUNTRY_PROMPT, reply_markup=popular_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp:"))
async def cb_country_page(callback: CallbackQuery) -> None:
    try:
        page = int((callback.data or "cp:0").split(":", 1)[1])
    except ValueError:
        page = 0
    await safe_edit(callback.message, COUNTRY_PROMPT, page_kb(page))
    await callback.answer()


@router.callback_query(F.data == "cpop")
async def cb_country_popular(callback: CallbackQuery) -> None:
    await safe_edit(callback.message, COUNTRY_PROMPT, popular_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("cc:"))
async def cb_country_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "cc:").split(":", 1)[1].upper()
    if code not in COUNTRIES:
        await callback.answer("Unknown country.", show_alert=True)
        return
    user_id = callback.from_user.id
    if not await asyncio.to_thread(db.set_country, user_id, code):
        await callback.answer(
            "Your country is locked after your first donation.", show_alert=True
        )
        return

    data = await state.get_data()
    pending = data.get("pending_amount")
    await state.clear()
    await safe_edit(callback.message, f"✅ Country set: <b>{country_label(code)}</b>")
    await callback.answer()
    if pending:
        await send_invoice(callback.bot, user_id, user_id, int(pending), code)


# ------------------------------------------------- FSM text input handlers
@router.message(DonateStates.choosing_country, F.text)
async def on_country_search(message: Message) -> None:
    found = countries.search(message.text or "")
    if not found:
        await message.answer(
            "No country found. Try another spelling, or pick from the list:",
            reply_markup=popular_kb(),
        )
        return
    await message.answer("Pick your country:", reply_markup=country_kb(found, []))


@router.message(DonateStates.waiting_custom_amount, F.text)
async def on_custom_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit():
        await message.answer(
            f"Please send a whole number between {MIN_STARS} and {MAX_STARS}, "
            f"or /cancel."
        )
        return
    amount = int(raw)
    if not (MIN_STARS <= amount <= MAX_STARS):
        await message.answer(
            f"Amount must be between {MIN_STARS} and {MAX_STARS} ⭐. Try again or /cancel."
        )
        return
    await state.clear()
    await begin_donation(
        message.bot, message.chat.id, message.from_user.id, amount, state
    )


# ------------------------------------------------------------------ payments
@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Must be answered within 10 seconds."""
    error = "Invalid payment request. Please try again."
    ok = False
    try:
        kind, uid, amt = query.invoice_payload.split(":")
        ok = (
            kind == "donate"
            and query.currency == "XTR"
            and int(uid) == query.from_user.id
            and int(amt) == query.total_amount
            and MIN_STARS <= query.total_amount <= MAX_STARS
        )
    except ValueError:
        ok = False

    if ok and not await asyncio.to_thread(db.get_country, query.from_user.id):
        ok = False
        error = "Please choose your country first: open the bot and tap ⭐ Donate Stars."

    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=error)


async def notify_passed(
    bot: Bot, passer: str, passed: list[str], exclude_user: int
) -> None:
    """Tell donors of overtaken countries that they were passed."""
    now = time.monotonic()
    codes = [
        c for c in passed[:3] if now - _notify_last.get((c, passer), 0.0) > NOTIFY_COOLDOWN
    ]
    if not codes:
        return
    for c in codes:
        _notify_last[(c, passer)] = now
    recipients = await asyncio.to_thread(
        db.donors_of_countries, codes, exclude_user, NOTIFY_LIMIT
    )
    for r in recipients:
        text = (
            f"⚠️ {country_label(passer)} just overtook "
            f"{country_label(r['country_code'])} on the Country Ranking!\n\n"
            f"Take the lead back 👉 tap ⭐ <b>Donate Stars</b>"
        )
        try:
            await bot.send_message(r["user_id"], text)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        await asyncio.sleep(0.05)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    user = message.from_user
    if payment is None or user is None or payment.currency != "XTR":
        return

    try:
        result = await asyncio.to_thread(
            db.record_payment,
            user.id,
            payment.total_amount,
            payment.telegram_payment_charge_id,
        )
    except Exception:
        # Money was taken but the DB write failed: log everything needed to
        # credit it manually / refund it.
        log.exception(
            "PAYMENT NOT RECORDED user=%s amount=%s charge_id=%s",
            user.id,
            payment.total_amount,
            payment.telegram_payment_charge_id,
        )
        await message.answer(
            "✅ Your payment was received, but we couldn't update the leaderboard "
            "right now. It will be credited shortly — thank you!"
        )
        return

    if result is None:
        log.warning("Duplicate payment ignored: %s", payment.telegram_payment_charge_id)
        return

    country = result["country"]
    lines = [f"🎉 <b>Thank you for donating {payment.total_amount} ⭐!</b>", ""]

    if country:
        o, n = result["old_country_pos"], result["new_country_pos"]
        c = country_label(country)
        if o is None:
            lines.append(f"🌍 {c} entered the Country Ranking at <b>#{n}</b>!")
        elif n < o:
            lines.append(f"🚀 {c} climbed from <b>#{o}</b> to <b>#{n}</b>!")
        else:
            lines.append(f"💪 {c} holds <b>#{n}</b>.")
        if n == 1:
            lines.append(f"👑 <b>{c} is #1 in the world right now!</b>")

    old_pos, new_pos = result["old_pos"], result["new_pos"]
    if old_pos is None:
        lines.append(f"🏆 You entered the Top Donors at <b>#{new_pos}</b>!")
    elif new_pos < old_pos:
        lines.append(f"📈 You climbed from <b>#{old_pos}</b> to <b>#{new_pos}</b> in Top Donors!")
    else:
        lines.append(f"💪 You're holding <b>#{new_pos}</b> in Top Donors.")

    lines += [
        "",
        f"⭐ You donated in total: <b>{result['new_total']}</b>",
        f"Title: <b>{title_for(result['new_total'])}</b>",
    ]
    if result["is_first_donation"] and result["donor_no"] <= FOUNDING_LIMIT:
        lines.append(
            f"🏅 You earned the <b>Founding Donor #{result['donor_no']}</b> badge!"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu())

    if country and result["passed_countries"]:
        task = asyncio.create_task(
            notify_passed(message.bot, country, result["passed_countries"], user.id)
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


# ------------------------------------------------------------------ fallback
@router.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer("Please choose an option below 👇", reply_markup=main_menu())


# ----------------------------------------------------- hosting helpers (Render)
async def start_web_server(bot: Bot) -> Optional[Any]:
    """Serve the Mini App + API (and a health check) on $PORT.

    Only started when PORT is set (Render) or a WEBAPP_URL is configured.
    """
    port = os.getenv("PORT") or ("8080" if WEBAPP_URL else "")
    if not port:
        return None
    from aiohttp import web  # installed together with aiogram

    import webapp_server

    me = await bot.me()
    app = webapp_server.create_app(
        bot,
        db,
        {
            "token": BOT_TOKEN,
            "bot_username": me.username,
            "presets": list(PRESETS),
            "min_stars": MIN_STARS,
            "max_stars": MAX_STARS,
            "show_sample": SHOW_SAMPLE_ROW,
            "sample_stars": SAMPLE_STARS,
            "miniapp_link": os.getenv("MINIAPP_LINK", "").strip().rstrip("/"),
        },
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    log.info("Web server listening on :%s (Mini App at %s)", port, WEBAPP_URL or "/app")
    return runner


async def keepalive_loop() -> None:
    """Ping our own public URL so free hosts don't put the service to sleep."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    import aiohttp

    await asyncio.sleep(60)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    url + "/health", timeout=aiohttp.ClientTimeout(total=15)
                ):
                    pass
            except Exception as exc:  # network hiccup: just try again later
                log.warning("keepalive ping failed: %s", exc)
            await asyncio.sleep(600)


async def weekly_winner_loop(bot: Bot) -> None:
    """Every Monday (UTC) tell the winning country's people they won last week."""
    await asyncio.sleep(45)
    while True:
        try:
            week = await asyncio.to_thread(db.current_week_key)
            if await asyncio.to_thread(db.get_meta, "weekly_announced") != week:
                b = await asyncio.to_thread(db.battle)
                await asyncio.to_thread(db.set_meta, "weekly_announced", week)
                if b["last"]:
                    win = b["last"][0]
                    code = win["place"]
                    text = (
                        f"🏆 <b>{country_label(code)}</b> won last week's battle with "
                        f"<b>{win['value']} ⭐</b>!\n\nA new week just started. "
                        f"Defend the title and invite friends."
                    )
                    kb = None
                    if WEBAPP_URL:
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="🚀 Open the app", web_app=WebAppInfo(url=WEBAPP_URL))
                        ]])
                    for uid in await asyncio.to_thread(db.audience_of_country, code, 500):
                        try:
                            await bot.send_message(uid, text, reply_markup=kb)
                        except Exception:
                            pass
                        await asyncio.sleep(0.05)
        except Exception:
            log.exception("weekly winner loop failed")
        await asyncio.sleep(1800)


# ---------------------------------------------------------------------- main
async def main() -> None:
    global db
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is missing. Put it in the .env file.")
    if not DATABASE_URL:
        raise SystemExit(
            "DATABASE_URL is missing. Use the Supabase Session pooler connection string."
        )

    db = await asyncio.to_thread(Database, DATABASE_URL)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserTrackerMiddleware())
    dp.include_router(router)

    me = await bot.me()
    log.info("Starting @%s", me.username)
    await bot.delete_webhook(drop_pending_updates=False)
    web_runner = await start_web_server(bot)
    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🚀 Open", web_app=WebAppInfo(url=WEBAPP_URL))
            )
        except Exception:
            log.exception("Could not set the menu button")
    keepalive = asyncio.create_task(keepalive_loop())
    weekly = asyncio.create_task(weekly_winner_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        keepalive.cancel()
        weekly.cancel()
        if web_runner is not None:
            await web_runner.cleanup()
        await bot.session.close()
        await asyncio.to_thread(db.close)


if __name__ == "__main__":
    asyncio.run(main())
