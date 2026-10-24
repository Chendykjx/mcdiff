"""Capturing a store menu as an immutable, diffable snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .mcp import McdBusinessError, McdMCPClient, McdMCPError

SCHEMA = "mcdiff/snapshot@1"
TZ = ZoneInfo("Asia/Shanghai")
SNAPSHOT_DIR = Path("data") / "snapshots"

#: daypart windows (start hour, minute) in local time
DAYPARTS: tuple[tuple[str, str, int, int], ...] = (
    ("breakfast", "早餐", 5, 0),
    ("lunch", "午餐", 10, 30),
    ("afternoon", "下午茶", 14, 0),
    ("dinner", "夜市", 17, 0),
    ("late-night", "深夜", 21, 30),
)


def now_local() -> datetime:
    return datetime.now(tz=TZ)


def infer_daypart(moment: datetime) -> tuple[str, str]:
    """Return ``(key, label)`` for a local timestamp."""
    minutes = moment.hour * 60 + moment.minute
    current = ("late-night", "深夜")
    for key, label, hour, minute in DAYPARTS:
        if minutes >= hour * 60 + minute:
            current = (key, label)
    return current


def yuan_to_cents(value: Any) -> int | None:
    """Menu prices are yuan strings ("14.9"); this returns integer cents."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    text = str(value).strip().replace("¥", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return int(round(float(match.group(0)) * 100))


