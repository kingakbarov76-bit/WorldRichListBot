# WorldRichListBot

Country vs Country leaderboard of Telegram Stars (XTR) donations. aiogram 3.x + Postgres (Supabase).

## Ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # BOT_TOKEN va DATABASE_URL ni to'ldiring
python bot.py
```

`DATABASE_URL` - Supabase -> Connect -> **Session pooler** ulanish satri (IPv4 bilan ishlaydi).
Jadvallar `worldrichlist` schema ichida (users, payments); ular `migrations/` ichidagi SQL bilan yaratiladi.

## Mini App

Bot ichida `🚀 Open App` tugmasi (va chat menyusidagi tugma) Telegram Mini App'ni ochadi: podium, davlatlar reytingi, donatchilar, davlat tanlash va Stars bilan to'lov.
Mini App shu jarayonning o'zida xizmat qiladi: `/app` sahifa, `/api/*` — API (har bir so'rov Telegram `initData` imzosi bilan tekshiriladi).
`WEBAPP_URL` (yoki Render'dagi `RENDER_EXTERNAL_URL`) ochiq HTTPS manzil bo'lishi kerak. Dizaynni brauzerda ko'rish: `/app?demo=1`.

## Imkoniyatlar

- 🌍 Country Ranking: davlatlar reytingi (All time / This week), "davlat o'tib ketdi" xabarlari
- 👤 Top Donors: shaxsiy reyting; 🏳️ My country: o'z davlatingizning top donatchilari
- ⭐ Donate Stars: 10 / 50 / 100 / 500 / 1000 va Custom Amount; davlat birinchi donatda tanlanadi va keyin qulflanadi
- 👤 My Rank: o'rin, unvon, davlat o'rni, referal havola, "Share my country"
- 🕶 Anonymous mode (davlat baribir hisoblanadi), 🏅 Founding Donor, unvonlar
- To'lovlar faqat Stars: `currency="XTR"`, `provider_token=""`; dublikat to'lov ikki marta hisoblanmaydi
- Davlatlar: BMT a'zolari (193 ta), bahsli hududlar kiritilmagan (`countries.py`)

## Sozlamalar (.env)

| Kalit | Ma'nosi | Default |
|---|---|---|
| `BOT_TOKEN` | BotFather tokeni | - |
| `DATABASE_URL` | Supabase Session pooler | - |
| `MIN_STARS` / `MAX_STARS` | Custom Amount chegaralari | 1 / 10000 |
| `SHOW_SAMPLE_ROW` / `SAMPLE_STARS` | Bo'sh reytingda belgilangan namuna qator | 1 / 100 |
| `FOUNDING_LIMIT` | Founding Donor soni | 50 |

## Fayllar

- `webapp/index.html` - Mini App (bitta fayl)
- `webapp_server.py`, `webapp_logic.py`, `tg_auth.py` - Mini App API va imzo tekshiruvi
- `bot.py` - handlerlar, to'lovlar (PreCheckoutQuery, SuccessfulPayment)
- `db.py`, `sql_queries.py` - Postgres qatlami va reyting so'rovlari
- `countries.py` - davlatlar ro'yxati
- `migrations/001_initial_schema.sql` - baza sxemasi
