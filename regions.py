"""First-level administrative regions (viloyat / oblast / province) per country.

Only countries listed here get a Region ranking; everyone else simply has
World + Country rankings. Keys look like "UZ-SAMARKAND" and must stay stable
(they are stored in the database), so only ever ADD entries, never rename keys.

Cities that are not their own region (e.g. Samarkand city inside Samarkand
Region) are intentionally not ranked: free-text cities would create duplicates
and moderation problems.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def _slug(name: str) -> str:
    base = unicodedata.normalize("NFKD", name)
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.replace("ı", "i").replace("İ", "I").replace("ğ", "g").replace("Ğ", "G")
    return re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").upper()


def _build(cc: str, names: list[str]) -> list[tuple[str, str]]:
    return [(f"{cc}-{_slug(n)}", n) for n in names]


_RAW: dict[str, list[str]] = {
    "UZ": [
        "Karakalpakstan", "Andijan", "Bukhara", "Fergana", "Jizzakh",
        "Kashkadarya", "Khorezm", "Namangan", "Navoi", "Samarkand",
        "Surkhandarya", "Syrdarya", "Tashkent Region", "Tashkent City",
    ],
    "KZ": [
        "Astana", "Almaty", "Shymkent", "Abai", "Akmola", "Aktobe",
        "Almaty Region", "Atyrau", "East Kazakhstan", "Jambyl", "Jetisu",
        "Karaganda", "Kostanay", "Kyzylorda", "Mangystau",
        "North Kazakhstan", "Pavlodar", "Turkistan", "Ulytau",
        "West Kazakhstan",
    ],
    "KG": [
        "Bishkek", "Osh City", "Batken", "Chüy", "Issyk-Kul", "Jalal-Abad",
        "Naryn", "Osh Region", "Talas",
    ],
    "TJ": [
        "Dushanbe", "Sughd", "Khatlon", "Gorno-Badakhshan",
        "Districts of Republican Subordination",
    ],
    "TM": ["Ashgabat", "Ahal", "Balkan", "Daşoguz", "Lebap", "Mary"],
    "TR": [
        "Adana", "Adıyaman", "Afyonkarahisar", "Ağrı", "Aksaray", "Amasya",
        "Ankara", "Antalya", "Ardahan", "Artvin", "Aydın", "Balıkesir",
        "Bartın", "Batman", "Bayburt", "Bilecik", "Bingöl", "Bitlis", "Bolu",
        "Burdur", "Bursa", "Çanakkale", "Çankırı", "Çorum", "Denizli",
        "Diyarbakır", "Düzce", "Edirne", "Elazığ", "Erzincan", "Erzurum",
        "Eskişehir", "Gaziantep", "Giresun", "Gümüşhane", "Hakkâri", "Hatay",
        "Iğdır", "Isparta", "İstanbul", "İzmir", "Kahramanmaraş", "Karabük",
        "Karaman", "Kars", "Kastamonu", "Kayseri", "Kırıkkale", "Kırklareli",
        "Kırşehir", "Kilis", "Kocaeli", "Konya", "Kütahya", "Malatya",
        "Manisa", "Mardin", "Mersin", "Muğla", "Muş", "Nevşehir", "Niğde",
        "Ordu", "Osmaniye", "Rize", "Sakarya", "Samsun", "Siirt", "Sinop",
        "Sivas", "Şanlıurfa", "Şırnak", "Tekirdağ", "Tokat", "Trabzon",
        "Tunceli", "Uşak", "Van", "Yalova", "Yozgat", "Zonguldak",
    ],
}

# country code -> [(region_key, name), ...] (alphabetical by name)
REGIONS: dict[str, list[tuple[str, str]]] = {
    cc: sorted(_build(cc, names), key=lambda kv: kv[1].lower())
    for cc, names in _RAW.items()
}

# region_key -> (country_code, name)
_INDEX: dict[str, tuple[str, str]] = {
    key: (cc, name) for cc, items in REGIONS.items() for key, name in items
}


def regions_of(country: Optional[str]) -> list[tuple[str, str]]:
    return REGIONS.get((country or "").upper(), [])


def region_country(key: Optional[str]) -> Optional[str]:
    hit = _INDEX.get(key or "")
    return hit[0] if hit else None


def region_name(key: Optional[str]) -> str:
    hit = _INDEX.get(key or "")
    return hit[1] if hit else (key or "")


def is_valid(country: Optional[str], key: Optional[str]) -> bool:
    return bool(country and key and region_country(key) == country.upper())
