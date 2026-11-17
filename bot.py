"""WorldRichListBot - global leaderboard of Telegram Stars (XTR) donors.

Stack: aiogram 3.x + SQLite. Payments are Telegram Stars only
(currency="XTR", provider_token="").
"""
from __future__ import annotations

import asyncio
import logging
import os
from html import escape
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import Database

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

log = logging.getLogger("worldrichlist")

# --------------------------------------------------------------------- config
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "worldrichlist.db")
MIN_STARS = int(os.getenv("MIN_STARS", "1"))
MAX_STARS = int(os.getenv("MAX_STARS", "10000"))
TOP_N = 15
PRESETS = (10, 50, 100, 500, 1000)
FOUNDING_LIMIT = int(os.getenv("FOUNDING_LIMIT", "50"))

# Clearly-labelled sample row shown while the board is (nearly) empty.
# It is NOT counted in ranks and is always marked "not a real donor".
SHOW_SAMPLE_ROW = os.getenv("SHOW_SAMPLE_ROW", "1") == "1"
SAMPLE_STARS = int(os.getenv("SAMPLE_STARS", "100"))

BTN_LB = "🏆 Global Leaderboard"
BTN_DONATE = "⭐ Donate Stars"
BTN_RANK = "👤 My Rank"
MENU_BUTTONS = {BTN_LB, BTN_DONATE, BTN_RANK}

TIERS = (
    (10000, "💎 Legend"),
    (2500, "🐋 Whale"),
    (1000, "🦈 Shark"),
    (250, "🐬 Dolphin"),
    (50, "🐟 Fish"),
    (1, "🦐 Shrimp"),
)
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

db: Database  # initialised in main()
router = Router()


class DonateStates(StatesGroup):
    waiting_custom_amount = State()


# ------------------------------------------------------------------ helpers
def title_for(total: int) -> str:
    for threshold, name in TIERS:
        if total >= threshold:
            return name
    return "🌱 Newcomer"


def short(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def public_name(row: dict[str, Any]) -> str:
    """Name shown to everyone else."""
    if row.get("is_anonymous"):
        return "🕶 Anonymous"
    if row.get("username"):
        return "@" + escape(short(row["username"]))
    name = (row.get("full_name") or "").strip() or f"User {row['user_id']}"
    return escape(short(name))


def own_name(row: dict[str, Any]) -> str:
    """Name the viewer sees for their own row (even if anonymous)."""
    if row.get("username"):
        base = "@" + escape(short(row["username"]))
    else:
        base = escape(
            short((row.get("full_name") or "").strip() or f"User {row['user_id']}")
        )
    suffix = " 🕶 <i>(you, hidden from others)</i>" if row.get("is_anonymous") else " <i>(you)</i>"
    return base + suffix


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


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LB)],
            [KeyboardButton(text=BTN_DONATE), KeyboardButton(text=BTN_RANK)],
        ],
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
def leaderboard_keyboard(period: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=("• " if period == "all" else "") + "All time",
        callback_data="lb:all",
    )
    b.button(
        text=("• " if period == "week" else "") + "This week",
        callback_data="lb:week",
    )
    b.button(text="⭐ Donate", callback_data="menu:donate")
    b.adjust(2, 1)
    return b.as_markup()


async def build_leaderboard(period: str, viewer_id: int) -> str:
    rows, me = await asyncio.to_thread(db.leaderboard, period, TOP_N, viewer_id)
    heading = "All time" if period == "all" else "This week (last 7 days)"
    lines = [f"🏆 <b>Global Leaderboard</b> — {heading}", ""]

    top_stars = rows[0]["stars"] if rows else 0
    if SHOW_SAMPLE_ROW and top_stars < SAMPLE_STARS:
        lines.append(
            f"🎯 <i>Sample entry (not a real donor):</i> 🤖 Demo Player — "
            f"{SAMPLE_STARS} ⭐"
        )
        lines.append("<i>Beat it to take the #1 spot!</i>")
        lines.append("")

    if not rows:
        lines.append("No donors yet — be the first on the list! 👑")
    else:
        for row in rows:
            pos = row["pos"]
            medal = MEDALS.get(pos, f"<b>{pos}.</b>")
            name = own_name(row) if row["user_id"] == viewer_id else public_name(row)
            lines.append(f"{medal} {name} — <b>{row['stars']}</b> ⭐")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    if me:
        lines.append(f"📍 Your rank: <b>#{me['pos']}</b> · Balance: <b>{me['stars']}</b> ⭐")
    else:
        lines.append("📍 You're not on the board yet — donate to join!")
    return "\n".join(lines)


