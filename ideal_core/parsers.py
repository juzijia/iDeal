from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse


ARROWS = r"(?:→|➜|->|－>|—>)"
PRICE_TOKEN = r"(?:Free|免费|限免|[$¥￥₺€£]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:USD|CNY|RMB|TRY|EUR|GBP))"
CURRENCY_BY_SYMBOL = {"$": "USD", "¥": "CNY", "￥": "CNY", "₺": "TRY", "€": "EUR", "£": "GBP"}


def extract_app_id(text: str) -> str:
    text = html.unescape(text or "").replace("\\/", "/")
    patterns = [
        r"/id(\d{6,12})(?:[/?#]|$)",
        r"[?&]id=(\d{6,12})(?:[&#]|$)",
        r"appstore-discounts\.eyelly\.me/(?:[a-z]{2})/(\d{6,12})(?:[/?#]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    try:
        query = parse_qs(urlparse(text).query)
        value = (query.get("id") or [""])[0]
        return value if value.isdigit() else ""
    except ValueError:
        return ""


def _price(value: str) -> tuple[Decimal | None, str]:
    token = html.unescape(value or "").strip()
    if re.search(r"free|免费|限免", token, flags=re.I):
        return Decimal("0"), ""
    currency = ""
    for symbol, code in CURRENCY_BY_SYMBOL.items():
        if symbol in token:
            currency = code
            break
    for code in ("USD", "CNY", "RMB", "TRY", "EUR", "GBP"):
        if code in token.upper():
            currency = "CNY" if code == "RMB" else code
            break
    number = re.search(r"\d+(?:[.,]\d+)?", token)
    if not number:
        return None, currency
    raw = number.group(0)
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw), currency
    except InvalidOperation:
        return None, currency


def parse_app_price_transition(text: str) -> tuple[Decimal | None, Decimal | None, str]:
    """只解析 App 本体的 Discount Information 行，避免把 IAP 误判为本体降价。"""
    clean = html.unescape(re.sub(r"<[^>]+>", "\n", text or ""))
    patterns = [
        rf"Discount\s+Information[^\r\n]*?Price\s*[:：]\s*({PRICE_TOKEN})\s*{ARROWS}\s*({PRICE_TOKEN})",
        rf"(?:App\s+Price|应用价格|本体价格)\s*[:：]?\s*({PRICE_TOKEN})\s*{ARROWS}\s*({PRICE_TOKEN})",
        rf"(?:^|\r?\n)\s*(?:Price|价格|價格)\s*[:：]\s*({PRICE_TOKEN})\s*{ARROWS}\s*({PRICE_TOKEN})",
        rf"[\[(]\s*({PRICE_TOKEN})\s*{ARROWS}\s*({PRICE_TOKEN})\s*[\])]",
        rf"^\s*({PRICE_TOKEN})\s*{ARROWS}\s*({PRICE_TOKEN})\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.I | re.M)
        if not match:
            continue
        old_price, old_currency = _price(match.group(1))
        new_price, new_currency = _price(match.group(2))
        return old_price, new_price, new_currency or old_currency
    return None, None, ""


def infer_deal_kind(text: str, old_price: Decimal | None, new_price: Decimal | None) -> str:
    if old_price is not None and new_price is not None and old_price > new_price:
        return "free" if new_price == 0 else "discount"
    clean = html.unescape(re.sub(r"<[^>]+>", " ", text or "")).lower()
    if re.search(r"\b(?:gone|now|temporarily)\s+free\b|限免|免费", clean):
        return "free_hint"
    if re.search(r"\bprice\s+drop\b|\bon\s+sale\b|降价|打折", clean):
        return "discount_hint"
    return "discovery"


def parse_datetime(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