def format_cents(cents: int | None, *, symbol: str = "¥") -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{symbol}{cents // 100}.{cents % 100:02d}"


def _checksum(meals: Mapping[str, Any], categories: Iterable[Mapping[str, Any]]) -> str:
    payload = {
        "meals": {
            code: [meal.get("name"), meal.get("price_cents"), meal.get("tags"), meal.get("category")]
            for code, meal in sorted(meals.items())
        },
        "categories": [[category.get("name"), category.get("codes")] for category in categories],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def snapshot_dir(root: str | Path = ".", store_code: str | None = None) -> Path:
    base = Path(root) / SNAPSHOT_DIR
    return base / store_code if store_code else base


def _parse_menu(result: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    data = result.data or {}
    categories_raw = data.get("categories") or []
    meals_raw = data.get("meals") or {}

    categories: list[dict[str, Any]] = []
    code_to_category: dict[str, str] = {}
    code_to_tags: dict[str, list[str]] = {}
    for category in categories_raw:
        name = (category.get("name") or "").replace("\n", " ").strip()
        codes: list[str] = []
        for entry in category.get("meals") or []:
            code = str(entry.get("code") or "")
            if not code:
                continue
            codes.append(code)
            code_to_category.setdefault(code, name)
            tags = [str(tag) for tag in (entry.get("tags") or [])]
            if tags:
                code_to_tags.setdefault(code, tags)
        categories.append({"name": name, "codes": codes})

    meals: dict[str, dict[str, Any]] = {}
    for code, meal in meals_raw.items():
        code = str(code)
        price_cents = yuan_to_cents(meal.get("currentPrice"))
        original_cents = yuan_to_cents(meal.get("originalPrice"))
        meals[code] = {
            "name": (meal.get("name") or "").strip(),
            "price_cents": price_cents if price_cents is not None else 0,
            "original_price_cents": original_cents,
            "tags": code_to_tags.get(code, []),
            "category": code_to_category.get(code, ""),
            "discount_type": meal.get("discountType"),
            "can_with_order": bool(meal.get("canWithOrder")),
            "image": meal.get("image") or "",
        }
    return meals, categories


def _summarise_coupons(result: Any) -> dict[str, Any]:
    coupons = result.data or []
    items = []
    for coupon in coupons[:20]:
        items.append(
            {
                "title": coupon.get("title"),
                "validity": coupon.get("tradeDateTime"),
            }
        )
    return {"count": len(coupons), "items": items}


def _summarise_promotions(result: Any) -> dict[str, Any]:
    promotions = result.data or []
    rules = []
    for promo in promotions[:20]:
        detail = promo.get("ruleDetail") or {}
        order_discount = detail.get("orderDiscount") or {}
        rules.append(
            {
                "promotionType": promo.get("promotionType"),
                "ruleCategory": promo.get("ruleCategory"),
                "startTime": promo.get("startTime"),
                "endTime": promo.get("endTime"),
                "discount": order_discount.get("discount"),
                "threshold": order_discount.get("threshold") or order_discount.get("conditionAmount"),
            }
        )
    return {"count": len(promotions), "rules": rules}


def capture(
    client: McdMCPClient,
    store: Mapping[str, Any],
    *,
    note: str | None = None,
    include_extras: bool = True,
    anchors: Iterable[str] | None = None,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Capture one store menu as a snapshot dict (does not write to disk)."""
    store_code = str(store.get("storeCode") or store.get("store_code") or "").strip()
    if not store_code:
        raise ValueError("storeCode is required")
    be_type = int(store.get("beType") or store.get("be_type") or 1)
    order_type = int(store.get("orderType") or store.get("order_type") or 1)
    args: dict[str, Any] = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if store.get("beCode"):
        args["beCode"] = str(store["beCode"])

    result = client.call("query-meals", args)
    meals, categories = _parse_menu(result)

    extras: dict[str, Any] = {}
    if include_extras:
        coupon_args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
        try:
            extras["coupons"] = _summarise_coupons(client.call("query-store-coupons", coupon_args))
        except McdMCPError as exc:  # closing hours, missing beCode, ...
            extras["coupons"] = {"error": str(exc)}
        try:
            extras["promotions"] = _summarise_promotions(client.call("query-promotions", coupon_args))
        except McdMCPError as exc:
            extras["promotions"] = {"error": str(exc)}

    anchor_prices: dict[str, Any] = {}
    for code in list(anchors or []):
        try:
            priced = client.call(
                "calculate-price",
                {
                    **{"storeCode": store_code, "orderType": order_type, "beType": be_type},
                    "items": [{"productCode": code, "quantity": 1}],
                },
            )
            data = priced.data or {}
            product_list = data.get("productList") or []
            anchor_prices[code] = {
                "price_cents": data.get("price"),
                "returned_items": len(product_list),
                # a silently dropped productCode is a known server quirk
                "dropped": len(product_list) == 0,
            }
        except McdMCPError as exc:
            anchor_prices[code] = {"error": str(exc)}

    moment = (moment or now_local()).astimezone(TZ)
    daypart_key, daypart_label = infer_daypart(moment)

    snapshot: dict[str, Any] = {
        "schema": SCHEMA,
        "captured_at": moment.isoformat(timespec="seconds"),
        "captured_ts": int(moment.timestamp()),
        "daypart": daypart_key,
        "daypart_label": daypart_label,
        "note": note or "",
        "synthetic": bool(store.get("synthetic", False)),
        "store": {
            "storeCode": store_code,
            "storeName": store.get("storeName") or store.get("store_name") or f"门店 {store_code}",
            "city": store.get("city") or "",
            "address": store.get("address") or "",
            "businessStartTime": store.get("businessStartTime") or "",
            "businessEndTime": store.get("businessEndTime") or "",
            "beType": be_type,
            "orderType": order_type,
        },
        "menu": {"meal_count": len(meals), "categories": categories},
        "meals": meals,
        "extras": extras,
    }
    if anchor_prices:
        snapshot["anchors"] = anchor_prices
    snapshot["checksum"] = _checksum(meals, categories)
    return snapshot


def save(snapshot: Mapping[str, Any], root: str | Path = ".") -> Path:
    store_code = str(snapshot["store"]["storeCode"])
    moment = datetime.fromisoformat(str(snapshot["captured_at"])).astimezone(TZ)
    folder = snapshot_dir(root, store_code)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{moment.strftime('%Y%m%dT%H%M')}-{snapshot.get('daypart', 'x')}.json"
    path = folder / filename
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8")
    return path


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_snapshots(root: str | Path = ".", store_code: str | None = None) -> list[Path]:
    base = snapshot_dir(root, store_code)
    if not base.exists():
        return []
    return sorted(base.rglob("*.json"), key=lambda p: p.name)


def load_series(root: str | Path = ".", store_code: str | None = None) -> list[dict[str, Any]]:
    return [load(path) for path in iter_snapshots(root, store_code)]


def latest(root: str | Path = ".", store_code: str | None = None) -> dict[str, Any] | None:
    series = load_series(root, store_code)
    return series[-1] if series else None


def previous(root: str | Path = ".", store_code: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    series = load_series(root, store_code)
    if len(series) < 2:
        return (series[0] if series else None), None
    return series[-2], series[-1]


def same_day(moment: datetime, other: datetime) -> bool:
    return moment.astimezone(TZ).date() == other.astimezone(TZ).date()


def describe(snapshot: Mapping[str, Any]) -> str:
    store = snapshot.get("store", {})
    moment = datetime.fromisoformat(str(snapshot["captured_at"])).astimezone(TZ)
    return (
        f"{store.get('storeName')}（{store.get('storeCode')}）"
        f" · {moment.strftime('%m-%d %H:%M')} · {snapshot.get('daypart_label', '')}"
    )


__all__ = [
    "DAYPARTS",
    "SCHEMA",
    "TZ",
    "capture",
    "describe",
    "format_cents",
    "infer_daypart",
    "iter_snapshots",
    "latest",
    "load",
    "load_series",
    "now_local",
    "previous",
    "save",
    "snapshot_dir",
    "yuan_to_cents",
]