# -------------------------------------------------------------------- rank
def rank_keyboard(is_anonymous: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⭐ Donate", callback_data="menu:donate")
    b.button(text=anon_label(is_anonymous), callback_data="anon:rank")
    b.adjust(1)
    return b.as_markup()


def anon_label(is_anonymous: bool) -> str:
    return "🕶 Anonymous mode: ON" if is_anonymous else "👁 Anonymous mode: OFF"


async def build_rank(user_id: int, bot: Bot) -> tuple[str, bool]:
    s = await asyncio.to_thread(db.user_stats, user_id)
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"

    lines = ["👤 <b>My Rank</b>", ""]
    lines.append(f"Title: <b>{title_for(s['total'])}</b>")
    if s["donor_no"] is not None and s["donor_no"] <= FOUNDING_LIMIT:
        lines.append(f"Badge: 🏅 <b>Founding Donor #{s['donor_no']}</b>")
    lines.append("")
    lines.append(f"⭐ Total donated: <b>{s['total']}</b>")
    if s["pos"] is not None:
        lines.append(f"🌍 Global rank: <b>#{s['pos']}</b>")
    else:
        lines.append("🌍 Global rank: <i>not ranked yet — donate to join!</i>")
    if s["week_pos"] is not None:
        lines.append(
            f"📅 This week: <b>{s['week_stars']}</b> ⭐ (rank <b>#{s['week_pos']}</b>)"
        )
    lines.append(f"👥 Friends invited: <b>{s['invited']}</b>")
    lines.append("")
    lines.append(f"🔗 Your invite link:\n<code>{link}</code>")
    return "\n".join(lines), s["is_anonymous"]


# ------------------------------------------------------------------ donate
def donate_keyboard(is_anonymous: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for amount in PRESETS:
        b.button(text=f"{amount} ⭐", callback_data=f"don:{amount}")
    b.button(text="✏️ Custom Amount", callback_data="don:custom")
    b.button(text=anon_label(is_anonymous), callback_data="anon:donate")
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


DONATE_TEXT = (
    "⭐ <b>Donate Stars</b>\n\n"
    "Pick an amount and climb the Global Leaderboard!\n"
    "Prefer to stay private? Turn on <b>Anonymous mode</b> — everyone else "
    "sees you as 🕶 Anonymous, only you see your own name."
)


async def send_invoice(bot: Bot, chat_id: int, user_id: int, amount: int) -> None:
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Donate {amount} ⭐",
        description=(
            f"Support WorldRichListBot with {amount} Telegram Stars "
            f"and climb the Global Leaderboard!"
        ),
        payload=f"donate:{user_id}:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
        provider_token="",
    )


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
        "🌍 <b>WorldRichListBot</b> ranks the world's biggest Telegram Stars "
        "donors.\n\n"
        "🏆 See who tops the Global Leaderboard\n"
        "⭐ Donate Stars and climb the ranks\n"
        "👤 Track your own rank, title and badges\n\n"
        "Choose an option below 👇",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Help</b>\n\n"
        "• 🏆 <b>Global Leaderboard</b> — Top 15 donors (all time / this week)\n"
        "• ⭐ <b>Donate Stars</b> — pick an amount or enter your own\n"
        "• 👤 <b>My Rank</b> — your stats, title and invite link\n\n"
        "Payments use Telegram Stars only. Payment problems? Send /paysupport.",
        reply_markup=main_menu(),
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    await message.answer(
        "💬 <b>Payment support</b>\n\n"
        "If a payment didn't go through or was not credited, please describe the "
        "problem and include the time of the payment. We'll review it as soon "
        "as possible."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.", reply_markup=main_menu())


@router.message(F.text == BTN_LB)
async def on_leaderboard(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = await build_leaderboard("all", message.from_user.id)
    await message.answer(text, reply_markup=leaderboard_keyboard("all"))


@router.message(F.text == BTN_DONATE)
async def on_donate(message: Message, state: FSMContext) -> None:
    await state.clear()
    anon = await asyncio.to_thread(db.is_anonymous, message.from_user.id)
    await message.answer(DONATE_TEXT, reply_markup=donate_keyboard(anon))


@router.message(F.text == BTN_RANK)
async def on_rank(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, anon = await build_rank(message.from_user.id, message.bot)
    await message.answer(text, reply_markup=rank_keyboard(anon))


# ---------------------------------------------------------------- callbacks
@router.callback_query(F.data.startswith("lb:"))
async def cb_leaderboard(callback: CallbackQuery) -> None:
    period = "week" if callback.data == "lb:week" else "all"
    text = await build_leaderboard(period, callback.from_user.id)
    await safe_edit(callback.message, text, leaderboard_keyboard(period))
    await callback.answer()


@router.callback_query(F.data == "menu:donate")
async def cb_menu_donate(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    anon = await asyncio.to_thread(db.is_anonymous, callback.from_user.id)
    await callback.bot.send_message(
        callback.from_user.id, DONATE_TEXT, reply_markup=donate_keyboard(anon)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("anon:"))
async def cb_anonymous(callback: CallbackQuery) -> None:
    new_value = await asyncio.to_thread(db.toggle_anonymous, callback.from_user.id)
    if callback.data == "anon:rank":
        text, anon = await build_rank(callback.from_user.id, callback.bot)
        await safe_edit(callback.message, text, rank_keyboard(anon))
    else:
        await safe_edit(callback.message, DONATE_TEXT, donate_keyboard(new_value))
    await callback.answer(
        "Anonymous mode is ON 🕶" if new_value else "Anonymous mode is OFF 👁"
    )


@router.callback_query(F.data.startswith("don:"))
async def cb_donate(callback: CallbackQuery, state: FSMContext) -> None:
    arg = callback.data.split(":", 1)[1]
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

    await send_invoice(callback.bot, callback.from_user.id, callback.from_user.id, amount)
    await callback.answer()


# ------------------------------------------------- custom amount (FSM state)
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
    await send_invoice(message.bot, message.chat.id, message.from_user.id, amount)


# ------------------------------------------------------------------ payments
@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """Must be answered within 10 seconds."""
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

    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(
            ok=False, error_message="Invalid payment request. Please try again."
        )


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

    old_pos, new_pos = result["old_pos"], result["new_pos"]
    lines = [f"🎉 <b>Thank you for donating {payment.total_amount} ⭐!</b>", ""]
    if old_pos is None:
        lines.append(f"🚀 You've entered the Global Leaderboard at <b>#{new_pos}</b>!")
    elif new_pos < old_pos:
        lines.append(
            f"🚀 You climbed from <b>#{old_pos}</b> to <b>#{new_pos}</b> on the "
            f"Global Leaderboard!"
        )
    else:
        lines.append(f"💪 You're holding your spot at <b>#{new_pos}</b>.")
    if new_pos == 1:
        lines.append("👑 <b>You're the #1 donor in the world right now!</b>")
    lines.append("")
    lines.append(
        f"⭐ Total donated: <b>{result['new_total']}</b>\n"
        f"Title: <b>{title_for(result['new_total'])}</b>"
    )
    if result["is_first_donation"] and result["donor_no"] <= FOUNDING_LIMIT:
        lines.append(
            f"🏅 You earned the <b>Founding Donor #{result['donor_no']}</b> badge!"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu())


# ------------------------------------------------------------------ fallback
@router.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer("Please choose an option below 👇", reply_markup=main_menu())


# ---------------------------------------------------------------------- main
async def main() -> None:
    global db
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is missing. Put it in the .env file.")

    db = Database(DB_PATH)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserTrackerMiddleware())
    dp.include_router(router)

    me = await bot.me()
    log.info("Starting @%s", me.username)
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
