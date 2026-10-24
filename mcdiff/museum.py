"""The museum: every SKU that vanished from a menu gets a tombstone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .snapshot import format_cents

TZ = ZoneInfo("Asia/Shanghai")

EPITAPHS = (
    "它最后一次出现在菜单上，是一个普通的{daypart}。",
    "没有公告，没有告别，只是某天它不在了。",
    "它曾经也是「今日推荐」里的一员。",
    "菜单轮换的名单里，又多了一个名字。",
    "如果你还记得它，它就没有真正下架。",
    "它把位置让给了下一位联名款。",
    "价格停在那一天，之后再也没有变过。",
)


@dataclass
class ItemHistory:
    code: str
    name: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    image: str = ""
    first_seen: str = ""
    last_seen: str = ""
    prices: list[tuple[str, int]] = field(default_factory=list)
    synthetic: bool = False

    @property
    def days_alive(self) -> int:
        try:
            first = datetime.fromisoformat(self.first_seen)
            last = datetime.fromisoformat(self.last_seen)
        except ValueError:
            return 1
        return max(1, (last.date() - first.date()).days + 1)

    @property
    def first_price(self) -> int | None:
        return self.prices[0][1] if self.prices else None

    @property
    def last_price(self) -> int | None:
        return self.prices[-1][1] if self.prices else None

    @property
    def price_changes(self) -> list[dict[str, Any]]:
        changes = []
        for (prev_ts, prev_price), (ts, price) in zip(self.prices, self.prices[1:]):
            if price != prev_price:
                changes.append({"at": ts, "from": prev_price, "to": price})
        return changes

    def epitaph(self) -> str:
        template = EPITAPHS[sum(ord(ch) for ch in self.code) % len(EPITAPHS)]
        return template.format(daypart="午后" if self.last_price else "傍晚")

    def to_dict(self, *, status: str) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "tags": list(self.tags),
            "image": self.image,
            "status": status,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "days_alive": self.days_alive,
            "first_price_cents": self.first_price,
            "last_price_cents": self.last_price,
            "price_changes": self.price_changes,
            "epitaph": self.epitaph() if status == "gone" else "",
            "synthetic": self.synthetic,
        }


@dataclass
class Museum:
    store: dict[str, Any]
    generated_at: str
    items: list[ItemHistory]
    snapshot_count: int

    @property
    def gone(self) -> list[ItemHistory]:
        return [item for item in self.items if item.code not in self._alive]

    @property
    def alive(self) -> list[ItemHistory]:
        return [item for item in self.items if item.code in self._alive]

    _alive: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        gone = sorted(self.gone, key=lambda item: item.last_seen, reverse=True)
        return {
            "store": self.store,
            "generated_at": self.generated_at,
            "snapshot_count": self.snapshot_count,
            "stats": {
                "tracked": len(self.items),
                "alive": len(self.alive),
                "gone": len(gone),
                "synthetic": any(item.synthetic for item in self.items),
            },
            "items": [item.to_dict(status="gone" if item.code not in self._alive else "alive") for item in (gone + self.alive)],
        }

    def render_markdown(self) -> str:
        gone = sorted(self.gone, key=lambda item: item.last_seen, reverse=True)
        lines = [
            "## 🪦 麦门考古：已下架的餐品",
            "",
            f"基于 {self.snapshot_count} 次快照，追踪 {len(self.items)} 个 SKU，其中 {len(gone)} 个已经消失。",
            "",
        ]
        if not gone:
            lines.append("_暂时没有发现下架的餐品——再多采集几天。_")
            return "\n".join(lines)
        lines += ["| 餐品 | 在架天数 | 最后价格 | 最后一次出现 | 墓志铭 |", "|---|---:|---:|---|---|"]
        for item in gone:
            lines.append(
                f"| {item.name} | {item.days_alive} 天 | {format_cents(item.last_price)} | "
                f"{item.last_seen[:10]} | {item.epitaph()} |"
            )
        return "\n".join(lines)


def build_museum(snapshots: Iterable[Mapping[str, Any]], *, store: Mapping[str, Any] | None = None) -> Museum:
    series = sorted(snapshots, key=lambda snap: str(snap.get("captured_at")))
    if not series:
        raise ValueError("no snapshots")

    histories: dict[str, ItemHistory] = {}
    alive: set[str] = set()
    synthetic_flag = False

    for snap in series:
        captured_at = str(snap.get("captured_at"))
        synthetic_flag = synthetic_flag or bool(snap.get("synthetic"))
        meals = snap.get("meals") or {}
        alive = set(meals)
        for code, meal in meals.items():
            code = str(code)
            price = int(meal.get("price_cents") or 0)
            history = histories.get(code)
            if history is None:
                history = ItemHistory(
                    code=code,
                    name=meal.get("name", ""),
                    category=meal.get("category", ""),
                    tags=list(meal.get("tags") or []),
                    image=meal.get("image", ""),
                    first_seen=captured_at,
                    last_seen=captured_at,
                    prices=[(captured_at, price)],
                    synthetic=bool(snap.get("synthetic")),
                )
                histories[code] = history
            else:
                history.name = meal.get("name", history.name)
                history.category = meal.get("category", history.category)
                history.tags = list(meal.get("tags") or history.tags)
                history.last_seen = captured_at
                if not history.prices or history.prices[-1][1] != price:
                    history.prices.append((captured_at, price))

    store_info = dict(store or (series[-1].get("store") or {}))
    return Museum(
        store=store_info,
        generated_at=str(series[-1].get("captured_at")),
        items=list(histories.values()),
        snapshot_count=len(series),
        _alive=alive,
    )


__all__ = ["EPITAPHS", "ItemHistory", "Museum", "build_museum"]
