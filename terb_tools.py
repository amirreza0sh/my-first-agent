"""TERB tool layer: dataset, queries, scoring and the tool schemas given to Claude.

Everything here is deterministic and testable without an API key.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

try:
    from zoneinfo import ZoneInfo

    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:  # tzdata missing (e.g. Windows without `pip install tzdata`)
    TEHRAN = timezone(timedelta(hours=3, minutes=30))  # Iran has had no DST since 2022

# datetime.weekday(): Monday=0 ... Sunday=6
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEK_ORDER_IR = ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"]

STALE_AFTER_DAYS = 7
CLOSING_SOON_MINUTES = 30
SCORE_WEIGHTS = {"price": 0.4, "distance": 0.3, "hours": 0.3}
TEHRAN_BBOX = (35.55, 35.90, 51.05, 51.75)  # lat_min, lat_max, lng_min, lng_max

DATA_NOTE = (
    "Prices and stock come from a snapshot that is about a week old; stock is never "
    "confirmed. Identify shops by address (names are mostly unreliable). Phone numbers "
    "are not available."
)

# Approximate centres of well-known Tehran areas (used to turn a spoken area into coordinates).
AREAS = [
    ("میدان آزادی", ["آزادی", "azadi"], 35.6997, 51.3380),
    ("ونک", ["ونک", "vanak"], 35.7570, 51.4090),
    ("تجریش", ["تجریش", "tajrish"], 35.8042, 51.4331),
    ("میدان انقلاب", ["انقلاب", "enghelab"], 35.7015, 51.3915),
    ("میدان ولیعصر", ["ولیعصر", "valiasr"], 35.7116, 51.4090),
    ("بازار بزرگ تهران", ["بازار بزرگ", "بازار تهران", "grand bazaar"], 35.6736, 51.4203),
    ("صادقیه", ["صادقیه", "sadeghiyeh"], 35.7155, 51.3050),
    ("اکباتان", ["اکباتان", "ekbatan"], 35.7175, 51.2960),
]


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize(text: Any) -> str:
    """Lowercase, unify Persian/Arabic letters and digits, ZWNJ -> space."""
    if text is None:
        return ""
    t = str(text).translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return re.sub(r"\s+", " ", t).strip().lower()


def now_tehran() -> datetime:
    return datetime.now(TEHRAN)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


def _coords(shop: dict) -> tuple[float, float] | None:
    lat = shop.get("latitude", shop.get("lat"))
    lng = shop.get("longitude", shop.get("lng"))
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def whatsapp_link(number: str | None, text: str | None = None) -> str | None:
    if not number:
        return None
    digits = re.sub(r"\D", "", normalize(number))
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "98" + digits[1:]
    if len(digits) < 10:
        return None
    link = f"https://wa.me/{digits}"
    return link + (f"?text={quote(text)}" if text else "")


def maps_link(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


# ----------------------------------------------------------------------------
# Opening hours (real clock, real weekday, overnight + split intervals)
# ----------------------------------------------------------------------------
_INTERVAL = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
_CLOSED_WORDS = {"closed", "تعطیل", "بسته"}


def parse_hours(value: Any) -> list[tuple[int, int]] | None:
    """Return minute intervals, [] if explicitly closed, None if unknown.

    "09:00-21:00" -> [(540, 1260)]; "20:00-02:00" -> [(1200, 1560)] (overnight);
    "09:00-13:00,16:00-21:00" -> two intervals.
    """
    text = normalize(value)
    if not text:
        return None
    if text in _CLOSED_WORDS:
        return []
    out = []
    for m in _INTERVAL.finditer(text):
        oh, om, ch, cm = map(int, m.groups())
        if oh > 24 or ch > 24 or om > 59 or cm > 59:
            continue
        o, c = oh * 60 + om, ch * 60 + cm
        if c == o:
            o, c = 0, 1440  # same open/close time -> treated as 24h
        elif c < o:
            c += 1440  # closes after midnight
        out.append((o, c))
    return out or None


def _fmt(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def open_status(hours: dict | None, now: datetime) -> dict:
    """Status of a shop at `now`: open | closed | unknown, plus opening/closing info."""
    hours = hours or {}
    wd = now.weekday()
    today_raw = hours.get(DAYS[wd])
    today = parse_hours(today_raw)
    yesterday = parse_hours(hours.get(DAYS[(wd - 1) % 7])) or []
    minute = now.hour * 60 + now.minute
    info = {
        "status": "unknown",
        "hours_today": today_raw if isinstance(today_raw, str) else None,
        "closes_at": None,
        "opens_at": None,
        "minutes_until_close": None,
        "closing_soon": False,
    }

    def opened(left: int, close_abs: int) -> dict:
        info.update(
            status="open",
            closes_at=_fmt(close_abs),
            minutes_until_close=left,
            closing_soon=left <= CLOSING_SOON_MINUTES,
        )
        return info

    for _o, c in yesterday:  # yesterday's overnight interval spilling into today
        if c > 1440 and minute < c - 1440:
            return opened(c - 1440 - minute, c)
    if today is None:
        return info
    for o, c in today:
        if o <= minute < c:
            return opened(c - minute, c)
    info["status"] = "closed"
    later = [o for o, _c in today if o > minute]
    if later:
        info["opens_at"] = _fmt(min(later))
    return info


# ----------------------------------------------------------------------------
# Dataset
# ----------------------------------------------------------------------------
class Dataset:
    def __init__(self, shops: list[dict], products: list[dict], offers: list[dict], source: str = "custom"):
        self.source = source
        self.shops = {s["id"]: s for s in shops}
        self.products = {p["id"]: p for p in products}
        self.offers_by_product: dict[str, list[dict]] = defaultdict(list)
        for o in offers:
            if o.get("product_id"):
                self.offers_by_product[o["product_id"]].append(o)
        self.haystack = {
            pid: normalize(
                " ".join(
                    str(p.get(k) or "")
                    for k in ("title_fa", "title_en", "brand", "category", "subcategory")
                )
            )
            for pid, p in self.products.items()
        }

    @classmethod
    def from_dir(cls, directory: str | Path) -> "Dataset":
        d = Path(directory)

        def load(name: str):
            with open(d / name, encoding="utf-8") as f:
                return json.load(f)

        return cls(load("shops.json"), load("products.json"), load("offers.json"), source=f"files:{d}")

    @classmethod
    def sample(cls) -> "Dataset":
        from sample_data import OFFERS, PRODUCTS, SHOPS

        return cls(SHOPS, PRODUCTS, OFFERS, source="sample")


def load_dataset(path: str | None = None) -> Dataset:
    """Real files if TERB_DATA_DIR (or `path`) holds them, otherwise the sample data."""
    path = path or os.environ.get("TERB_DATA_DIR")
    if path and (Path(path) / "shops.json").exists():
        return Dataset.from_dir(path)
    return Dataset.sample()


def _title(product: dict) -> str:
    return product.get("title_fa") or product.get("title_en") or product.get("id", "")


def _sizes(product: dict) -> list[str]:
    s = (product.get("specs") or {}).get("size")
    if s is None:
        return []
    values = s if isinstance(s, (list, tuple, set)) else re.split(r"[,\s/،]+", normalize(s))
    return [normalize(v) for v in values if normalize(v)]


def _valid_offer(o: dict) -> bool:
    price = o.get("price")
    return isinstance(price, (int, float)) and price > 0 and o.get("availability") != "out_of_stock"


# ----------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------
class TerbTools:
    def __init__(self, dataset: Dataset, clock: Callable[[], datetime] = now_tehran):
        self.ds = dataset
        self.clock = clock
        self._registry: dict[str, Callable[..., dict]] = {
            "search_products": self.search_products,
            "resolve_location": self.resolve_location,
            "find_shops": self.find_shops,
            "compare_shops": self.compare_shops,
            "get_shop_details": self.get_shop_details,
            "is_shop_open": self.is_shop_open,
        }

    def execute(self, name: str, arguments: dict | None) -> dict:
        fn = self._registry.get(name)
        if fn is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return fn(**(arguments or {}))
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}
        except Exception as e:  # never crash the chat loop on a tool failure
            return {"error": f"{name} failed: {e}"}

    # -- Stage 1 -------------------------------------------------------------
    def search_products(self, query, brand=None, budget_min=None, budget_max=None, size=None, limit=5) -> dict:
        tokens = normalize(query).split()
        if not tokens:
            return {"error": "empty query"}
        brand_n = normalize(brand) if brand else None
        size_n = normalize(size) if size else None
        found = []
        for pid, product in self.ds.products.items():
            hay = self.ds.haystack[pid]
            if not all(t in hay for t in tokens):
                continue
            if brand_n and brand_n not in normalize(product.get("brand")):
                continue
            sizes = _sizes(product)
            if size_n and sizes and size_n not in sizes:
                continue
            offers = [o for o in self.ds.offers_by_product.get(pid, []) if _valid_offer(o) and o.get("shop_id") in self.ds.shops]
            if budget_min is not None:
                offers = [o for o in offers if o["price"] >= budget_min]
            if budget_max is not None:
                offers = [o for o in offers if o["price"] <= budget_max]
            if not offers:
                continue
            prices = [o["price"] for o in offers]
            specs = product.get("specs") or {}
            found.append(
                {
                    "product_id": pid,
                    "title": _title(product),
                    "title_en": product.get("title_en"),
                    "brand": product.get("brand"),
                    "lowest_price": min(prices),
                    "highest_price": max(prices),
                    "shop_count": len({o["shop_id"] for o in offers}),
                    "available_sizes": sizes,
                    "size_known": bool(sizes),
                    "color": specs.get("color"),
                }
            )
        found.sort(key=lambda r: (-r["shop_count"], r["lowest_price"]))
        result = {"total_matching": len(found), "products": found[: max(1, min(int(limit), 10))], "data_note": DATA_NOTE}
        if not found:
            result["hint"] = "Nothing matched. Relax budget/size/brand or try a broader query."
        return result

    # -- Location --------------------------------------------------------------
    def resolve_location(self, query) -> dict:
        q = normalize(query)
        m = re.search(r"(-?\d+\.\d+)\s*[,،;\s]\s*(-?\d+\.\d+)", q)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if not (TEHRAN_BBOX[0] <= lat <= TEHRAN_BBOX[1] and TEHRAN_BBOX[2] <= lng <= TEHRAN_BBOX[3]):
                return {"found": False, "message": "These coordinates are outside Tehran; only Tehran is covered."}
            return {"found": True, "name": "coordinates", "lat": lat, "lng": lng, "approximate": False}
        best, best_len = None, 0
        for canonical, aliases, lat, lng in AREAS:
            for alias in aliases:
                a = normalize(alias)
                if a in q and len(a) > best_len:
                    best, best_len = (canonical, lat, lng), len(a)
        if best:
            return {"found": True, "name": best[0], "lat": best[1], "lng": best[2], "approximate": True}
        return {
            "found": False,
            "message": "Unknown area. Ask the user for a well-known Tehran landmark or coordinates.",
            "known_areas": [a[0] for a in AREAS],
        }

    # -- Stage 2 / 3 -----------------------------------------------------------
    def _price_age(self, last_updated: Any, now: datetime) -> int | None:
        try:
            return max((now.date() - date.fromisoformat(str(last_updated)[:10])).days, 0)
        except ValueError:
            return None

    def _row(self, shop: dict, offer: dict, lat: float, lng: float, now: datetime) -> dict | None:
        coords = _coords(shop)
        if coords is None:
            return None
        services = shop.get("services") or {}
        age = self._price_age(offer.get("last_updated"), now)
        has_real_name = bool(shop.get("instagram") or shop.get("website"))
        return {
            "shop_id": shop["id"],
            "address": shop.get("address_full") or shop.get("address"),
            "name": shop.get("name") if has_real_name else None,
            "distance_km": round(haversine_km(lat, lng, *coords), 2),
            "price": offer["price"],
            "availability": offer.get("availability", "unknown"),
            "price_updated": offer.get("last_updated"),
            "price_age_days": age,
            "price_stale": age is None or age >= STALE_AFTER_DAYS,
            "hours": open_status(shop.get("operating_hours"), now),
            "services": {k: bool(services.get(k)) for k in ("terb_pay", "installment", "delivery", "warranty")},
            "has_whatsapp": bool((shop.get("contact") or {}).get("whatsapp")),
        }

    def _collect(self, product_id, lat, lng, max_distance_km=None, installment_only=False,
                 delivery_only=False, terb_pay_only=False, open_now_only=False, exclude_shop_ids=None):
        product = self.ds.products.get(product_id)
        if product is None:
            return None, []
        lat, lng = float(lat), float(lng)
        now = self.clock()
        excluded = set(exclude_shop_ids or [])
        cheapest: dict[str, dict] = {}
        for o in self.ds.offers_by_product.get(product_id, []):
            sid = o.get("shop_id")
            if not _valid_offer(o) or sid in excluded or sid not in self.ds.shops:
                continue
            if sid not in cheapest or o["price"] < cheapest[sid]["price"]:
                cheapest[sid] = o
        rows = []
        for sid, offer in cheapest.items():
            row = self._row(self.ds.shops[sid], offer, lat, lng, now)
            if row is None:
                continue
            if max_distance_km is not None and row["distance_km"] > max_distance_km:
                continue
            sv = row["services"]
            if (installment_only and not sv["installment"]) or (delivery_only and not sv["delivery"]) \
                    or (terb_pay_only and not sv["terb_pay"]):
                continue
            if open_now_only and row["hours"]["status"] != "open":
                continue
            rows.append(row)
        return product, rows

    def find_shops(self, product_id, lat, lng, max_distance_km=None, installment_only=False,
                   delivery_only=False, terb_pay_only=False, open_now_only=False,
                   exclude_shop_ids=None, limit=7) -> dict:
        product, rows = self._collect(product_id, lat, lng, max_distance_km, installment_only,
                                      delivery_only, terb_pay_only, open_now_only, exclude_shop_ids)
        if product is None:
            return {"error": f"unknown product_id: {product_id}"}
        rows.sort(key=lambda r: (r["distance_km"], r["price"]))
        return {
            "product": {"product_id": product_id, "title": _title(product)},
            "total_matching": len(rows),
            "shops": rows[: max(1, min(int(limit), 10))],
            "data_note": DATA_NOTE,
        }

    def compare_shops(self, product_id, lat, lng, max_distance_km=None, installment_only=False,
                      delivery_only=False, terb_pay_only=False, open_now_only=False,
                      exclude_shop_ids=None) -> dict:
        """Rank shops. Shops confirmed closed are NOT ranked; they are listed separately."""
        product, rows = self._collect(product_id, lat, lng, max_distance_km, installment_only,
                                      delivery_only, terb_pay_only, open_now_only, exclude_shop_ids)
        if product is None:
            return {"error": f"unknown product_id: {product_id}"}
        candidates = [r for r in rows if r["hours"]["status"] != "closed"]
        closed = sorted((r for r in rows if r["hours"]["status"] == "closed"), key=lambda r: r["distance_km"])
        base = {
            "product": {"product_id": product_id, "title": _title(product)},
            "weights": SCORE_WEIGHTS,
            "closed_shops": closed,
            "data_note": DATA_NOTE,
        }
        if not candidates:
            return {**base, "recommendation": None, "ranked": [],
                    "message": "No open shop found. See closed_shops (opens_at) or relax the filters."}
        best_price = min(r["price"] for r in candidates)
        min_dist = min(r["distance_km"] for r in candidates)
        cheapest_price = best_price
        ranked = sorted((self._score(r, best_price) for r in candidates), key=lambda r: (-r["score"], r["price"]))
        for i, r in enumerate(ranked):
            r["is_cheapest"] = r["price"] == cheapest_price
            r["is_closest"] = r["distance_km"] == min_dist
            r["saving_vs_next"] = ranked[i + 1]["price"] - r["price"] if i + 1 < len(ranked) else None
        return {**base, "recommendation": ranked[0], "ranked": ranked[:5]}

    @staticmethod
    def _score(row: dict, best_price: int | float) -> dict:
        price_score = 100.0 * best_price / row["price"]
        distance_score = 100.0 / (1.0 + row["distance_km"])  # 0 km -> 100, never divides by zero
        h = row["hours"]
        hours_score = (50.0 if h["closing_soon"] else 100.0) if h["status"] == "open" else 60.0
        total = (price_score * SCORE_WEIGHTS["price"]
                 + distance_score * SCORE_WEIGHTS["distance"]
                 + hours_score * SCORE_WEIGHTS["hours"])
        return {**row, "score": round(total, 1),
                "score_breakdown": {"price": round(price_score, 1), "distance": round(distance_score, 1),
                                    "hours": round(hours_score, 1)}}

    # -- Stage 4 -----------------------------------------------------------------
    def is_shop_open(self, shop_id) -> dict:
        shop = self.ds.shops.get(shop_id)
        if shop is None:
            return {"error": f"unknown shop_id: {shop_id}"}
        now = self.clock()
        return {"shop_id": shop_id, "address": shop.get("address_full"),
                "checked_at": now.strftime("%Y-%m-%d %H:%M"), **open_status(shop.get("operating_hours"), now)}

    def get_shop_details(self, shop_id, product_id=None, size=None) -> dict:
        shop = self.ds.shops.get(shop_id)
        if shop is None:
            return {"error": f"unknown shop_id: {shop_id}"}
        now = self.clock()
        contact = shop.get("contact") or {}
        coords = _coords(shop)
        address = shop.get("address_full") or shop.get("address") or ""
        product = self.ds.products.get(product_id) if product_id else None

        message = None
        if product:
            message = f"سلام، {_title(product)}" + (f" سایز {normalize(size)}" if size else "") + " موجود دارید؟"
        details = {
            "shop_id": shop_id,
            "address": address,
            "name": shop.get("name") if (shop.get("instagram") or shop.get("website")) else None,
            "instagram": shop.get("instagram"),
            "website": shop.get("website"),
            "google_maps_link": maps_link(*coords) if coords else None,
            "likely_in_mall_or_bazaar": any(w in normalize(address) for w in ("بازار", "مجتمع", "پاساژ", "مرکز خرید")),
            "hours": open_status(shop.get("operating_hours"), now),
            "weekly_hours": {d: (shop.get("operating_hours") or {}).get(d) for d in WEEK_ORDER_IR},
            "whatsapp_link": whatsapp_link(contact.get("whatsapp"), message),
            "bale": contact.get("bale"),
            "services": {k: bool((shop.get("services") or {}).get(k)) for k in ("terb_pay", "installment", "delivery", "warranty")},
            "suggested_message": message,
            "contact_note": None if contact.get("whatsapp") else "No messenger contact on file: advise visiting in person.",
            "data_note": DATA_NOTE,
        }
        if product:
            offers = [o for o in self.ds.offers_by_product.get(product_id, []) if o.get("shop_id") == shop_id]
            if offers:
                o = min(offers, key=lambda x: x.get("price", float("inf")))
                age = self._price_age(o.get("last_updated"), now)
                details["product_offer"] = {"title": _title(product), "price": o.get("price"),
                                            "availability": o.get("availability", "unknown"),
                                            "price_updated": o.get("last_updated"), "price_age_days": age,
                                            "price_stale": age is None or age >= STALE_AFTER_DAYS}
        return details


# ----------------------------------------------------------------------------
# Tool schemas for the Claude API
# ----------------------------------------------------------------------------
_FILTERS = {
    "max_distance_km": {"type": "number", "description": "Only shops within this distance (km)."},
    "installment_only": {"type": "boolean", "description": "Only shops offering installment payment."},
    "delivery_only": {"type": "boolean", "description": "Only shops offering delivery."},
    "terb_pay_only": {"type": "boolean", "description": "Only shops accepting TerbPay."},
    "open_now_only": {"type": "boolean", "description": "Only shops confirmed open right now (shops with unknown hours are excluded)."},
    "exclude_shop_ids": {"type": "array", "items": {"type": "string"},
                         "description": "Shops to skip, e.g. ones the user visited and found closed or out of stock."},
}
_LOC = {
    "product_id": {"type": "string", "description": "product_id from search_products."},
    "lat": {"type": "number", "description": "User latitude (from resolve_location)."},
    "lng": {"type": "number", "description": "User longitude (from resolve_location)."},
}

TOOL_SCHEMAS = [
    {
        "name": "search_products",
        "description": "Stage 1. Search products by free text (Persian or English). Returns the best matches with lowest/highest in-stock price, number of shops carrying them and available sizes. Budget is in tomans.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "e.g. 'کفش نایک' or 'Nike shoes'"},
            "brand": {"type": "string"}, "budget_min": {"type": "number"}, "budget_max": {"type": "number"},
            "size": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
    },
    {
        "name": "resolve_location",
        "description": "Turn an area/landmark name in Tehran (or 'lat, lng' text) into coordinates. Call before find_shops/compare_shops.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "find_shops",
        "description": "Stage 2. List shops with the product in stock (or unknown stock), sorted by distance, with price, price age, opening status and services. Also use it for fallbacks (exclude_shop_ids + open_now_only).",
        "input_schema": {"type": "object", "properties": {**_LOC, **_FILTERS, "limit": {"type": "integer"}},
                         "required": ["product_id", "lat", "lng"]},
    },
    {
        "name": "compare_shops",
        "description": "Stage 3. Score and rank shops (40% price, 30% distance, 30% opening hours). Closed shops are never ranked; they come back in closed_shops. Returns a recommendation.",
        "input_schema": {"type": "object", "properties": {**_LOC, **_FILTERS}, "required": ["product_id", "lat", "lng"]},
    },
    {
        "name": "get_shop_details",
        "description": "Stage 4. Full details of one shop: address, Google Maps link, today's hours and status, weekly hours, WhatsApp link with a prefilled message, services and the product's price/age there.",
        "input_schema": {"type": "object", "properties": {
            "shop_id": {"type": "string"}, "product_id": {"type": "string"},
            "size": {"type": "string", "description": "Size to put in the prefilled message."}},
            "required": ["shop_id"]},
    },
    {
        "name": "is_shop_open",
        "description": "Check whether one shop is open right now (Tehran time) and when it opens/closes.",
        "input_schema": {"type": "object", "properties": {"shop_id": {"type": "string"}}, "required": ["shop_id"]},
    },
]
