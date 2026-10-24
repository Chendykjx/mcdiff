"""Deterministic synthetic history, for demos that need more than one day.

The generator never pretends to be real: every snapshot it writes carries
``"synthetic": true`` and a note, and the UI prints a watermark.  A fresh clone
can therefore show the diff/museum experience without a token.

Model
-----
* every real SKU keeps its price today; a subset gets a *historical* delta that
  decays linearly to zero, i.e. the price "crept up" over the window;
* a handful of legacy SKUs exist at the start of the window and are retired
  mid-window (they become museum exhibits with a real lifespan);
* a handful of "launch" SKUs appear mid-window and survive to today;
* everything is derived from ``sha256(seed_text + day)`` so runs are identical.
"""

from __future__ import annotations

import copy
import hashlib
import random
from datetime import datetime, timedelta
from typing import Any

from .snapshot import TZ, infer_daypart

LEGACY_ITEMS = [
    ("薯角（海盐味）", "小食甜品/其他", 1150, ["经典小食"], 3),
    ("麦麦脆汁鸡-琵琶腿", "蘸酱炸鸡", 1850, ["经典"], 6),
    ("菠萝派", "小食甜品/其他", 850, ["季节限定"], 9),
    ("草莓奶昔", "饮品", 1250, ["经典"], 12),
    ("黑椒牛肉堡", "巨无霸 牛鱼肉堡", 2350, ["限时"], 16),
    ("焦糖拿铁", "麦咖啡™", 1650, ["麦咖啡"], 20),
]

LAUNCH_ITEMS = [
    ("龙焰芝士棒鸡腿堡三件套", "鸡肉汉堡/卷", 2990, ["新品", "韩式风味"], 4),
    ("厚松饼堡早餐三件套", "精选单人餐", 1500, ["早餐", "限时"], 9),
    ("心形薯饼", "小食甜品/其他", 900, ["季节限定"], 13),
    ("黑松露风味安格斯厚牛堡", "安格斯MAX 厚牛堡", 4590, ["新品", "限时"], 18),
]


def _rng(seed_text: str, salt: str) -> random.Random:
    digest = hashlib.sha256(f"{seed_text}:{salt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _synthetic_code(name: str) -> str:
    return "9900" + f"{int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 100000:06d}"


def _meal(name: str, category: str, price: int, tags: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "price_cents": price,
        "original_price_cents": price,
        "tags": list(tags),
        "category": category,
        "discount_type": None,
        "can_with_order": False,
        "image": "",
    }


def generate_history(
    base_snapshot: dict[str, Any],
    *,
    days: int = 30,
    seed_text: str | None = None,
) -> list[dict[str, Any]]:
    """Return ``days`` snapshots, oldest first, ending with the real capture."""
    store_code = str(base_snapshot["store"]["storeCode"])
    seed_text = seed_text or store_code
    end = datetime.fromisoformat(str(base_snapshot["captured_at"])).astimezone(TZ)
    today_meals = copy.deepcopy(base_snapshot.get("meals") or {})
    categories_now = [c.get("name") for c in base_snapshot.get("menu", {}).get("categories") or []]

    rng = _rng(seed_text, "plan")
    total_days = max(2, days)
    # historical price drift: 20% of real SKUs jump by ±1–3 元 on one specific day
    drifted: dict[str, tuple[int, int]] = {}
    codes = sorted(today_meals)
    for index, code in enumerate(rng.sample(codes, k=max(1, len(codes) // 5))):
        delta = rng.choice([-300, -200, -100, 100, 200, 300])
        # the newest step always carries a couple of movements, so "today" has news
        change_day = 1 if index < 3 else rng.randint(2, total_days - 2)
        drifted[code] = (delta, change_day)

    legacy = [
        (_synthetic_code(name), _meal(name, category, price, tags), retire_day)
        for name, category, price, tags, retire_day in LEGACY_ITEMS
    ]
    launches = [
        (_synthetic_code(name), _meal(name, category, price, tags), launch_day)
        for name, category, price, tags, launch_day in LAUNCH_ITEMS
    ]

    state: dict[str, dict[str, Any]] = copy.deepcopy(today_meals)
    base_price: dict[str, int] = {code: int(meal["price_cents"]) for code, meal in today_meals.items()}
    for code, meal, _retire in legacy:
        state[code] = copy.deepcopy(meal)
        base_price[code] = int(meal["price_cents"])
    for code, meal, _launch in launches:
        base_price[code] = int(meal["price_cents"])

    series: list[dict[str, Any]] = []
    for day_index in range(total_days - 1, 0, -1):  # oldest → yesterday
        moment = (end - timedelta(days=day_index)).replace(hour=12, minute=0, second=0, microsecond=0)

        # retirements / launches happen *before* the snapshot of that day
        for code, _meal_data, retire_day in legacy:
            if day_index <= retire_day:
                state.pop(code, None)
        for code, meal, launch_day in launches:
            if day_index <= launch_day:
                state.setdefault(code, copy.deepcopy(meal))

        # a price change happens on its change_day and stays in effect afterwards
        for code, meal in state.items():
            if code in drifted:
                delta, change_day = drifted[code]
                meal["price_cents"] = max(300, base_price[code] + (delta if day_index >= change_day else 0))

        snap = copy.deepcopy(base_snapshot)
        snap["captured_at"] = moment.isoformat(timespec="seconds")
        snap["captured_ts"] = int(moment.timestamp())
        snap["daypart"], snap["daypart_label"] = infer_daypart(moment)
        snap["synthetic"] = True
        snap["note"] = "synthetic seed（示例历史，非真实采集）"
        snap["meals"] = copy.deepcopy(state)

        by_category: dict[str, list[str]] = {name: [] for name in categories_now if name}
        for code, meal in state.items():
            by_category.setdefault(meal.get("category") or "其他", []).append(code)
        snap["menu"] = {
            "meal_count": len(state),
            "categories": [{"name": name, "codes": sorted(codes_)} for name, codes_ in by_category.items()],
        }
        snap["checksum"] = "synthetic:" + hashlib.sha256(
            repr(sorted((code, meal["price_cents"]) for code, meal in state.items())).encode()
        ).hexdigest()[:32]
        series.append(snap)

    real = copy.deepcopy(base_snapshot)
    real["synthetic"] = False
    series.append(real)
    return series


__all__ = ["LAUNCH_ITEMS", "LEGACY_ITEMS", "generate_history"]
