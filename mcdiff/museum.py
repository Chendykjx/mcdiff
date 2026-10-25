"""The museum: every SKU that vanished from a menu gets a tombstone.

A menu has *dayparts*: a breakfast item disappears at 10:30 and comes back
tomorrow at 05:00 — that is not retirement.  So an SKU is only considered gone
when, for **every daypart it ever appeared in**, the newest snapshot of that
daypart no longer lists it.  Anything still listed in the latest snapshot of one
of its dayparts is alive.
"""

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
    last_daypart_label: str = ""
    dayparts: set[str] = field(default_factory=set)

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
        return template.format(daypart=self.last_daypart_label or "午后")

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
            "dayparts": sorted(self.dayparts),
            "epitaph": self.epitaph() if status == "gone" else "",
            "synthetic": self.synthetic,
        }


@dataclass
class Museum:
    store: dict[str, Any]
    generated_at: str
    items: list[ItemHistory]
    snapshot_count: int
    status: dict[str, str] = field(default_factory=dict)

    @property
    def gone(self) -> list[ItemHistory]:
        return [item for item in self.items if self.status.get(item.code) == "gone"]

    @property
    def alive(self) -> list[ItemHistory]:
        return [item for item in self.items if self.status.get(item.code) != "gone"]

    def to_dict(self) -> dict[str, Any]:
        gone = sorted(self.gone, key=lambda item: item.last_seen, reverse=True)
        alive = sorted(self.alive, key=lambda item: item.name)
        return {
            "store": self.store,
            "generated_at": self.generated_at,
            "snapshot_count": self.snapshot_count,
            "stats": {
                "tracked": len(self.items),
                "alive": len(alive),
                "gone": len(gone),
                "synthetic": any(item.synthetic for item in self.items),
            },
            "items": [item.to_dict(status="gone") for item in gone]
            + [item.to_dict(status="alive") for item in alive],
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


def _latest_by_daypart(series: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for snap in series:
        daypart = str(snap.get("daypart") or "unknown")
        previous = latest.get(daypart)
        if previous is None or str(snap.get("captured_at")) >= str(previous.get("captured_at")):
            latest[daypart] = snap
    return latest


def build_museum(snapshots: Iterable[Mapping[str, Any]], *, store: Mapping[str, Any] | None = None) -> Museum:
    series = sorted(snapshots, key=lambda snap: str(snap.get("captured_at")))
    if not series:
        raise ValueError("no snapshots")

    histories: dict[str, ItemHistory] = {}
    seen_in_daypart: dict[str, set[str]] = {}

    for snap in series:
        captured_at = str(snap.get("captured_at"))
        daypart = str(snap.get("daypart") or "unknown")
        synthetic = bool(snap.get("synthetic"))
        for code, meal in (snap.get("meals") or {}).items():
            code = str(code)
            price = int(meal.get("price_cents") or 0)
            seen_in_daypart.setdefault(code, set()).add(daypart)
            history = histories.get(code)
            if history is None:
                histories[code] = ItemHistory(
                    code=code,
                    name=meal.get("name", ""),
                    category=meal.get("category", ""),
                    tags=list(meal.get("tags") or []),
                    image=meal.get("image", ""),
                    first_seen=captured_at,
                    last_seen=captured_at,
                    prices=[(captured_at, price)],
                    synthetic=synthetic,
                    last_daypart_label=str(snap.get("daypart_label") or ""),
                    dayparts={daypart},
                )
            else:
                history.name = meal.get("name", history.name)
                history.category = meal.get("category", history.category)
                history.tags = list(meal.get("tags") or history.tags)
                history.last_seen = captured_at
                history.last_daypart_label = str(snap.get("daypart_label") or history.last_daypart_label)
                history.synthetic = history.synthetic or synthetic
                history.dayparts.add(daypart)
                if not history.prices or history.prices[-1][1] != price:
                    history.prices.append((captured_at, price))

    latest_by_daypart = _latest_by_daypart(series)
    status: dict[str, str] = {}
    for code, item in histories.items():
        alive = any(
            code in (latest_by_daypart[daypart].get("meals") or {})
            for daypart in seen_in_daypart.get(code, set())
            if daypart in latest_by_daypart
        )
        status[code] = "alive" if alive else "gone"

    store_info = dict(store or (series[-1].get("store") or {}))
    return Museum(
        store=store_info,
        generated_at=str(series[-1].get("captured_at")),
        items=list(histories.values()),
        snapshot_count=len(series),
        status=status,
    )


__all__ = ["EPITAPHS", "ItemHistory", "Museum", "build_museum"]
