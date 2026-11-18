"""Country list: the 193 UN member states (ISO 3166-1 alpha-2).

Disputed or partially recognised territories are intentionally left out so the
ranking stays neutral. Edit COUNTRIES to add/remove entries.
"""
from __future__ import annotations

from typing import Optional

COUNTRIES: dict[str, str] = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AD": "Andorra",
    "AO": "Angola", "AG": "Antigua and Barbuda", "AR": "Argentina",
    "AM": "Armenia", "AU": "Australia", "AT": "Austria", "AZ": "Azerbaijan",
    "BS": "Bahamas", "BH": "Bahrain", "BD": "Bangladesh", "BB": "Barbados",
    "BY": "Belarus", "BE": "Belgium", "BZ": "Belize", "BJ": "Benin",
    "BT": "Bhutan", "BO": "Bolivia", "BA": "Bosnia and Herzegovina",
    "BW": "Botswana", "BR": "Brazil", "BN": "Brunei", "BG": "Bulgaria",
    "BF": "Burkina Faso", "BI": "Burundi", "CV": "Cabo Verde",
    "KH": "Cambodia", "CM": "Cameroon", "CA": "Canada",
    "CF": "Central African Republic", "TD": "Chad", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "KM": "Comoros", "CG": "Congo",
    "CD": "DR Congo", "CR": "Costa Rica", "CI": "Côte d'Ivoire",
    "HR": "Croatia", "CU": "Cuba", "CY": "Cyprus", "CZ": "Czechia",
    "DK": "Denmark", "DJ": "Djibouti", "DM": "Dominica",
    "DO": "Dominican Republic", "EC": "Ecuador", "EG": "Egypt",
    "SV": "El Salvador", "GQ": "Equatorial Guinea", "ER": "Eritrea",
    "EE": "Estonia", "SZ": "Eswatini", "ET": "Ethiopia", "FJ": "Fiji",
    "FI": "Finland", "FR": "France", "GA": "Gabon", "GM": "Gambia",
    "GE": "Georgia", "DE": "Germany", "GH": "Ghana", "GR": "Greece",
    "GD": "Grenada", "GT": "Guatemala", "GN": "Guinea",
    "GW": "Guinea-Bissau", "GY": "Guyana", "HT": "Haiti", "HN": "Honduras",
    "HU": "Hungary", "IS": "Iceland", "IN": "India", "ID": "Indonesia",
    "IR": "Iran", "IQ": "Iraq", "IE": "Ireland", "IL": "Israel",
    "IT": "Italy", "JM": "Jamaica", "JP": "Japan", "JO": "Jordan",
    "KZ": "Kazakhstan", "KE": "Kenya", "KI": "Kiribati",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait",
    "KG": "Kyrgyzstan", "LA": "Laos", "LV": "Latvia", "LB": "Lebanon",
    "LS": "Lesotho", "LR": "Liberia", "LY": "Libya", "LI": "Liechtenstein",
    "LT": "Lithuania", "LU": "Luxembourg", "MG": "Madagascar",
    "MW": "Malawi", "MY": "Malaysia", "MV": "Maldives", "ML": "Mali",
    "MT": "Malta", "MH": "Marshall Islands", "MR": "Mauritania",
    "MU": "Mauritius", "MX": "Mexico", "FM": "Micronesia", "MD": "Moldova",
    "MC": "Monaco", "MN": "Mongolia", "ME": "Montenegro", "MA": "Morocco",
    "MZ": "Mozambique", "MM": "Myanmar", "NA": "Namibia", "NR": "Nauru",
    "NP": "Nepal", "NL": "Netherlands", "NZ": "New Zealand",
    "NI": "Nicaragua", "NE": "Niger", "NG": "Nigeria",
    "MK": "North Macedonia", "NO": "Norway", "OM": "Oman", "PK": "Pakistan",
    "PW": "Palau", "PA": "Panama", "PG": "Papua New Guinea",
    "PY": "Paraguay", "PE": "Peru", "PH": "Philippines", "PL": "Poland",
    "PT": "Portugal", "QA": "Qatar", "RO": "Romania", "RU": "Russia",
    "RW": "Rwanda", "KN": "Saint Kitts and Nevis", "LC": "Saint Lucia",
    "VC": "Saint Vincent and the Grenadines", "WS": "Samoa",
    "SM": "San Marino", "ST": "São Tomé and Príncipe", "SA": "Saudi Arabia",
    "SN": "Senegal", "RS": "Serbia", "SC": "Seychelles",
    "SL": "Sierra Leone", "SG": "Singapore", "SK": "Slovakia",
    "SI": "Slovenia", "SB": "Solomon Islands", "SO": "Somalia",
    "ZA": "South Africa", "SS": "South Sudan", "ES": "Spain",
    "LK": "Sri Lanka", "SD": "Sudan", "SR": "Suriname", "SE": "Sweden",
    "CH": "Switzerland", "SY": "Syria", "TJ": "Tajikistan",
    "TZ": "Tanzania", "TH": "Thailand", "TL": "Timor-Leste", "TG": "Togo",
    "TO": "Tonga", "TT": "Trinidad and Tobago", "TN": "Tunisia",
    "TR": "Türkiye", "TM": "Turkmenistan", "TV": "Tuvalu", "UG": "Uganda",
    "UA": "Ukraine", "AE": "United Arab Emirates", "GB": "United Kingdom",
    "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan",
    "VU": "Vanuatu", "VE": "Venezuela", "VN": "Vietnam", "YE": "Yemen",
    "ZM": "Zambia", "ZW": "Zimbabwe",
}

# Alphabetical by name, used for the paginated picker.
SORTED_CODES: list[str] = sorted(COUNTRIES, key=lambda c: COUNTRIES[c].lower())

# Shown first in the picker (Central Asia + biggest Telegram audiences).
POPULAR: tuple[str, ...] = (
    "UZ", "KZ", "KG", "TJ", "TM", "RU", "TR", "UA", "AZ", "US", "IN", "BR",
)


def flag(code: str) -> str:
    """ISO alpha-2 code -> flag emoji (regional indicator symbols)."""
    code = code.upper()
    if len(code) != 2 or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def name_of(code: Optional[str]) -> str:
    if not code:
        return "Unknown"
    return COUNTRIES.get(code.strip().upper(), code.strip().upper())


def label(code: Optional[str]) -> str:
    """'🇺🇿 Uzbekistan'."""
    if not code:
        return "🏳️ Unknown"
    code = code.strip().upper()
    return f"{flag(code)} {name_of(code)}"


def search(query: str, limit: int = 12) -> list[str]:
    """Case-insensitive name/code search; prefix matches first."""
    q = query.strip().lower()
    if not q:
        return []
    starts, contains = [], []
    for code in SORTED_CODES:
        n = COUNTRIES[code].lower()
        if code.lower() == q or n.startswith(q):
            starts.append(code)
        elif q in n:
            contains.append(code)
    return (starts + contains)[:limit]
