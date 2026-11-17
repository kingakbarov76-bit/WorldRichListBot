# WorldRichListBot

Telegram Stars (XTR) donor leaderboard bot. aiogram 3.x + SQLite.

## Ishga tushirish

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Token `.env` faylida (`BOT_TOKEN=...`). Namuna: `.env.example`.

## Imkoniyatlar

- 🏆 Global Leaderboard: Top-15 (All time / This week) + sizning o'rningiz va balansingiz
- ⭐ Donate Stars: 10 / 50 / 100 / 500 / 1000 va Custom Amount
- 👤 My Rank: o'rin, unvon, haftalik natija, taklif qilingan do'stlar, referal havola
- 🕶 Anonymous mode: boshqalar "Anonymous" ko'radi, siz o'z ismingizni ko'rasiz
- 🏅 Founding Donor (birinchi 50 donatchi), unvonlar: Shrimp → Legend
- Referal: `/start ref_<id>`
- To'lovlar faqat Stars: `currency="XTR"`, `provider_token=""`
- Bir xil to'lov ikki marta hisoblanmaydi (`telegram_payment_charge_id` unikal)
- `/paysupport` buyrug'i bor (Telegram to'lov qabul qiladigan botlardan talab qiladi)

## Sozlamalar (.env)

| Kalit | Ma'nosi | Default |
|---|---|---|
| `MIN_STARS` / `MAX_STARS` | Custom Amount chegaralari | 1 / 10000 |
| `SHOW_SAMPLE_ROW` | Bo'sh reytingda belgilangan namuna qator (1/0) | 1 |
| `SAMPLE_STARS` | Namuna qator miqdori | 100 |
| `FOUNDING_LIMIT` | Founding Donor soni | 50 |

Namuna qator har doim "sample entry (not a real donor)" deb belgilanadi, o'rinlarga
hisoblanmaydi va real donatchi shu miqdordan oshganda yo'qoladi.

## Fayllar

- `bot.py` — handlerlar, to'lovlar (PreCheckoutQuery, SuccessfulPayment)
- `db.py` — SQLite (`users`, `payments` jadvallari)
